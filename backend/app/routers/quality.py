"""Quality inspection records for each batch (+ pre-batch AI defect-risk check)."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as M, schemas as S, services
from ..database import get_db
from ..ml import inference
from ..security import get_current_user, require_roles, audit, FLOOR
from ..utils import to_dict, now_local, shift_for
from .common import get_or_404, Page

router = APIRouter(prefix="/quality", tags=["Quality Inspections"])


def qc_out(i: M.QualityInspection):
    d = to_dict(i)
    d.update(machine_code=i.machine.code, inspector=i.inspector.full_name if i.inspector else None,
             defect_rate=round(i.defects_found / i.sample_size * 100, 2) if i.sample_size else 0)
    return d


@router.get("")
def list_inspections(result: str | None = None, machine_id: int | None = None, batch_no: str | None = None,
                     start: date | None = None, end: date | None = None, page: Page = Depends(),
                     db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    q = select(M.QualityInspection).order_by(M.QualityInspection.inspected_at.desc())
    if result:
        q = q.where(M.QualityInspection.result == result)
    if machine_id:
        q = q.where(M.QualityInspection.machine_id == machine_id)
    if batch_no:
        q = q.where(M.QualityInspection.batch_no.ilike(f"%{batch_no}%"))
    if start:
        q = q.where(M.QualityInspection.inspected_at >= datetime.combine(start, datetime.min.time()))
    if end:
        q = q.where(M.QualityInspection.inspected_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
    return [qc_out(i) for i in db.scalars(q.limit(page.limit).offset(page.offset))]


@router.get("/{inspection_id}")
def get_inspection(inspection_id: int, db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    return qc_out(get_or_404(db, M.QualityInspection, inspection_id, "Inspection"))


@router.post("", status_code=201, summary="Record batch inspection (AI defect risk is attached automatically)")
def create_inspection(body: S.InspectionIn, db: Session = Depends(get_db), user: M.User = Depends(require_roles(*FLOOR))):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    if body.production_log_id:
        pl = get_or_404(db, M.ProductionLog, body.production_log_id, "Production log")
        if pl.machine_id != m.id:
            raise HTTPException(422, "Production log belongs to a different machine")
    data = body.model_dump()
    rate = body.defects_found / body.sample_size
    if not data.get("result"):   # AQL-style rule
        data["result"] = "fail" if rate > 0.02 else "rework" if rate > 0.005 else "pass"
    if body.defects_found == 0:
        data["defect_type"] = "none"
    elif data["defect_type"] == "none":
        raise HTTPException(422, "defect_type is required when defects_found > 0")
    i = M.QualityInspection(**data, inspector_id=user.id)
    if not i.shift_id:
        sh = shift_for(now_local(), db.scalars(select(M.Shift)).all())
        i.shift_id = sh.id if sh else None
    if None not in (body.seal_temp, body.seal_pressure, body.line_speed, body.fill_weight_dev, body.humidity):
        i.ai_defect_risk = inference.predict_defect(body.model_dump())["defect_probability"]
    db.add(i)
    db.flush()
    if i.result == "fail":
        services.add_alert(db, "quality", "critical",
                           f"Batch {i.batch_no} on {m.code} FAILED inspection: {i.defects_found}/{i.sample_size} "
                           f"({i.defect_type})", machine_id=m.id, dedupe_minutes=1)
    audit(db, user, "create", "inspection", i.id, {"batch": i.batch_no, "result": i.result})
    db.commit()
    return qc_out(i)


@router.post("/predict", summary="AI: pre-batch defect-risk check from planned process parameters")
def predict_defect(body: S.DefectRiskIn, db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    params = body.model_dump()
    if body.machine_id and body.vibration is None:
        rows = services.recent_readings(db, body.machine_id, 5)
        if rows:
            params["vibration"] = sum(r.vibration for r in rows) / len(rows)
    res = inference.predict_defect(params)
    res["inputs"] = params
    if body.machine_id and res["defect_probability"] >= float(services.get_setting(db, "defect_hold_threshold")):
        m = db.get(M.Machine, body.machine_id)
        if m:
            services.add_alert(db, "quality", "warning",
                               f"High defect risk ({res['defect_probability']:.0%}) for next batch on {m.code}: "
                               f"{res['likely_defect_type']}", machine_id=m.id, dedupe_minutes=30)
            db.commit()
    return res
