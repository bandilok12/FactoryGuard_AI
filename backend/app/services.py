"""Domain services shared by routers and the simulator: sensor pipeline, predictions, OEE, alerts."""
import json
from datetime import datetime, date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as M
from .ml import inference
from .utils import now_local, shift_for, to_dict


# --------------------------------------------------------------------------- settings
DEFAULT_SETTINGS = {
    "auto_ticket_on_critical_anomaly": ("true", "Create a maintenance ticket automatically for critical anomalies"),
    "prediction_alert_threshold": ("0.6", "Failure probability that raises a predictive-maintenance alert"),
    "auto_ticket_on_prediction": ("true", "Create a predictive ticket when risk exceeds the threshold"),
    "plant_name": ("Vizag Pharma Packaging Unit-2", "Plant display name"),
    "oee_target": ("75", "Target OEE % shown on dashboards"),
    "defect_hold_threshold": ("0.6", "Defect probability above which a batch is put on hold"),
}


def get_setting(db: Session, key: str) -> str:
    s = db.get(M.Setting, key)
    return s.value if s else DEFAULT_SETTINGS.get(key, ("",))[0]


def setting_bool(db, key):
    return get_setting(db, key).lower() in ("1", "true", "yes", "on")


def next_ticket_no(db: Session) -> str:
    n = db.scalar(select(func.count(M.MaintenanceTicket.id))) or 0
    year = now_local().year
    while True:
        n += 1
        no = f"MT-{year}-{n:05d}"
        if not db.scalar(select(M.MaintenanceTicket.id).where(M.MaintenanceTicket.ticket_no == no)):
            return no


def add_alert(db, kind, severity, message, machine_id=None, part_id=None, dedupe_minutes=60):
    """Insert an alert unless an identical unacknowledged one exists recently."""
    since = now_local() - timedelta(minutes=dedupe_minutes)
    q = select(M.Alert).where(M.Alert.kind == kind, M.Alert.acknowledged == False,  # noqa: E712
                              M.Alert.created_at >= since)
    q = q.where(M.Alert.machine_id == machine_id) if machine_id else q
    q = q.where(M.Alert.part_id == part_id) if part_id else q
    if db.scalars(q).first():
        return None
    a = M.Alert(kind=kind, severity=severity, message=message[:500], machine_id=machine_id, part_id=part_id)
    db.add(a)
    return a


def open_ticket_exists(db, machine_id, sources=None):
    q = select(M.MaintenanceTicket.id).where(M.MaintenanceTicket.machine_id == machine_id,
                                             M.MaintenanceTicket.status.notin_(["resolved", "closed"]))
    if sources:
        q = q.where(M.MaintenanceTicket.source.in_(sources))
    return db.scalar(q) is not None


# --------------------------------------------------------------------------- sensor pipeline
def limits_of(machine: M.Machine) -> dict:
    return {"temp_max": machine.temp_max, "vibration_max": machine.vibration_max,
            "pressure_min": machine.pressure_min, "pressure_max": machine.pressure_max}


def ingest_reading(db: Session, machine: M.Machine, data: dict, source="manual", operator_id=None,
                   run_prediction=True, anomaly: dict | None = None, shifts=None) -> dict:
    """Store a reading, run anomaly detection (AI #2), update failure prediction (AI #1), raise alerts/tickets."""
    ts = data.get("recorded_at") or now_local()
    shifts = shifts if shifts is not None else db.scalars(select(M.Shift)).all()
    sh = shift_for(ts, shifts)
    res = anomaly or inference.detect_anomaly(data, limits_of(machine))
    r = M.SensorReading(machine_id=machine.id, shift_id=data.get("shift_id") or (sh.id if sh else None),
                        operator_id=operator_id, recorded_at=ts, source=source,
                        temperature=data["temperature"], vibration=data["vibration"], pressure=data["pressure"],
                        rpm=data["rpm"], torque=data["torque"], tool_wear=data["tool_wear"],
                        power_kw=data.get("power_kw"), humidity=data.get("humidity"),
                        is_anomaly=res["is_anomaly"], anomaly_score=res["score"],
                        anomaly_reason="; ".join(res["reasons"])[:255] if res["reasons"] else None)
    db.add(r)
    if run_prediction or res["is_anomaly"]:
        db.flush()
    ticket = None
    if res["is_anomaly"]:
        add_alert(db, "anomaly", res["severity"],
                  f"{machine.code} abnormal reading: {r.anomaly_reason}", machine_id=machine.id, dedupe_minutes=30)
        # debounce: a single transient spike never opens a ticket; it must persist (2 of the last 3 readings)
        recent_anoms = sum(x.is_anomaly for x in recent_readings(db, machine.id, 3))
        persistent = recent_anoms >= 2 or source == "manual"
        if res["severity"] == "critical" and persistent and setting_bool(db, "auto_ticket_on_critical_anomaly") \
                and not open_ticket_exists(db, machine.id):
            ticket = M.MaintenanceTicket(
                ticket_no=next_ticket_no(db), machine_id=machine.id,
                title=f"{machine.code}: AI anomaly — {res['reasons'][0][:120] if res['reasons'] else 'abnormal readings'}",
                description="Auto-created by FactoryGuard anomaly detector.\nReasons: " + "; ".join(res["reasons"]),
                category=_category_from_reasons(res["reasons"]), priority="high" if machine.criticality != "A" else "critical",
                ticket_type="predictive", source="ai_anomaly")
            db.add(ticket)
            db.flush()
            db.add(M.TicketActivity(ticket_id=ticket.id, kind="status", message="Ticket auto-created from critical anomaly"))
    pred = None
    if run_prediction:
        pred = predict_for_machine(db, machine, store=True)
    return {"reading": to_dict(r), "anomaly": res, "prediction": pred,
            "auto_ticket": ticket.ticket_no if ticket else None}


def _category_from_reasons(reasons):
    t = " ".join(reasons).lower()
    if "pressure" in t:
        return "pneumatic"
    if "temperature" in t:
        return "electrical"
    return "mechanical"


def recent_readings(db, machine_id, n=20):
    rows = db.scalars(select(M.SensorReading).where(M.SensorReading.machine_id == machine_id)
                      .order_by(M.SensorReading.recorded_at.desc()).limit(n)).all()
    return list(reversed(rows))


def predict_for_machine(db: Session, machine: M.Machine, store=True) -> dict | None:
    rows = recent_readings(db, machine.id, 20)
    if not rows:
        return None
    feats = inference.build_failure_features([to_dict(r) for r in rows])
    p = inference.predict_failure(feats)
    machine.failure_risk = p["probability"]
    machine.health_score = round(max(0.0, 100 * (1 - p["probability"]) - 5 * sum(r.is_anomaly for r in rows[-10:])), 1)
    if store:
        last = db.scalars(select(M.FailurePrediction).where(M.FailurePrediction.machine_id == machine.id)
                          .order_by(M.FailurePrediction.created_at.desc()).limit(1)).first()
        # keep the table compact: store at most one prediction / 10 min unless risk level changes
        if not last or last.risk_level != p["risk_level"] or (now_local() - last.created_at) > timedelta(minutes=10):
            db.add(M.FailurePrediction(machine_id=machine.id, probability=p["probability"],
                                       risk_level=p["risk_level"], predicted_mode=p["predicted_mode"],
                                       top_factors=json.dumps(p["top_factors"])))
        thr = float(get_setting(db, "prediction_alert_threshold") or 0.6)
        if p["probability"] >= thr:
            add_alert(db, "prediction", "critical" if p["probability"] >= 0.75 else "warning",
                      f"{machine.code} failure risk {p['probability']:.0%} — {p['mode_description']}"
                      + (f", est. {p['est_hours_to_failure']} h" if p["est_hours_to_failure"] else ""),
                      machine_id=machine.id, dedupe_minutes=120)
            if setting_bool(db, "auto_ticket_on_prediction") and not open_ticket_exists(db, machine.id):
                t = M.MaintenanceTicket(
                    ticket_no=next_ticket_no(db), machine_id=machine.id,
                    title=f"{machine.code}: Predictive — {p['mode_description']} (risk {p['probability']:.0%})",
                    description="Auto-created by FactoryGuard failure-prediction model.\nTop factors: " +
                                ", ".join(f"{f['label']} {f['direction']} ({f['value']})" for f in p["top_factors"]),
                    category={"pneumatic": "pneumatic", "overheat": "electrical", "power": "electrical"}
                    .get(p["predicted_mode"], "mechanical"),
                    priority="critical" if p["probability"] >= 0.75 else "high",
                    ticket_type="predictive", source="ai_prediction")
                db.add(t)
                db.flush()
                db.add(M.TicketActivity(ticket_id=t.id, kind="status", message="Ticket auto-created from failure prediction"))
    return p


# --------------------------------------------------------------------------- spares
def check_reorder(db: Session, part: M.SparePart):
    if part.quantity <= part.reorder_level:
        sev = "critical" if part.quantity == 0 else "warning"
        add_alert(db, "reorder", sev,
                  f"Reorder {part.part_no} ({part.name}): stock {part.quantity} ≤ level {part.reorder_level}. "
                  f"Suggested PO qty {part.reorder_qty} from {part.supplier or 'supplier'} (lead {part.lead_time_days} d)",
                  part_id=part.id, dedupe_minutes=24 * 60)


# --------------------------------------------------------------------------- OEE & analytics
def oee_from_logs(logs) -> dict:
    planned = sum(l.planned_time_min for l in logs)
    run = sum(l.run_time_min for l in logs)
    produced = sum(l.produced_qty for l in logs)
    rejected = sum(l.rejected_qty for l in logs)
    ideal = sum(l.ideal_cycle_sec * l.produced_qty for l in logs)
    a = run / planned if planned else 0
    p = min(1.0, ideal / (run * 60)) if run else 0
    q = (produced - rejected) / produced if produced else 0
    return {"availability": round(a * 100, 1), "performance": round(p * 100, 1), "quality": round(q * 100, 1),
            "oee": round(a * p * q * 100, 1), "planned_min": round(planned), "run_min": round(run),
            "produced": produced, "rejected": rejected, "good": produced - rejected}


def logs_between(db, start: date, end: date, machine_id=None, shift_id=None):
    q = select(M.ProductionLog).where(M.ProductionLog.log_date >= start, M.ProductionLog.log_date <= end)
    if machine_id:
        q = q.where(M.ProductionLog.machine_id == machine_id)
    if shift_id:
        q = q.where(M.ProductionLog.shift_id == shift_id)
    return db.scalars(q).all()


def shift_kpis(db: Session, d: date, shift_id: int | None) -> dict:
    """All numbers that feed the GenAI shift report (computed, never invented)."""
    shift = db.get(M.Shift, shift_id) if shift_id else None
    logs = logs_between(db, d, d, shift_id=shift_id)
    o = oee_from_logs(logs)
    planned_units = sum(l.planned_qty for l in logs)
    # time window
    if shift:
        from .utils import _t
        start = datetime.combine(d, _t(shift.start_time))
        end = datetime.combine(d if _t(shift.end_time) > _t(shift.start_time) else d + timedelta(days=1),
                               _t(shift.end_time))
    else:
        start, end = datetime.combine(d, datetime.min.time()), datetime.combine(d + timedelta(days=1), datetime.min.time())
    dts = db.scalars(select(M.DowntimeEvent).where(M.DowntimeEvent.start_time >= start,
                                                    M.DowntimeEvent.start_time < end)).all()
    by_reason, by_machine = {}, {}
    for e in dts:
        mins = e.duration_min or ((min(now_local(), end) - e.start_time).total_seconds() / 60)
        by_reason[e.reason_category] = by_reason.get(e.reason_category, 0) + mins
        by_machine[e.machine.code] = by_machine.get(e.machine.code, 0) + mins
    insp = db.scalars(select(M.QualityInspection).where(M.QualityInspection.inspected_at >= start,
                                                         M.QualityInspection.inspected_at < end)).all()
    by_type = {}
    for i in insp:
        if i.defects_found and i.defect_type and i.defect_type != "none":
            by_type[i.defect_type] = by_type.get(i.defect_type, 0) + i.defects_found
    samples = sum(i.sample_size for i in insp)
    defects = sum(i.defects_found for i in insp)
    anomalies = db.scalar(select(func.count(M.SensorReading.id)).where(
        M.SensorReading.is_anomaly == True, M.SensorReading.recorded_at >= start,  # noqa: E712
        M.SensorReading.recorded_at < end)) or 0
    open_t = db.scalar(select(func.count(M.MaintenanceTicket.id)).where(
        M.MaintenanceTicket.status.notin_(["resolved", "closed"]))) or 0
    risky = db.scalars(select(M.Machine).where(M.Machine.failure_risk >= 0.4)
                       .order_by(M.Machine.failure_risk.desc()).limit(5)).all()
    modes = {}
    for m in risky:
        fp = db.scalars(select(M.FailurePrediction).where(M.FailurePrediction.machine_id == m.id)
                        .order_by(M.FailurePrediction.created_at.desc()).limit(1)).first()
        modes[m.id] = fp.predicted_mode if fp else None
    low = db.scalars(select(M.SparePart).where(M.SparePart.quantity <= M.SparePart.reorder_level)).all()
    # per-operator production
    ops = {}
    for l in logs:
        k = l.operator.full_name if l.operator else "Unassigned"
        ops.setdefault(k, {"operator": k, "produced": 0, "rejected": 0})
        ops[k]["produced"] += l.produced_qty
        ops[k]["rejected"] += l.rejected_qty
    return {
        "date": d.isoformat(), "shift": f"Shift {shift.code} ({shift.start_time}-{shift.end_time})" if shift else "All shifts",
        "plant": get_setting(db, "plant_name"),
        "oee": {k: o[k] for k in ("oee", "availability", "performance", "quality")},
        "production": {"planned": planned_units, "produced": o["produced"], "rejected": o["rejected"],
                       "attainment": round(o["produced"] / planned_units * 100, 1) if planned_units else 0,
                       "reject_rate": round(o["rejected"] / o["produced"] * 100, 2) if o["produced"] else 0,
                       "logs": len(logs)},
        "operators": sorted(ops.values(), key=lambda x: -x["produced"])[:8],
        "downtime": {"total_minutes": round(sum(by_reason.values())), "events": len(dts),
                     "by_reason": [{"reason": k, "minutes": round(v)} for k, v in sorted(by_reason.items(), key=lambda x: -x[1])],
                     "by_machine": [{"machine": k, "minutes": round(v)} for k, v in sorted(by_machine.items(), key=lambda x: -x[1])][:5]},
        "quality": {"inspections": len(insp), "failed_batches": sum(i.result == "fail" for i in insp),
                    "rework_batches": sum(i.result == "rework" for i in insp),
                    "defect_rate": round(defects / samples * 100, 2) if samples else 0,
                    "by_type": [{"type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])]},
        "maintenance": {"open_tickets": open_t, "anomalies": anomalies,
                        "high_risk_machines": [{"code": m.code, "risk": round(m.failure_risk, 2), "mode": modes.get(m.id)}
                                               for m in risky]},
        "spares_low": [{"part_no": p.part_no, "qty": p.quantity, "reorder_level": p.reorder_level} for p in low],
    }
