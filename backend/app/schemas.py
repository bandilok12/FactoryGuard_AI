"""Pydantic request/response schemas with validation rules."""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator

Role = Literal["admin", "plant_head", "technician", "operator"]
Priority = Literal["low", "medium", "high", "critical"]
TicketStatus = Literal["open", "assigned", "in_progress", "on_hold", "resolved", "closed"]
MachineStatus = Literal["running", "idle", "down", "maintenance"]
DowntimeReason = Literal["breakdown", "changeover", "material_shortage", "planned_maintenance",
                         "power_failure", "quality_hold", "no_operator", "other"]
DefectType = Literal["none", "seal_leak", "misprint", "underfill", "overfill", "foreign_particle",
                     "crack", "missing_tablet", "label_error"]

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
CODE = r"^[A-Za-z0-9_\-/]+$"


# ---------- auth / users ----------
class LoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.]+$")
    full_name: str = Field(min_length=2, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r"^\+?[0-9 \-]{7,20}$")
    role: Role
    password: str = Field(min_length=8, max_length=64)


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r"^\+?[0-9 \-]{7,20}$")
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=8, max_length=64)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=64)


# ---------- shifts ----------
class ShiftIn(BaseModel):
    code: str = Field(min_length=1, max_length=5, pattern=CODE)
    name: str = Field(min_length=2, max_length=50)
    start_time: str = Field(pattern=HHMM)
    end_time: str = Field(pattern=HHMM)
    supervisor_id: Optional[int] = None


class AssignmentIn(BaseModel):
    user_id: int
    shift_id: int
    machine_id: Optional[int] = None
    work_date: date


# ---------- machines ----------
class MachineIn(BaseModel):
    code: str = Field(min_length=2, max_length=20, pattern=CODE)
    name: str = Field(min_length=2, max_length=120)
    machine_type: str = Field(min_length=2, max_length=50)
    manufacturer: Optional[str] = Field(None, max_length=80)
    model: Optional[str] = Field(None, max_length=80)
    serial_no: Optional[str] = Field(None, max_length=80)
    line: Optional[str] = Field(None, max_length=40)
    install_date: Optional[date] = None
    rated_speed: float = Field(6000, gt=0, le=1_000_000)
    power_kw: Optional[float] = Field(None, ge=0, le=5000)
    criticality: Literal["A", "B", "C"] = "B"
    status: MachineStatus = "running"
    pm_interval_days: int = Field(30, ge=1, le=365)
    last_pm_date: Optional[date] = None
    temp_max: float = Field(75, gt=0, le=400)
    vibration_max: float = Field(7.1, gt=0, le=100)
    pressure_min: float = Field(4.5, ge=0, le=50)
    pressure_max: float = Field(7.0, gt=0, le=50)
    specs: Optional[dict] = None

    @model_validator(mode="after")
    def _p(self):
        if self.pressure_min >= self.pressure_max:
            raise ValueError("pressure_min must be less than pressure_max")
        return self


class MachineUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    machine_type: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_no: Optional[str] = None
    line: Optional[str] = None
    rated_speed: Optional[float] = Field(None, gt=0)
    power_kw: Optional[float] = Field(None, ge=0)
    criticality: Optional[Literal["A", "B", "C"]] = None
    status: Optional[MachineStatus] = None
    pm_interval_days: Optional[int] = Field(None, ge=1, le=365)
    last_pm_date: Optional[date] = None
    temp_max: Optional[float] = Field(None, gt=0)
    vibration_max: Optional[float] = Field(None, gt=0)
    pressure_min: Optional[float] = Field(None, ge=0)
    pressure_max: Optional[float] = Field(None, gt=0)
    specs: Optional[dict] = None


class MachineStatusIn(BaseModel):
    status: MachineStatus


# ---------- production ----------
class ProductionIn(BaseModel):
    machine_id: int
    shift_id: int
    log_date: date
    product_name: str = Field(min_length=2, max_length=120)
    batch_no: str = Field(min_length=2, max_length=40, pattern=CODE)
    planned_qty: int = Field(ge=0, le=10_000_000)
    produced_qty: int = Field(ge=0, le=10_000_000)
    rejected_qty: int = Field(0, ge=0)
    planned_time_min: float = Field(480, gt=0, le=720)
    run_time_min: float = Field(ge=0, le=720)
    ideal_cycle_sec: float = Field(0.6, gt=0, le=3600)
    notes: Optional[str] = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _chk(self):
        if self.rejected_qty > self.produced_qty:
            raise ValueError("rejected_qty cannot exceed produced_qty")
        if self.run_time_min > self.planned_time_min:
            raise ValueError("run_time_min cannot exceed planned_time_min")
        if self.log_date > date.today():
            raise ValueError("log_date cannot be in the future")
        return self


# ---------- sensors ----------
class SensorIn(BaseModel):
    machine_id: int
    shift_id: Optional[int] = None
    recorded_at: Optional[datetime] = None
    temperature: float = Field(ge=-20, le=300)
    vibration: float = Field(ge=0, le=100)
    pressure: float = Field(ge=0, le=50)
    rpm: float = Field(ge=0, le=20000)
    torque: float = Field(ge=0, le=500)
    tool_wear: float = Field(ge=0, le=1000)
    power_kw: Optional[float] = Field(None, ge=0, le=5000)
    humidity: Optional[float] = Field(None, ge=0, le=100)


# ---------- maintenance ----------
class TicketIn(BaseModel):
    machine_id: int
    title: str = Field(min_length=4, max_length=200)
    description: Optional[str] = Field(None, max_length=4000)
    category: Literal["mechanical", "electrical", "pneumatic", "sensor", "software", "other"] = "mechanical"
    priority: Priority = "medium"
    ticket_type: Literal["breakdown", "preventive", "predictive"] = "breakdown"
    assigned_to_id: Optional[int] = None
    use_ai_triage: bool = True


class TicketUpdate(BaseModel):
    status: Optional[TicketStatus] = None
    priority: Optional[Priority] = None
    assigned_to_id: Optional[int] = None
    root_cause: Optional[str] = Field(None, max_length=4000)
    action_taken: Optional[str] = Field(None, max_length=4000)
    labour_hours: Optional[float] = Field(None, ge=0, le=500)
    comment: Optional[str] = Field(None, max_length=2000)


class PartUseIn(BaseModel):
    part_id: int
    quantity: int = Field(gt=0, le=1000)


# ---------- spares ----------
class PartIn(BaseModel):
    part_no: str = Field(min_length=2, max_length=30, pattern=CODE)
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=40)
    compatible_types: Optional[str] = Field(None, max_length=255)
    quantity: int = Field(0, ge=0, le=1_000_000)
    reorder_level: int = Field(5, ge=0, le=100_000)
    reorder_qty: int = Field(10, ge=1, le=100_000)
    unit_cost: float = Field(0, ge=0, le=10_000_000)
    location: Optional[str] = Field(None, max_length=40)
    supplier: Optional[str] = Field(None, max_length=80)
    lead_time_days: int = Field(7, ge=0, le=365)


class PartUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    compatible_types: Optional[str] = None
    reorder_level: Optional[int] = Field(None, ge=0)
    reorder_qty: Optional[int] = Field(None, ge=1)
    unit_cost: Optional[float] = Field(None, ge=0)
    location: Optional[str] = None
    supplier: Optional[str] = None
    lead_time_days: Optional[int] = Field(None, ge=0)


class StockAdjust(BaseModel):
    delta: int = Field(ge=-100_000, le=100_000)
    reason: str = Field(min_length=3, max_length=200)

    @field_validator("delta")
    @classmethod
    def _nz(cls, v):
        if v == 0:
            raise ValueError("delta must be non-zero")
        return v


# ---------- quality ----------
class InspectionIn(BaseModel):
    batch_no: str = Field(min_length=2, max_length=40, pattern=CODE)
    machine_id: int
    production_log_id: Optional[int] = None
    shift_id: Optional[int] = None
    sample_size: int = Field(gt=0, le=100_000)
    defects_found: int = Field(0, ge=0)
    defect_type: DefectType = "none"
    seal_temp: Optional[float] = Field(None, ge=0, le=400)
    seal_pressure: Optional[float] = Field(None, ge=0, le=50)
    line_speed: Optional[float] = Field(None, ge=0, le=1000)
    fill_weight_dev: Optional[float] = Field(None, ge=-100, le=100)
    humidity: Optional[float] = Field(None, ge=0, le=100)
    result: Optional[Literal["pass", "fail", "rework"]] = None
    notes: Optional[str] = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _chk(self):
        if self.defects_found > self.sample_size:
            raise ValueError("defects_found cannot exceed sample_size")
        return self


class DefectRiskIn(BaseModel):
    machine_id: Optional[int] = None
    seal_temp: float = Field(ge=0, le=400)
    seal_pressure: float = Field(ge=0, le=50)
    line_speed: float = Field(ge=0, le=1000)
    fill_weight_dev: float = Field(ge=-100, le=100)
    humidity: float = Field(ge=0, le=100)
    vibration: Optional[float] = Field(None, ge=0, le=100)


# ---------- downtime ----------
class DowntimeIn(BaseModel):
    machine_id: int
    shift_id: Optional[int] = None
    ticket_id: Optional[int] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    reason_category: DowntimeReason
    reason_detail: Optional[str] = Field(None, max_length=1000)
    is_planned: bool = False

    @model_validator(mode="after")
    def _chk(self):
        if self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class DowntimeClose(BaseModel):
    end_time: Optional[datetime] = None


# ---------- genai ----------
class ChatIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    machine_id: Optional[int] = None
    history: list[dict] = Field(default_factory=list, max_length=10)


class ShiftReportIn(BaseModel):
    report_date: date
    shift_id: Optional[int] = None   # None => all shifts of the day


class TriageIn(BaseModel):
    machine_id: int
    description: str = Field(min_length=5, max_length=2000)


class SettingIn(BaseModel):
    value: str = Field(max_length=255)
