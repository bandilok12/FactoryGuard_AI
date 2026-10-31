"""Machine registry with specifications, health and full maintenance history."""
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models as M, schemas as S
from ..database import get_db
from ..security import get_current_user, require_roles, audit, MAINT
from ..utils import to_dict, now_local
from .common import get_or_404

router = APIRouter(prefix="/machines", tags=["Machines"])


def machine_out(m: M.Machine, db: Session | None = None, detail=False):
    d = to_dict(m)
    d["specs"] = json.loads(m.specs) if m.specs else {}
    if m.last_pm_date:
        due = m.last_pm_date + timedelta(days=m.pm_interval_days)
        d["next_pm_date"] = due.isoformat()
        d["pm_overdue"] = due < now_local().date()
    return d


@router.get("")
def list_machines(status: str | None = None, machine_type: str | None = None, line: str | None = None,
                  q: str | None = None, db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    stmt = select(M.Machine).order_by(M.Machine.code)
    if status:
        stmt = stmt.where(M.Machine.status == status)
    if machine_type:
        stmt = stmt.where(M.Machine.machine_type == machine_type)
    if line:
        stmt = stmt.where(M.Machine.line == line)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(M.Machine.code.ilike(like) | M.Machine.name.ilike(like))
    open_counts = dict(db.execute(select(M.MaintenanceTicket.machine_id, func.count())
                                  .where(M.MaintenanceTicket.status.notin_(["resolved", "closed"]))
                                  .group_by(M.MaintenanceTicket.machine_id)).all())
    out = []
    for m in db.scalars(stmt):
        d = machine_out(m)
        d["open_tickets"] = open_counts.get(m.id, 0)
        out.append(d)
    return out


@router.get("/types")
def machine_types(db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    return sorted(db.scalars(select(M.Machine.machine_type).distinct()).all())


@router.get("/{machine_id}")
def get_machine(machine_id: int, db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    m = get_or_404(db, M.Machine, machine_id, "Machine")
    d = machine_out(m)
    tickets = db.scalars(select(M.MaintenanceTicket).where(M.MaintenanceTicket.machine_id == m.id)
                         .order_by(M.MaintenanceTicket.created_at.desc()).limit(50)).all()
    d["maintenance_history"] = [{
        **{k: v for k, v in to_dict(t).items() if k not in ("ai_triage",)},
        "assigned_to": t.assigned_to.full_name if t.assigned_to else None,
        "parts": [{"part_no": u.part.part_no, "name": u.part.name, "qty": u.quantity} for u in t.parts_used],
    } for t in tickets]
    dts = db.scalars(select(M.DowntimeEvent).where(M.DowntimeEvent.machine_id == m.id)
                     .order_by(M.DowntimeEvent.start_time.desc()).limit(30)).all()
    d["downtime_history"] = [to_dict(e) for e in dts]
    d["downtime_minutes_30d"] = round(db.scalar(select(func.coalesce(func.sum(M.DowntimeEvent.duration_min), 0))
                                                .where(M.DowntimeEvent.machine_id == m.id,
                                                       M.DowntimeEvent.start_time >= now_local() - timedelta(days=30))) or 0)
    preds = db.scalars(select(M.FailurePrediction).where(M.FailurePrediction.machine_id == m.id)
                       .order_by(M.FailurePrediction.created_at.desc()).limit(1)).first()
    d["latest_prediction"] = {**to_dict(preds), "top_factors": json.loads(preds.top_factors or "[]")} if preds else None
    mtbf_n = sum(1 for t in tickets if t.ticket_type == "breakdown")
    rep = [((t.resolved_at - t.created_at).total_seconds() / 3600) for t in tickets if t.resolved_at]
    d["reliability"] = {"breakdowns_logged": mtbf_n,
                        "mttr_hours": round(sum(rep) / len(rep), 2) if rep else None}
    return d


@router.post("", status_code=201)
def create_machine(body: S.MachineIn, db: Session = Depends(get_db), admin: M.User = Depends(require_roles("admin"))):
    data = body.model_dump()
    data["specs"] = json.dumps(data.get("specs") or {})
    data["code"] = data["code"].upper()
    m = M.Machine(**data)
    db.add(m)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Machine code already exists")
    audit(db, admin, "create", "machine", m.id, {"code": m.code})
    db.commit()
    return machine_out(m)


@router.patch("/{machine_id}")
def update_machine(machine_id: int, body: S.MachineUpdate, db: Session = Depends(get_db),
                   admin: M.User = Depends(require_roles("admin"))):
    m = get_or_404(db, M.Machine, machine_id, "Machine")
    data = body.model_dump(exclude_unset=True)
    if "specs" in data:
        data["specs"] = json.dumps(data["specs"] or {})
    for k, v in data.items():
        setattr(m, k, v)
    if m.pressure_min >= m.pressure_max:
        db.rollback()
        raise HTTPException(422, "pressure_min must be less than pressure_max")
    audit(db, admin, "update", "machine", m.id, list(data.keys()))
    db.commit()
    return machine_out(m)


@router.patch("/{machine_id}/status", summary="Change running status (technician/plant head/admin)")
def set_status(machine_id: int, body: S.MachineStatusIn, db: Session = Depends(get_db),
               user: M.User = Depends(require_roles(*MAINT))):
    m = get_or_404(db, M.Machine, machine_id, "Machine")
    old = m.status
    m.status = body.status
    audit(db, user, "status", "machine", m.id, f"{old}->{body.status}")
    db.commit()
    return machine_out(m)


@router.delete("/{machine_id}")
def delete_machine(machine_id: int, db: Session = Depends(get_db), admin: M.User = Depends(require_roles("admin"))):
    m = get_or_404(db, M.Machine, machine_id, "Machine")
    if db.scalar(select(func.count(M.ProductionLog.id)).where(M.ProductionLog.machine_id == m.id)):
        raise HTTPException(409, "Machine has production history; set status instead of deleting (GMP data integrity)")
    db.delete(m)
    audit(db, admin, "delete", "machine", machine_id)
    db.commit()
    return {"ok": True}
