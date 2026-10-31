"""AI (scikit-learn) and GenAI (LLM) endpoints."""
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as M, schemas as S, services
from ..database import get_db
from ..genai import assistants, llm, rag
from ..ml import inference
from ..security import get_current_user, require_roles, audit, MANAGERS, MAINT
from ..utils import to_dict, now_local
from .common import get_or_404
from .maintenance import _sensor_ctx, _machine_brief, log_genai

router = APIRouter()


# =========================================================================== AI / ML
@router.get("/ai/models", tags=["AI (ML)"], summary="Model cards & hold-out evaluation metrics")
def model_info(_: M.User = Depends(get_current_user)):
    return {"metrics": inference.metrics(),
            "data": "Synthetic data generated with AI4I-2020-style failure physics (public Kaggle/UCI dataset schema). "
                    "No real factory data."}


@router.get("/ai/predict/{machine_id}", tags=["AI (ML)"], summary="Failure probability for one machine (next 24 h)")
def predict_machine(machine_id: int, db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    m = get_or_404(db, M.Machine, machine_id, "Machine")
    p = services.predict_for_machine(db, m, store=True)
    db.commit()
    if not p:
        raise HTTPException(409, "No sensor readings for this machine yet")
    history = db.scalars(select(M.FailurePrediction).where(M.FailurePrediction.machine_id == m.id)
                         .order_by(M.FailurePrediction.created_at.desc()).limit(48)).all()
    return {"machine": _machine_brief(m), **p,
            "history": [{"t": h.created_at.isoformat(), "p": h.probability} for h in reversed(history)]}


@router.get("/ai/fleet-risk", tags=["AI (ML)"], summary="All machines ranked by predicted failure risk")
def fleet_risk(refresh: bool = False, db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    machines = db.scalars(select(M.Machine)).all()
    out = []
    for m in machines:
        p = services.predict_for_machine(db, m, store=False) if refresh else None
        last = db.scalars(select(M.FailurePrediction).where(M.FailurePrediction.machine_id == m.id)
                          .order_by(M.FailurePrediction.created_at.desc()).limit(1)).first()
        out.append({"id": m.id, "code": m.code, "name": m.name, "type": m.machine_type, "status": m.status,
                    "criticality": m.criticality, "risk": p["probability"] if p else m.failure_risk,
                    "risk_level": inference.risk_level(p["probability"] if p else m.failure_risk),
                    "health": m.health_score,
                    "mode": (p or {}).get("mode_description") or (inference.MODE_TEXT.get(last.predicted_mode) if last else None),
                    "factors": (p or {}).get("top_factors") or (json.loads(last.top_factors or "[]") if last else [])})
    if refresh:
        db.commit()
    return sorted(out, key=lambda x: -x["risk"])


@router.post("/ai/anomaly/check", tags=["AI (ML)"], summary="Score a reading without saving it")
def anomaly_check(body: S.SensorIn, db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    return inference.detect_anomaly(body.model_dump(), services.limits_of(m))


@router.post("/ai/retrain", tags=["AI (ML)"], summary="Retrain all models (admin)")
def retrain(db: Session = Depends(get_db), user: M.User = Depends(require_roles("admin"))):
    from ..ml.train import train_all
    metrics = train_all(verbose=False)
    inference._models.clear()
    audit(db, user, "retrain", "ml", None)
    db.commit()
    return {"ok": True, "metrics": metrics}


# =========================================================================== GenAI
@router.get("/genai/status", tags=["GenAI"])
def genai_status(_: M.User = Depends(get_current_user)):
    p = llm.active_provider()
    return {"provider": p, "live": p != "offline",
            "note": "Set GEMINI_API_KEY / GROQ_API_KEY / OPENAI_API_KEY in .env for live LLM answers. "
                    "Offline mode uses retrieval + templates so every feature still works."}


@router.get("/genai/manuals", tags=["GenAI"], summary="Manual library indexed for RAG")
def manuals(_: M.User = Depends(get_current_user)):
    return rag.list_manuals()


@router.post("/genai/troubleshoot", tags=["GenAI"], summary="Troubleshooting Copilot (RAG over manuals + live data)")
def troubleshoot(body: S.ChatIn, db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    machine, ctx, recent = None, None, []
    if body.machine_id:
        m = get_or_404(db, M.Machine, body.machine_id, "Machine")
        machine, ctx = _machine_brief(m), _sensor_ctx(db, m)
        recent = [{"ticket_no": t.ticket_no, "title": t.title, "root_cause": t.root_cause, "action_taken": t.action_taken}
                  for t in db.scalars(select(M.MaintenanceTicket).where(M.MaintenanceTicket.machine_id == m.id,
                                                                        M.MaintenanceTicket.root_cause.isnot(None))
                                      .order_by(M.MaintenanceTicket.created_at.desc()).limit(3))]
    res = assistants.troubleshoot(body.question, machine, ctx, recent, body.history)
    log_genai(db, user, res.pop("_log"))
    db.commit()
    return res


@router.post("/genai/shift-report", tags=["GenAI"], summary="Shift performance report for the plant head")
def shift_report(body: S.ShiftReportIn, db: Session = Depends(get_db), user: M.User = Depends(require_roles(*MANAGERS))):
    if body.report_date > now_local().date():
        raise HTTPException(422, "report_date cannot be in the future")
    kpis = services.shift_kpis(db, body.report_date, body.shift_id)
    res = assistants.shift_report(kpis)
    log_genai(db, user, res.pop("_log"))
    db.commit()
    return {**res, "kpis": kpis}


@router.post("/genai/triage", tags=["GenAI"], summary="Preview AI triage of an operator issue description")
def triage(body: S.TriageIn, db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    parts = [to_dict(p) for p in db.scalars(select(M.SparePart))]
    res = assistants.triage(body.description, _machine_brief(m), _sensor_ctx(db, m), parts)
    log_genai(db, user, res.pop("_log", None))
    db.commit()
    pmap = {p["part_no"]: p for p in parts}
    res["suggested_parts"] = [{"part_no": n, "name": pmap[n]["name"], "in_stock": pmap[n]["quantity"]}
                              for n in res.get("suggested_part_nos", [])]
    return res


@router.post("/genai/quality-rca/{inspection_id}", tags=["GenAI"], summary="Root-cause analysis & CAPA for a batch")
def quality_rca(inspection_id: int, db: Session = Depends(get_db),
                user: M.User = Depends(require_roles(*MANAGERS, "operator"))):
    i = get_or_404(db, M.QualityInspection, inspection_id, "Inspection")
    params = {"seal_temp": i.seal_temp or 150, "seal_pressure": i.seal_pressure or 4.5, "line_speed": i.line_speed or 45,
              "fill_weight_dev": i.fill_weight_dev or 0, "humidity": i.humidity or 45}
    t0 = i.inspected_at - timedelta(hours=8)
    rows = db.scalars(select(M.SensorReading).where(M.SensorReading.machine_id == i.machine_id,
                                                    M.SensorReading.recorded_at >= t0,
                                                    M.SensorReading.recorded_at <= i.inspected_at)).all()
    sensor = None
    if rows:
        params["vibration"] = sum(r.vibration for r in rows) / len(rows)
        sensor = {"readings": len(rows), "anomalies": sum(r.is_anomaly for r in rows),
                  "avg_temperature": round(sum(r.temperature for r in rows) / len(rows), 1),
                  "avg_vibration": round(params["vibration"], 2),
                  "avg_pressure": round(sum(r.pressure for r in rows) / len(rows), 2)}
    payload = {"inspection": {k: v for k, v in to_dict(i).items() if k not in ("id",)},
               "machine": _machine_brief(i.machine), "process_parameters": params,
               "ml_assessment": inference.predict_defect(params), "sensor_summary": sensor}
    res = assistants.quality_rca(payload)
    log_genai(db, user, res.pop("_log"))
    db.commit()
    return {**res, "ml_assessment": payload["ml_assessment"]}


@router.get("/genai/history", tags=["GenAI"], summary="Audit trail of GenAI calls")
def history(limit: int = 30, db: Session = Depends(get_db), user: M.User = Depends(require_roles(*MANAGERS))):
    rows = db.scalars(select(M.GenAILog).order_by(M.GenAILog.created_at.desc()).limit(min(limit, 200)))
    return [{k: v for k, v in to_dict(r).items() if k != "prompt"} for r in rows]
