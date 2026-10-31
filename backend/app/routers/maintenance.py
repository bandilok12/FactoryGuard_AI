"""Maintenance ticket workflow and spare-parts inventory."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models as M, schemas as S, services
from ..database import get_db
from ..genai import assistants
from ..security import get_current_user, require_roles, audit, MAINT, MANAGERS
from ..utils import to_dict, now_local
from .common import get_or_404, Page

router = APIRouter()

# allowed status transitions (state machine)
TRANSITIONS = {
    "open": {"assigned", "in_progress", "closed"},
    "assigned": {"in_progress", "open", "on_hold"},
    "in_progress": {"on_hold", "resolved"},
    "on_hold": {"in_progress"},
    "resolved": {"closed", "in_progress"},       # close = verified, in_progress = reopen
    "closed": set(),
}


def ticket_out(t: M.MaintenanceTicket, full=False):
    d = to_dict(t)
    d["ai_triage"] = json.loads(t.ai_triage) if t.ai_triage else None
    d.update(machine_code=t.machine.code, machine_name=t.machine.name,
             reported_by=t.reported_by.full_name if t.reported_by else "FactoryGuard AI",
             assigned_to=t.assigned_to.full_name if t.assigned_to else None)
    if t.resolved_at:
        d["resolution_hours"] = round((t.resolved_at - t.created_at).total_seconds() / 3600, 2)
    if full:
        d["activities"] = [{**to_dict(a), "user": a.user.full_name if a.user else "System"} for a in t.activities]
        d["parts_used"] = [{**to_dict(u), "part_no": u.part.part_no, "name": u.part.name,
                            "cost": round(u.quantity * u.part.unit_cost, 2)} for u in t.parts_used]
        d["parts_cost"] = round(sum(x["cost"] for x in d["parts_used"]), 2)
    return d


def _sensor_ctx(db, machine):
    rows = services.recent_readings(db, machine.id, 5)
    if not rows:
        return None
    r = rows[-1]
    flags = [x.anomaly_reason for x in rows if x.is_anomaly and x.anomaly_reason]
    return {"temperature": r.temperature, "vibration": r.vibration, "pressure": r.pressure, "rpm": r.rpm,
            "torque": r.torque, "tool_wear": r.tool_wear, "recorded_at": r.recorded_at.isoformat(),
            "failure_risk": machine.failure_risk, "flags": list(dict.fromkeys(flags))[:3]}


def _machine_brief(m):
    return {"code": m.code, "name": m.name, "machine_type": m.machine_type, "criticality": m.criticality,
            "status": m.status, "line": m.line}


def log_genai(db, user, payload):
    if payload:
        db.add(M.GenAILog(user_id=user.id if user else None, **payload))


# =========================================================================== TICKETS
@router.get("/tickets", tags=["Maintenance Tickets"])
def list_tickets(status: str | None = None, machine_id: int | None = None, priority: str | None = None,
                 mine: bool = False, open_only: bool = False, page: Page = Depends(),
                 db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    q = select(M.MaintenanceTicket).order_by(M.MaintenanceTicket.created_at.desc())
    if status:
        q = q.where(M.MaintenanceTicket.status == status)
    if open_only:
        q = q.where(M.MaintenanceTicket.status.notin_(["resolved", "closed"]))
    if machine_id:
        q = q.where(M.MaintenanceTicket.machine_id == machine_id)
    if priority:
        q = q.where(M.MaintenanceTicket.priority == priority)
    if mine:
        q = q.where((M.MaintenanceTicket.assigned_to_id == user.id) | (M.MaintenanceTicket.reported_by_id == user.id))
    return [ticket_out(t) for t in db.scalars(q.limit(page.limit).offset(page.offset))]


@router.get("/tickets/{ticket_id}", tags=["Maintenance Tickets"])
def get_ticket(ticket_id: int, db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    return ticket_out(get_or_404(db, M.MaintenanceTicket, ticket_id, "Ticket"), full=True)


@router.post("/tickets", tags=["Maintenance Tickets"], status_code=201,
             summary="Raise a ticket (optionally GenAI-triaged: category, priority, causes, parts)")
def create_ticket(body: S.TicketIn, db: Session = Depends(get_db), user: M.User = Depends(get_current_user)):
    m = get_or_404(db, M.Machine, body.machine_id, "Machine")
    if body.assigned_to_id:
        tech = get_or_404(db, M.User, body.assigned_to_id, "User")
        if tech.role != "technician" or user.role not in MANAGERS:
            raise HTTPException(422, "Only plant head/admin can assign, and only to technicians")
    t = M.MaintenanceTicket(ticket_no=services.next_ticket_no(db), machine_id=m.id, title=body.title,
                            description=body.description, category=body.category, priority=body.priority,
                            ticket_type=body.ticket_type, reported_by_id=user.id,
                            source="operator" if user.role == "operator" else "manual",
                            assigned_to_id=body.assigned_to_id)
    if body.assigned_to_id:
        t.status, t.assigned_at = "assigned", now_local()
    if body.use_ai_triage:
        parts = [to_dict(p) for p in db.scalars(select(M.SparePart))]
        tri = assistants.triage(f"{body.title}. {body.description or ''}", _machine_brief(m), _sensor_ctx(db, m), parts)
        log_genai(db, user, tri.pop("_log", None))
        t.ai_triage = json.dumps(tri)
        # AI may raise priority / set category but never lowers what the reporter chose
        order = ["low", "medium", "high", "critical"]
        if order.index(tri["priority"]) > order.index(t.priority):
            t.priority = tri["priority"]
        if body.category == "mechanical" and tri.get("category"):
            t.category = tri["category"]
    db.add(t)
    db.flush()
    db.add(M.TicketActivity(ticket_id=t.id, user_id=user.id, kind="status", message=f"Ticket raised ({t.priority})"))
    if t.ai_triage:
        db.add(M.TicketActivity(ticket_id=t.id, kind="ai", message="GenAI triage attached"))
    audit(db, user, "create", "ticket", t.id, {"no": t.ticket_no})
    db.commit()
    return ticket_out(t, full=True)


@router.patch("/tickets/{ticket_id}", tags=["Maintenance Tickets"], summary="Workflow: assign / start / hold / resolve / close")
def update_ticket(ticket_id: int, body: S.TicketUpdate, db: Session = Depends(get_db),
                  user: M.User = Depends(get_current_user)):
    t = get_or_404(db, M.MaintenanceTicket, ticket_id, "Ticket")
    data = body.model_dump(exclude_unset=True)
    now = now_local()
    if user.role == "operator" and set(data) - {"comment"}:
        raise HTTPException(403, "Operators can only add comments to tickets")
    if "assigned_to_id" in data and data["assigned_to_id"] != t.assigned_to_id:
        tech = get_or_404(db, M.User, data["assigned_to_id"], "User")
        if tech.role != "technician" or not tech.is_active:
            raise HTTPException(422, "Tickets can only be assigned to active technicians")
        if user.role == "technician" and tech.id != user.id:
            raise HTTPException(403, "Technicians can only self-assign")
        t.assigned_to_id, t.assigned_at = tech.id, now
        if t.status == "open":
            t.status = "assigned"
        db.add(M.TicketActivity(ticket_id=t.id, user_id=user.id, kind="assign", message=f"Assigned to {tech.full_name}"))
    for k in ("root_cause", "action_taken", "labour_hours", "priority"):
        if k in data:
            setattr(t, k, data[k])
    if "status" in data and data["status"] != t.status:
        new = data["status"]
        if new not in TRANSITIONS[t.status]:
            raise HTTPException(409, f"Cannot move ticket from '{t.status}' to '{new}'")
        if new == "closed" and user.role not in MANAGERS and t.status != "open":
            raise HTTPException(403, "Only plant head/admin can verify & close a resolved ticket")
        if new in ("in_progress", "resolved") and not t.assigned_to_id:
            if user.role == "technician":
                t.assigned_to_id, t.assigned_at = user.id, now
            else:
                raise HTTPException(409, "Assign a technician first")
        if new == "resolved" and not (t.root_cause and t.action_taken):
            raise HTTPException(422, "root_cause and action_taken are required to resolve a ticket")
        if new == "in_progress" and not t.started_at:
            t.started_at = now
            if t.machine.status == "running":
                t.machine.status = "maintenance"
        if new == "resolved":
            t.resolved_at = now
            if t.machine.status in ("maintenance", "down"):
                t.machine.status = "running"
            # auto-close open downtime linked to this ticket
            for e in db.scalars(select(M.DowntimeEvent).where(M.DowntimeEvent.ticket_id == t.id,
                                                              M.DowntimeEvent.end_time.is_(None))):
                e.end_time, e.duration_min = now, round((now - e.start_time).total_seconds() / 60, 1)
            if t.ticket_type == "preventive":
                t.machine.last_pm_date = now.date()
        if new == "closed":
            t.closed_at = now
        db.add(M.TicketActivity(ticket_id=t.id, user_id=user.id, kind="status", message=f"{t.status} → {new}"))
        t.status = new
    if data.get("comment"):
        db.add(M.TicketActivity(ticket_id=t.id, user_id=user.id, kind="comment", message=data["comment"]))
    audit(db, user, "update", "ticket", t.id, {k: v for k, v in data.items() if k != "comment"})
    db.commit()
    db.refresh(t)
    return ticket_out(t, full=True)


@router.post("/tickets/{ticket_id}/parts", tags=["Maintenance Tickets"], summary="Issue spare parts to a ticket")
def use_part(ticket_id: int, body: S.PartUseIn, db: Session = Depends(get_db),
             user: M.User = Depends(require_roles(*MAINT))):
    t = get_or_404(db, M.MaintenanceTicket, ticket_id, "Ticket")
    if t.status in ("resolved", "closed"):
        raise HTTPException(409, "Ticket is already resolved")
    p = get_or_404(db, M.SparePart, body.part_id, "Spare part")
    if p.quantity < body.quantity:
        raise HTTPException(409, f"Insufficient stock: {p.quantity} available")
    p.quantity -= body.quantity
    db.add(M.PartUsage(ticket_id=t.id, part_id=p.id, quantity=body.quantity, used_by_id=user.id))
    db.add(M.TicketActivity(ticket_id=t.id, user_id=user.id, kind="part",
                            message=f"Issued {body.quantity} × {p.part_no} {p.name}"))
    services.check_reorder(db, p)
    audit(db, user, "issue_part", "spare_part", p.id, {"ticket": t.ticket_no, "qty": body.quantity})
    db.commit()
    return ticket_out(t, full=True)


# =========================================================================== SPARE PARTS
def part_out(p):
    d = to_dict(p)
    d["needs_reorder"] = p.quantity <= p.reorder_level
    d["stock_value"] = round(p.quantity * p.unit_cost, 2)
    return d


@router.get("/spares", tags=["Spare Parts"])
def list_parts(q: str | None = None, low_stock: bool = False, category: str | None = None,
               db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    stmt = select(M.SparePart).order_by(M.SparePart.part_no)
    if q:
        stmt = stmt.where(M.SparePart.name.ilike(f"%{q}%") | M.SparePart.part_no.ilike(f"%{q}%"))
    if low_stock:
        stmt = stmt.where(M.SparePart.quantity <= M.SparePart.reorder_level)
    if category:
        stmt = stmt.where(M.SparePart.category == category)
    return [part_out(p) for p in db.scalars(stmt)]


@router.get("/spares/reorder-report", tags=["Spare Parts"], summary="Reorder alert list with suggested PO")
def reorder_report(db: Session = Depends(get_db), _: M.User = Depends(require_roles(*MAINT))):
    parts = db.scalars(select(M.SparePart).where(M.SparePart.quantity <= M.SparePart.reorder_level)).all()
    since = now_local().replace(day=1)
    out = []
    for p in parts:
        used_90 = db.scalar(select(func.coalesce(func.sum(M.PartUsage.quantity), 0))
                            .where(M.PartUsage.part_id == p.id)) or 0
        qty = max(p.reorder_qty, p.reorder_level * 2 - p.quantity)
        out.append({**part_out(p), "total_consumed": used_90, "suggested_order_qty": qty,
                    "estimated_cost": round(qty * p.unit_cost, 2),
                    "urgency": "critical" if p.quantity == 0 else "high" if p.quantity < p.reorder_level / 2 else "normal"})
    return sorted(out, key=lambda x: ["critical", "high", "normal"].index(x["urgency"]))


@router.post("/spares", tags=["Spare Parts"], status_code=201)
def create_part(body: S.PartIn, db: Session = Depends(get_db), user: M.User = Depends(require_roles(*MANAGERS))):
    p = M.SparePart(**body.model_dump())
    p.part_no = p.part_no.upper()
    db.add(p)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Part number already exists")
    services.check_reorder(db, p)
    audit(db, user, "create", "spare_part", p.id)
    db.commit()
    return part_out(p)


@router.patch("/spares/{part_id}", tags=["Spare Parts"])
def update_part(part_id: int, body: S.PartUpdate, db: Session = Depends(get_db),
                user: M.User = Depends(require_roles(*MANAGERS))):
    p = get_or_404(db, M.SparePart, part_id, "Spare part")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    services.check_reorder(db, p)
    audit(db, user, "update", "spare_part", p.id)
    db.commit()
    return part_out(p)


@router.post("/spares/{part_id}/adjust", tags=["Spare Parts"], summary="Goods receipt (+) or write-off (−)")
def adjust_stock(part_id: int, body: S.StockAdjust, db: Session = Depends(get_db),
                 user: M.User = Depends(require_roles(*MAINT))):
    p = get_or_404(db, M.SparePart, part_id, "Spare part")
    if p.quantity + body.delta < 0:
        raise HTTPException(409, f"Stock cannot go negative (current {p.quantity})")
    p.quantity += body.delta
    if body.delta > 0 and p.quantity > p.reorder_level:   # receipt clears reorder alerts
        for a in db.scalars(select(M.Alert).where(M.Alert.part_id == p.id, M.Alert.acknowledged == False)):  # noqa
            a.acknowledged, a.acknowledged_by_id = True, user.id
    services.check_reorder(db, p)
    audit(db, user, "stock_adjust", "spare_part", p.id, {"delta": body.delta, "reason": body.reason})
    db.commit()
    return part_out(p)
