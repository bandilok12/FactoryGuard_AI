"""SQLAlchemy ORM models — the full FactoryGuard AI data model (17 tables)."""
from datetime import datetime, date
from sqlalchemy import (Integer, String, Float, Boolean, DateTime, Date, Text, ForeignKey,
                        UniqueConstraint, Index, CheckConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

ROLES = ("admin", "plant_head", "technician", "operator")


def _now():
    from .utils import now_local
    return now_local()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(120), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (CheckConstraint("role in ('admin','plant_head','technician','operator')", name="ck_user_role"),)


class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(5), unique=True)          # A / B / C
    name: Mapped[str] = mapped_column(String(50))
    start_time: Mapped[str] = mapped_column(String(5))                 # HH:MM
    end_time: Mapped[str] = mapped_column(String(5))
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    supervisor = relationship("User")


class ShiftAssignment(Base):
    """Operator/technician roster: who works which shift, on which machine, on which day."""
    __tablename__ = "shift_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id", ondelete="CASCADE"))
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id", ondelete="SET NULL"))
    work_date: Mapped[date] = mapped_column(Date, index=True)
    user = relationship("User")
    shift = relationship("Shift")
    machine = relationship("Machine")
    __table_args__ = (UniqueConstraint("user_id", "work_date", name="uq_user_day"),)


class Machine(Base):
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    machine_type: Mapped[str] = mapped_column(String(50), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(80))
    serial_no: Mapped[str | None] = mapped_column(String(80))
    line: Mapped[str | None] = mapped_column(String(40))
    install_date: Mapped[date | None] = mapped_column(Date)
    rated_speed: Mapped[float] = mapped_column(Float, default=6000)      # units / hour
    power_kw: Mapped[float | None] = mapped_column(Float)
    criticality: Mapped[str] = mapped_column(String(1), default="B")     # A/B/C
    status: Mapped[str] = mapped_column(String(20), default="running")   # running/idle/down/maintenance
    pm_interval_days: Mapped[int] = mapped_column(Integer, default=30)
    last_pm_date: Mapped[date | None] = mapped_column(Date)
    # sensor operating limits
    temp_max: Mapped[float] = mapped_column(Float, default=75)
    vibration_max: Mapped[float] = mapped_column(Float, default=7.1)     # mm/s RMS (ISO 10816)
    pressure_min: Mapped[float] = mapped_column(Float, default=4.5)      # bar
    pressure_max: Mapped[float] = mapped_column(Float, default=7.0)
    specs: Mapped[str | None] = mapped_column(Text)                      # JSON text
    health_score: Mapped[float] = mapped_column(Float, default=100)
    failure_risk: Mapped[float] = mapped_column(Float, default=0)        # latest ML probability 0..1
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProductionLog(Base):
    __tablename__ = "production_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    log_date: Mapped[date] = mapped_column(Date, index=True)
    product_name: Mapped[str] = mapped_column(String(120))
    batch_no: Mapped[str] = mapped_column(String(40), index=True)
    planned_qty: Mapped[int] = mapped_column(Integer)
    produced_qty: Mapped[int] = mapped_column(Integer)
    rejected_qty: Mapped[int] = mapped_column(Integer, default=0)
    planned_time_min: Mapped[float] = mapped_column(Float, default=480)
    run_time_min: Mapped[float] = mapped_column(Float, default=480)
    ideal_cycle_sec: Mapped[float] = mapped_column(Float, default=0.6)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    machine = relationship("Machine")
    shift = relationship("Shift")
    operator = relationship("User")
    __table_args__ = (CheckConstraint("produced_qty >= 0 AND rejected_qty >= 0 AND rejected_qty <= produced_qty",
                                      name="ck_prod_qty"),
                      Index("ix_prod_machine_date", "machine_id", "log_date"))


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    temperature: Mapped[float] = mapped_column(Float)      # °C (motor / sealing jaw)
    vibration: Mapped[float] = mapped_column(Float)        # mm/s RMS
    pressure: Mapped[float] = mapped_column(Float)         # bar (pneumatic)
    rpm: Mapped[float] = mapped_column(Float)
    torque: Mapped[float] = mapped_column(Float)           # Nm
    tool_wear: Mapped[float] = mapped_column(Float)        # minutes since tool change
    power_kw: Mapped[float | None] = mapped_column(Float)
    humidity: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(12), default="manual")   # manual / simulator / iot
    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    anomaly_reason: Mapped[str | None] = mapped_column(String(255))
    machine = relationship("Machine")
    __table_args__ = (Index("ix_sensor_machine_time", "machine_id", "recorded_at"),)


class MaintenanceTicket(Base):
    __tablename__ = "maintenance_tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20), default="mechanical")
    priority: Mapped[str] = mapped_column(String(10), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)
    ticket_type: Mapped[str] = mapped_column(String(15), default="breakdown")   # breakdown/preventive/predictive
    source: Mapped[str] = mapped_column(String(20), default="operator")         # operator/ai_anomaly/ai_prediction/schedule
    reported_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    root_cause: Mapped[str | None] = mapped_column(Text)
    action_taken: Mapped[str | None] = mapped_column(Text)
    labour_hours: Mapped[float | None] = mapped_column(Float)
    ai_triage: Mapped[str | None] = mapped_column(Text)   # JSON produced by GenAI triage
    machine = relationship("Machine")
    reported_by = relationship("User", foreign_keys=[reported_by_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
    activities = relationship("TicketActivity", cascade="all, delete-orphan", order_by="TicketActivity.created_at")
    parts_used = relationship("PartUsage", cascade="all, delete-orphan")


class TicketActivity(Base):
    __tablename__ = "ticket_activities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("maintenance_tickets.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20), default="comment")   # comment/status/assign/part
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    user = relationship("User")


class SparePart(Base):
    __tablename__ = "spare_parts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40))
    compatible_types: Mapped[str | None] = mapped_column(String(255))   # comma-separated machine types
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, default=5)
    reorder_qty: Mapped[int] = mapped_column(Integer, default=10)
    unit_cost: Mapped[float] = mapped_column(Float, default=0)
    location: Mapped[str | None] = mapped_column(String(40))
    supplier: Mapped[str | None] = mapped_column(String(80))
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_part_qty"),)


class PartUsage(Base):
    __tablename__ = "part_usages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("maintenance_tickets.id", ondelete="CASCADE"), index=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("spare_parts.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    part = relationship("SparePart")


class QualityInspection(Base):
    __tablename__ = "quality_inspections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(40), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    production_log_id: Mapped[int | None] = mapped_column(ForeignKey("production_logs.id", ondelete="SET NULL"))
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    inspector_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    inspected_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    sample_size: Mapped[int] = mapped_column(Integer)
    defects_found: Mapped[int] = mapped_column(Integer, default=0)
    defect_type: Mapped[str | None] = mapped_column(String(40))   # seal_leak/misprint/underfill/foreign_particle/crack/none
    seal_temp: Mapped[float | None] = mapped_column(Float)        # process params at time of batch
    seal_pressure: Mapped[float | None] = mapped_column(Float)
    line_speed: Mapped[float | None] = mapped_column(Float)
    fill_weight_dev: Mapped[float | None] = mapped_column(Float)  # % deviation from target
    humidity: Mapped[float | None] = mapped_column(Float)
    result: Mapped[str] = mapped_column(String(10), default="pass")   # pass/fail/rework
    ai_defect_risk: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    machine = relationship("Machine")
    inspector = relationship("User")
    __table_args__ = (CheckConstraint("defects_found >= 0 AND defects_found <= sample_size", name="ck_qc_defects"),)


class DowntimeEvent(Base):
    __tablename__ = "downtime_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_tickets.id", ondelete="SET NULL"))
    reported_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime)
    duration_min: Mapped[float | None] = mapped_column(Float)
    reason_category: Mapped[str] = mapped_column(String(30), index=True)
    reason_detail: Mapped[str | None] = mapped_column(Text)
    is_planned: Mapped[bool] = mapped_column(Boolean, default=False)
    machine = relationship("Machine")


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)   # anomaly/prediction/reorder/quality/pm_due
    severity: Mapped[str] = mapped_column(String(10), default="warning")   # info/warning/critical
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"))
    part_id: Mapped[int | None] = mapped_column(ForeignKey("spare_parts.id", ondelete="CASCADE"))
    message: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    machine = relationship("Machine")


class FailurePrediction(Base):
    __tablename__ = "failure_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    probability: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(10))
    predicted_mode: Mapped[str | None] = mapped_column(String(40))
    top_factors: Mapped[str | None] = mapped_column(Text)    # JSON


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    user = relationship("User")


class GenAILog(Base):
    __tablename__ = "genai_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    feature: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
