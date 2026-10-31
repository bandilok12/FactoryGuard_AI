from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models as M
from ..database import get_db
from ..security import get_current_user, require_roles, audit
from ..simulator import simulator
from .common import get_or_404

router = APIRouter(prefix="/simulator", tags=["Real-time Simulator"])


class InjectIn(BaseModel):
    machine_id: int
    mode: Literal["bearing", "overheat", "pneumatic", "tool_wear"]
    severity: float = Field(0.75, ge=0.1, le=1.0)


class IntervalIn(BaseModel):
    interval_sec: float = Field(ge=1, le=300)


@router.get("/status")
def status(_: M.User = Depends(get_current_user)):
    return simulator.status()


@router.post("/start")
def start(db: Session = Depends(get_db), u: M.User = Depends(require_roles("admin"))):
    simulator.start()
    audit(db, u, "simulator_start")
    db.commit()
    return simulator.status()


@router.post("/stop")
def stop(db: Session = Depends(get_db), u: M.User = Depends(require_roles("admin"))):
    simulator.stop()
    audit(db, u, "simulator_stop")
    db.commit()
    return simulator.status()


@router.post("/interval")
def set_interval(body: IntervalIn, u: M.User = Depends(require_roles("admin"))):
    simulator.interval = body.interval_sec
    return simulator.status()


@router.post("/inject", summary="Inject a degradation fault into a machine (demo)")
def inject(body: InjectIn, db: Session = Depends(get_db), u: M.User = Depends(require_roles("admin", "plant_head"))):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    simulator.inject(m.id, body.mode, body.severity)
    audit(db, u, "simulator_inject", "machine", m.id, body.model_dump())
    db.commit()
    return {"ok": True, "machine": m.code, **body.model_dump()}
