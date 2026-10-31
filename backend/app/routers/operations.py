"""Shift-wise production logs, sensor readings, and downtime records."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .. import models as M, schemas as S, services
from ..database import get_db
from ..security import get_current_user, require_roles, audit, ANY, FLOOR
from ..utils import to_dict, now_local
from .common import get_or_404, Page

router = APIRouter()


# =========================================================================== PRODUCTION
def prod_out(l: M.ProductionLog):
    d = to_dict(l)
    d.update(machine_code=l.machine.code, shift_code=l.shift.code,
             operator_name=l.operator.full_name if l.operator else None)
    d["oee"] = services.oee_from_logs([l])
    return d


@router.get("/production", tags=["Production"])
def list_production(start: date | None = None, end: date | None = None, machine_id: int | None = None,
                    shift_id: int | None = None, batch_no: str | None = None, page: Page = Depends(),
                    db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    q = select(M.ProductionLog).order_by(M.ProductionLog.log_date.desc(), M.ProductionLog.id.desc())
    if start:
        q = q.where(M.ProductionLog.log_date >= start)
    if end:
        q = q.where(M.ProductionLog.log_date <= end)
    if machine_id:
        q = q.where(M.ProductionLog.machine_id == machine_id)
    if shift_id:
        q = q.where(M.ProductionLog.shift_id == shift_id)
    if batch_no:
        q = q.where(M.ProductionLog.batch_no.ilike(f"%{batch_no}%"))
    if user.role == "operator":
        q = q.where(M.ProductionLog.operator_id == user.id)
    return [prod_out(l) for l in db.scalars(q.limit(page.limit).offset(page.offset))]


@router.post("/production", tags=["Production"], status_code=201)
def create_production(body: S.ProductionIn, db: Session = Depends(get_db), user: M.User = Depends(require_roles(*FLOOR))):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    get_or_404(db, M.Shift, body.shift_id, "Shift")
    dup = db.scalar(select(M.ProductionLog.id).where(M.ProductionLog.machine_id == body.machine_id,
                                                     M.ProductionLog.shift_id == body.shift_id,
                                                     M.ProductionLog.log_date == body.log_date,
                                                     M.ProductionLog.batch_no == body.batch_no))
    if dup:
        raise HTTPException(409, "This batch is already logged for the machine/shift/date")
    l = M.ProductionLog(**body.model_dump(), operator_id=user.id)
    db.add(l)
    db.flush()
    audit(db, user, "create", "production", l.id, {"batch": l.batch_no, "machine": m.code})
    db.commit()
    db.refresh(l)
    return prod_out(l)


@router.put("/production/{log_id}", tags=["Production"])
def update_production(log_id: int, body: S.ProductionIn, db: Session = Depends(get_db),
                      user: M.User = Depends(require_roles(*FLOOR))):
    l = get_or_404(db, M.ProductionLog, log_id, "Production log")
    if user.role == "operator":
        if l.operator_id != user.id:
            raise HTTPException(403, "Operators can only edit their own logs")
        if now_local() - l.created_at > timedelta(hours=24):
            raise HTTPException(403, "Logs are locked 24 h after entry (data integrity). Ask the plant head.")
    before = to_dict(l)
    for k, v in body.model_dump().items():
        setattr(l, k, v)
    audit(db, user, "update", "production", l.id, {"before": before})
    db.commit()
    return prod_out(l)


@router.delete("/production/{log_id}", tags=["Production"])
def delete_production(log_id: int, db: Session = Depends(get_db), user: M.User = Depends(require_roles("admin"))):
    l = get_or_404(db, M.ProductionLog, log_id, "Production log")
    audit(db, user, "delete", "production", l.id, to_dict(l))
    db.delete(l)
    db.commit()
    return {"ok": True}


# =========================================================================== SENSOR READINGS
@router.get("/sensors", tags=["Sensor Readings"])
def list_readings(machine_id: int | None = None, anomalies_only: bool = False, hours: int | None = None,
                  source: str | None = None, page: Page = Depends(), db: Session = Depends(get_db),
                  _: M.User = Depends(get_current_user)):
    q = select(M.SensorReading).order_by(M.SensorReading.recorded_at.desc())
    if machine_id:
        q = q.where(M.SensorReading.machine_id == machine_id)
    if anomalies_only:
        q = q.where(M.SensorReading.is_anomaly == True)  # noqa: E712
    if hours:
        q = q.where(M.SensorReading.recorded_at >= now_local() - timedelta(hours=hours))
    if source:
        q = q.where(M.SensorReading.source == source)
    return [{**to_dict(r), "machine_code": r.machine.code} for r in db.scalars(q.limit(page.limit).offset(page.offset))]


@router.get("/sensors/latest", tags=["Sensor Readings"], summary="Latest reading for every machine (live board)")
def latest(db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    sub = (select(M.SensorReading.machine_id, func.max(M.SensorReading.id).label("mid"))
           .group_by(M.SensorReading.machine_id).subquery())
    rows = db.scalars(select(M.SensorReading).join(sub, M.SensorReading.id == sub.c.mid)).all()
    return [{**to_dict(r), "machine_code": r.machine.code, "machine_status": r.machine.status,
             "failure_risk": r.machine.failure_risk, "health_score": r.machine.health_score} for r in rows]


@router.post("/sensors", tags=["Sensor Readings"], status_code=201,
             summary="Log a sensor reading → runs anomaly detection + failure prediction")
def create_reading(body: S.SensorIn, db: Session = Depends(get_db),
                   user: M.User = Depends(require_roles("admin", "operator", "technician"))):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    if body.recorded_at and body.recorded_at > now_local() + timedelta(minutes=5):
        raise HTTPException(422, "recorded_at cannot be in the future")
    res = services.ingest_reading(db, m, body.model_dump(), source="manual", operator_id=user.id)
    audit(db, user, "create", "sensor_reading", res["reading"]["id"])
    db.commit()
    return res


# =========================================================================== DOWNTIME
def dt_out(e: M.DowntimeEvent):
    d = to_dict(e)
    d["machine_code"] = e.machine.code
    if e.duration_min is None:
        d["ongoing_minutes"] = round((now_local() - e.start_time).total_seconds() / 60, 1)
    return d


@router.get("/downtime", tags=["Downtime"])
def list_downtime(machine_id: int | None = None, reason: str | None = None, start: date | None = None,
                  end: date | None = None, ongoing: bool | None = None, page: Page = Depends(),
                  db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    q = select(M.DowntimeEvent).order_by(M.DowntimeEvent.start_time.desc())
    if machine_id:
        q = q.where(M.DowntimeEvent.machine_id == machine_id)
    if reason:
        q = q.where(M.DowntimeEvent.reason_category == reason)
    if start:
        q = q.where(M.DowntimeEvent.start_time >= datetime.combine(start, datetime.min.time()))
    if end:
        q = q.where(M.DowntimeEvent.start_time < datetime.combine(end + timedelta(days=1), datetime.min.time()))
    if ongoing is True:
        q = q.where(M.DowntimeEvent.end_time.is_(None))
    return [dt_out(e) for e in db.scalars(q.limit(page.limit).offset(page.offset))]


@router.post("/downtime", tags=["Downtime"], status_code=201)
def create_downtime(body: S.DowntimeIn, db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    if body.ticket_id:
        get_or_404(db, M.MaintenanceTicket, body.ticket_id, "Ticket")
    if body.start_time > now_local() + timedelta(minutes=5):
        raise HTTPException(422, "start_time cannot be in the future")
    if body.end_time is None and db.scalar(select(M.DowntimeEvent.id).where(
            M.DowntimeEvent.machine_id == m.id, M.DowntimeEvent.end_time.is_(None))):
        raise HTTPException(409, f"{m.code} already has an open downtime event — close it first")
    e = M.DowntimeEvent(**body.model_dump(), reported_by_id=user.id)
    if e.end_time:
        e.duration_min = round((e.end_time - e.start_time).total_seconds() / 60, 1)
    else:
        m.status = "maintenance" if body.reason_category == "planned_maintenance" else "down"
    if e.shift_id is None:
        from ..utils import shift_for
        sh = shift_for(e.start_time, db.scalars(select(M.Shift)).all())
        e.shift_id = sh.id if sh else None
    db.add(e)
    db.flush()
    audit(db, user, "create", "downtime", e.id, {"machine": m.code, "reason": e.reason_category})
    db.commit()
    return dt_out(e)


@router.post("/downtime/{event_id}/close", tags=["Downtime"])
def close_downtime(event_id: int, body: S.DowntimeClose, db: Session = Depends(get_db),
                   user: M.User = Depends(get_current_user)):
    e = get_or_404(db, M.DowntimeEvent, event_id, "Downtime event")
    if e.end_time:
        raise HTTPException(409, "Downtime already closed")
    end = body.end_time or now_local()
    if end <= e.start_time:
        raise HTTPException(422, "end_time must be after start_time")
    e.end_time = end
    e.duration_min = round((end - e.start_time).total_seconds() / 60, 1)
    if e.machine.status in ("down", "maintenance"):
        e.machine.status = "running"
    audit(db, user, "close", "downtime", e.id)
    db.commit()
    return dt_out(e)
