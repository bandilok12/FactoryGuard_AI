"""Dashboards, OEE analytics and alerts."""
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .. import models as M, services
from ..database import get_db
from ..security import get_current_user, require_roles, MANAGERS, ANY
from ..utils import to_dict, now_local
from .common import get_or_404

router = APIRouter()


@router.get("/dashboard/overview", tags=["Dashboard"], summary="Plant-wide KPIs: production, OEE, downtime, quality")
def overview(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db),
             _: M.User = Depends(require_roles(*MANAGERS))):
    today = now_local().date()
    start = today - timedelta(days=days - 1)
    logs = services.logs_between(db, start, today)
    o = services.oee_from_logs(logs)
    today_o = services.oee_from_logs([l for l in logs if l.log_date == today])

    # OEE trend by day and by shift
    by_day, by_shift, by_machine = defaultdict(list), defaultdict(list), defaultdict(list)
    for l in logs:
        by_day[l.log_date].append(l)
        by_shift[l.shift.code].append(l)
        by_machine[l.machine.code].append(l)
    trend = [{"date": d.isoformat(), **services.oee_from_logs(v)} for d, v in sorted(by_day.items())]
    shifts = [{"shift": k, **services.oee_from_logs(v)} for k, v in sorted(by_shift.items())]
    machine_oee = sorted(({"machine": k, **services.oee_from_logs(v)} for k, v in by_machine.items()),
                         key=lambda x: x["oee"])

    # downtime
    s_dt = datetime.combine(start, datetime.min.time())
    dts = db.scalars(select(M.DowntimeEvent).where(M.DowntimeEvent.start_time >= s_dt)).all()
    now = now_local()
    reason, mach, planned = defaultdict(float), defaultdict(float), 0.0
    for e in dts:
        mins = e.duration_min if e.duration_min is not None else (now - e.start_time).total_seconds() / 60
        reason[e.reason_category] += mins
        mach[e.machine.code] += mins
        planned += mins if e.is_planned else 0
    total_dt = sum(reason.values())
    pareto, cum = [], 0
    for k, v in sorted(reason.items(), key=lambda x: -x[1]):
        cum += v
        pareto.append({"reason": k, "minutes": round(v), "cum_pct": round(cum / total_dt * 100, 1) if total_dt else 0})

    # quality
    insp = db.scalars(select(M.QualityInspection).where(M.QualityInspection.inspected_at >= s_dt)).all()
    qtype, qmach = defaultdict(int), defaultdict(int)
    for i in insp:
        if i.defects_found and i.defect_type != "none":
            qtype[i.defect_type] += i.defects_found
            qmach[i.machine.code] += i.defects_found
    samples = sum(i.sample_size for i in insp)

    # maintenance
    tickets = db.scalars(select(M.MaintenanceTicket).where(M.MaintenanceTicket.created_at >= s_dt)).all()
    open_t = db.scalars(select(M.MaintenanceTicket).where(M.MaintenanceTicket.status.notin_(["resolved", "closed"]))).all()
    rep = [(t.resolved_at - t.created_at).total_seconds() / 3600 for t in tickets if t.resolved_at]
    breakdowns = [e for e in dts if e.reason_category == "breakdown"]
    n_machines = db.scalar(select(func.count(M.Machine.id))) or 1
    op_hours = n_machines * days * 24 - total_dt / 60
    machines = db.scalars(select(M.Machine)).all()
    status_counts = defaultdict(int)
    for m in machines:
        status_counts[m.status] += 1
    risky = sorted(machines, key=lambda m: -m.failure_risk)[:8]
    low_parts = db.scalars(select(M.SparePart).where(M.SparePart.quantity <= M.SparePart.reorder_level)).all()
    alerts = db.scalars(select(M.Alert).where(M.Alert.acknowledged == False)  # noqa: E712
                        .order_by(M.Alert.created_at.desc()).limit(10)).all()
    anomalies_24h = db.scalar(select(func.count(M.SensorReading.id)).where(
        M.SensorReading.is_anomaly == True, M.SensorReading.recorded_at >= now - timedelta(hours=24))) or 0  # noqa

    return {
        "period": {"start": start.isoformat(), "end": today.isoformat(), "days": days},
        "plant_name": services.get_setting(db, "plant_name"),
        "oee_target": float(services.get_setting(db, "oee_target") or 75),
        "kpis": {
            "oee": o["oee"], "availability": o["availability"], "performance": o["performance"], "quality": o["quality"],
            "today_oee": today_o["oee"] if today_o["produced"] else None, "produced": o["produced"], "good": o["good"], "rejected": o["rejected"],
            "downtime_hours": round(total_dt / 60, 1), "unplanned_downtime_hours": round((total_dt - planned) / 60, 1),
            "open_tickets": len(open_t), "critical_tickets": sum(t.priority == "critical" for t in open_t),
            "mttr_hours": round(sum(rep) / len(rep), 2) if rep else None,
            "mtbf_hours": round(op_hours / len(breakdowns), 1) if breakdowns else None,
            "defect_rate": round(sum(i.defects_found for i in insp) / samples * 100, 2) if samples else 0,
            "failed_batches": sum(i.result == "fail" for i in insp), "batches_inspected": len(insp),
            "anomalies_24h": anomalies_24h, "machines_total": len(machines),
            "machines_running": status_counts.get("running", 0), "high_risk_machines": sum(m.failure_risk >= 0.5 for m in machines),
            "spares_below_reorder": len(low_parts),
        },
        "oee_trend": trend, "shift_comparison": shifts, "worst_oee_machines": machine_oee[:10],
        "downtime_pareto": pareto,
        "downtime_by_machine": [{"machine": k, "minutes": round(v)} for k, v in sorted(mach.items(), key=lambda x: -x[1])[:10]],
        "defects_by_type": [{"type": k, "count": v} for k, v in sorted(qtype.items(), key=lambda x: -x[1])],
        "defects_by_machine": [{"machine": k, "count": v} for k, v in sorted(qmach.items(), key=lambda x: -x[1])[:10]],
        "machine_status": dict(status_counts),
        "tickets_by_priority": {p: sum(t.priority == p for t in open_t) for p in ("critical", "high", "medium", "low")},
        "high_risk_machines": [{"id": m.id, "code": m.code, "name": m.name, "risk": m.failure_risk,
                                "health": m.health_score, "status": m.status} for m in risky],
        "low_stock_parts": [{"part_no": p.part_no, "name": p.name, "quantity": p.quantity,
                             "reorder_level": p.reorder_level} for p in low_parts],
        "recent_alerts": [{**to_dict(a), "machine_code": a.machine.code if a.machine else None} for a in alerts],
    }


@router.get("/dashboard/my", tags=["Dashboard"], summary="Role home: operator / technician view")
def my_dashboard(db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    today = now_local().date()
    roster = db.scalar(select(M.ShiftAssignment).where(M.ShiftAssignment.user_id == user.id,
                                                       M.ShiftAssignment.work_date == today))
    out = {"user": to_dict(user), "today": today.isoformat(),
           "assignment": {"shift": roster.shift.code, "shift_name": roster.shift.name,
                          "window": f"{roster.shift.start_time}-{roster.shift.end_time}",
                          "machine_id": roster.machine_id,
                          "machine": roster.machine.code if roster.machine else None} if roster else None}
    if user.role == "operator":
        logs = db.scalars(select(M.ProductionLog).where(M.ProductionLog.operator_id == user.id,
                                                        M.ProductionLog.log_date == today)).all()
        out["today_production"] = services.oee_from_logs(logs)
        out["logs_today"] = len(logs)
        out["my_open_issues"] = db.scalar(select(func.count(M.MaintenanceTicket.id)).where(
            M.MaintenanceTicket.reported_by_id == user.id,
            M.MaintenanceTicket.status.notin_(["resolved", "closed"])))
    if user.role == "technician":
        mine = db.scalars(select(M.MaintenanceTicket).where(M.MaintenanceTicket.assigned_to_id == user.id,
                                                            M.MaintenanceTicket.status.notin_(["resolved", "closed"]))).all()
        unassigned = db.scalar(select(func.count(M.MaintenanceTicket.id)).where(
            M.MaintenanceTicket.status == "open", M.MaintenanceTicket.assigned_to_id.is_(None)))
        out["my_tickets"] = len(mine)
        out["unassigned_tickets"] = unassigned
        out["resolved_30d"] = db.scalar(select(func.count(M.MaintenanceTicket.id)).where(
            M.MaintenanceTicket.assigned_to_id == user.id, M.MaintenanceTicket.resolved_at >= now_local() - timedelta(days=30)))
    risky = db.scalars(select(M.Machine).where(M.Machine.failure_risk >= 0.4).order_by(M.Machine.failure_risk.desc()).limit(6)).all()
    out["high_risk_machines"] = [{"id": m.id, "code": m.code, "risk": m.failure_risk, "status": m.status} for m in risky]
    out["alerts"] = [{**to_dict(a), "machine_code": a.machine.code if a.machine else None} for a in db.scalars(
        select(M.Alert).where(M.Alert.acknowledged == False).order_by(M.Alert.created_at.desc()).limit(6))]  # noqa
    return out


@router.get("/analytics/oee", tags=["Dashboard"], summary="OEE = Availability × Performance × Quality, grouped")
def oee(group_by: str = Query("machine", pattern="^(machine|shift|day|line|machine_type|operator)$"),
        start: date | None = None, end: date | None = None, machine_id: int | None = None,
        db: Session = Depends(get_db), _: M.User = Depends(require_roles(*MANAGERS))):
    end = end or now_local().date()
    start = start or end - timedelta(days=6)
    if start > end:
        raise HTTPException(422, "start must be before end")
    logs = services.logs_between(db, start, end, machine_id=machine_id)
    key = {"machine": lambda l: l.machine.code, "shift": lambda l: l.shift.code, "day": lambda l: l.log_date.isoformat(),
           "line": lambda l: l.machine.line or "-", "machine_type": lambda l: l.machine.machine_type,
           "operator": lambda l: l.operator.full_name if l.operator else "-"}[group_by]
    g = defaultdict(list)
    for l in logs:
        g[key(l)].append(l)
    return {"group_by": group_by, "start": start.isoformat(), "end": end.isoformat(),
            "overall": services.oee_from_logs(logs),
            "formula": "OEE = Availability (run/planned time) × Performance (ideal cycle × output / run time) × Quality (good/total)",
            "rows": sorted(({"group": k, **services.oee_from_logs(v)} for k, v in g.items()), key=lambda r: r["group"])}


# =========================================================================== ALERTS
@router.get("/alerts", tags=["Alerts"])
def list_alerts(kind: str | None = None, unacknowledged: bool = True, limit: int = Query(50, le=500),
                db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    q = select(M.Alert).order_by(M.Alert.created_at.desc())
    if kind:
        q = q.where(M.Alert.kind == kind)
    if unacknowledged:
        q = q.where(M.Alert.acknowledged == False)  # noqa: E712
    return [{**to_dict(a), "machine_code": a.machine.code if a.machine else None} for a in db.scalars(q.limit(limit))]


@router.get("/alerts/count", tags=["Alerts"])
def alert_count(db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    rows = db.execute(select(M.Alert.severity, func.count()).where(M.Alert.acknowledged == False)  # noqa
                      .group_by(M.Alert.severity)).all()
    d = dict(rows)
    return {"total": sum(d.values()), **d}


@router.post("/alerts/{alert_id}/ack", tags=["Alerts"])
def ack(alert_id: int, db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    a = get_or_404(db, M.Alert, alert_id, "Alert")
    a.acknowledged, a.acknowledged_by_id = True, user.id
    db.commit()
    return {"ok": True}


@router.post("/alerts/ack-all", tags=["Alerts"])
def ack_all(kind: str | None = None, db: Session = Depends(get_db), user: M.User = Depends(require_roles(*MANAGERS, "technician"))):
    q = select(M.Alert).where(M.Alert.acknowledged == False)  # noqa: E712
    if kind:
        q = q.where(M.Alert.kind == kind)
    n = 0
    for a in db.scalars(q):
        a.acknowledged, a.acknowledged_by_id = True, user.id
        n += 1
    db.commit()
    return {"acknowledged": n}
