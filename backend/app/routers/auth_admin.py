"""Auth, users, shifts, roster (operator management), settings, audit log."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models as M, schemas as S
from ..database import get_db
from ..security import (hash_password, verify_password, create_access_token, get_current_user, require_roles,
                        validate_password_strength, audit, ANY, MANAGERS)
from ..services import DEFAULT_SETTINGS
from ..utils import to_dict, now_local
from .. import config
from .common import get_or_404, Page

router = APIRouter()


# =========================================================================== AUTH
def _login(db: Session, username: str, password: str):
    user = db.scalar(select(M.User).where(M.User.username == username.strip().lower()))
    now = now_local()
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(423, f"Account locked after repeated failures. Try again after {user.locked_until:%H:%M}")
    if not user or not verify_password(password, user.password_hash):
        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= config.LOGIN_MAX_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=config.LOGIN_LOCK_MINUTES)
                user.failed_attempts = 0
            audit(db, user, "login_failed", "user", user.id)
            db.commit()
        raise HTTPException(401, "Invalid username or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled. Contact admin.")
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login = now
    audit(db, user, "login", "user", user.id)
    db.commit()
    return {"access_token": create_access_token(user), "token_type": "bearer",
            "user": to_dict(user), "expires_in_minutes": config.ACCESS_TOKEN_MINUTES}


@router.post("/auth/login", tags=["Auth"], summary="Login (JSON) → JWT")
def login(body: S.LoginIn, db: Session = Depends(get_db)):
    return _login(db, body.username, body.password)


@router.post("/auth/token", tags=["Auth"], summary="OAuth2 password flow (used by Swagger 'Authorize')")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return _login(db, form.username, form.password)


@router.get("/auth/me", tags=["Auth"])
def me(user: M.User = Depends(get_current_user)):
    return to_dict(user)


@router.post("/auth/change-password", tags=["Auth"])
def change_password(body: S.PasswordChange, user: M.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    validate_password_strength(body.new_password)
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "password_change", "user", user.id)
    db.commit()
    return {"ok": True}


# =========================================================================== USERS
@router.get("/users", tags=["Users"])
def list_users(role: str | None = None, active: bool | None = None, db: Session = Depends(get_db),
               _: M.User = Depends(require_roles(*MANAGERS, "technician"))):
    q = select(M.User).order_by(M.User.role, M.User.full_name)
    if role:
        q = q.where(M.User.role == role)
    if active is not None:
        q = q.where(M.User.is_active == active)
    return [to_dict(u) for u in db.scalars(q)]


@router.post("/users", tags=["Users"], status_code=201)
def create_user(body: S.UserCreate, db: Session = Depends(get_db), admin: M.User = Depends(require_roles("admin"))):
    validate_password_strength(body.password)
    u = M.User(username=body.username.lower(), full_name=body.full_name, email=body.email, phone=body.phone,
               role=body.role, password_hash=hash_password(body.password))
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists")
    audit(db, admin, "create", "user", u.id, {"username": u.username, "role": u.role})
    db.commit()
    return to_dict(u)


@router.patch("/users/{user_id}", tags=["Users"])
def update_user(user_id: int, body: S.UserUpdate, db: Session = Depends(get_db),
                admin: M.User = Depends(require_roles("admin"))):
    u = get_or_404(db, M.User, user_id, "User")
    data = body.model_dump(exclude_unset=True)
    if u.id == admin.id and (data.get("is_active") is False or data.get("role", "admin") != "admin"):
        raise HTTPException(400, "You cannot deactivate or demote your own account")
    if "password" in data:
        validate_password_strength(data["password"])
        u.password_hash = hash_password(data.pop("password"))
    for k, v in data.items():
        setattr(u, k, v)
    try:
        audit(db, admin, "update", "user", u.id, {k: v for k, v in data.items()})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email already in use")
    return to_dict(u)


@router.delete("/users/{user_id}", tags=["Users"], summary="Deactivate user (soft delete)")
def deactivate_user(user_id: int, db: Session = Depends(get_db), admin: M.User = Depends(require_roles("admin"))):
    u = get_or_404(db, M.User, user_id, "User")
    if u.id == admin.id:
        raise HTTPException(400, "You cannot deactivate yourself")
    u.is_active = False
    audit(db, admin, "deactivate", "user", u.id)
    db.commit()
    return {"ok": True}


# =========================================================================== SHIFTS
@router.get("/shifts", tags=["Shifts & Roster"])
def list_shifts(db: Session = Depends(get_db), _: M.User = Depends(get_current_user)):
    out = []
    for s in db.scalars(select(M.Shift).order_by(M.Shift.start_time)):
        d = to_dict(s)
        d["supervisor_name"] = s.supervisor.full_name if s.supervisor else None
        out.append(d)
    return out


@router.post("/shifts", tags=["Shifts & Roster"], status_code=201)
def create_shift(body: S.ShiftIn, db: Session = Depends(get_db), admin: M.User = Depends(require_roles("admin"))):
    s = M.Shift(**body.model_dump())
    db.add(s)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Shift code already exists")
    audit(db, admin, "create", "shift", s.id)
    db.commit()
    return to_dict(s)


@router.put("/shifts/{shift_id}", tags=["Shifts & Roster"])
def update_shift(shift_id: int, body: S.ShiftIn, db: Session = Depends(get_db),
                 admin: M.User = Depends(require_roles("admin"))):
    s = get_or_404(db, M.Shift, shift_id, "Shift")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, admin, "update", "shift", s.id)
    db.commit()
    return to_dict(s)


@router.get("/roster", tags=["Shifts & Roster"], summary="Operator/technician shift assignments")
def roster(work_date: date | None = None, user_id: int | None = None, db: Session = Depends(get_db),
           user: M.User = Depends(get_current_user)):
    q = select(M.ShiftAssignment).order_by(M.ShiftAssignment.work_date.desc())
    if work_date:
        q = q.where(M.ShiftAssignment.work_date == work_date)
    if user.role in ("operator", "technician"):
        q = q.where(M.ShiftAssignment.user_id == user.id)
    elif user_id:
        q = q.where(M.ShiftAssignment.user_id == user_id)
    out = []
    for a in db.scalars(q.limit(500)):
        d = to_dict(a)
        d.update(user_name=a.user.full_name, role=a.user.role, shift_code=a.shift.code,
                 machine_code=a.machine.code if a.machine else None)
        out.append(d)
    return out


@router.post("/roster", tags=["Shifts & Roster"], status_code=201)
def assign(body: S.AssignmentIn, db: Session = Depends(get_db), mgr: M.User = Depends(require_roles(*MANAGERS))):
    u = get_or_404(db, M.User, body.user_id, "User")
    if u.role not in ("operator", "technician"):
        raise HTTPException(422, "Only operators and technicians can be rostered")
    get_or_404(db, M.Shift, body.shift_id, "Shift")
    if body.machine_id:
        get_or_404(db, M.Machine, body.machine_id, "Machine")
    existing = db.scalar(select(M.ShiftAssignment).where(M.ShiftAssignment.user_id == body.user_id,
                                                         M.ShiftAssignment.work_date == body.work_date))
    if existing:   # upsert: re-assign the day
        existing.shift_id, existing.machine_id = body.shift_id, body.machine_id
        a = existing
    else:
        a = M.ShiftAssignment(**body.model_dump())
        db.add(a)
    db.flush()
    audit(db, mgr, "assign", "roster", a.id, body.model_dump())
    db.commit()
    return to_dict(a)


@router.delete("/roster/{assignment_id}", tags=["Shifts & Roster"])
def unassign(assignment_id: int, db: Session = Depends(get_db), mgr: M.User = Depends(require_roles(*MANAGERS))):
    a = get_or_404(db, M.ShiftAssignment, assignment_id, "Assignment")
    db.delete(a)
    audit(db, mgr, "unassign", "roster", assignment_id)
    db.commit()
    return {"ok": True}


# =========================================================================== SETTINGS & AUDIT
@router.get("/settings", tags=["Admin"])
def get_settings(db: Session = Depends(get_db), _: M.User = Depends(require_roles(*MANAGERS))):
    stored = {s.key: s for s in db.scalars(select(M.Setting))}
    return [{"key": k, "value": stored[k].value if k in stored else v, "description": d}
            for k, (v, d) in DEFAULT_SETTINGS.items()]


@router.put("/settings/{key}", tags=["Admin"])
def put_setting(key: str, body: S.SettingIn, db: Session = Depends(get_db),
                admin: M.User = Depends(require_roles("admin"))):
    if key not in DEFAULT_SETTINGS:
        raise HTTPException(404, "Unknown setting")
    if key.endswith("threshold"):
        try:
            v = float(body.value)
            assert 0 < v < 1
        except Exception:
            raise HTTPException(422, "Threshold must be a number between 0 and 1")
    s = db.get(M.Setting, key) or M.Setting(key=key, description=DEFAULT_SETTINGS[key][1])
    s.value = body.value
    db.add(s)
    audit(db, admin, "setting", "settings", key, body.value)
    db.commit()
    return {"key": key, "value": s.value}


@router.get("/audit-logs", tags=["Admin"])
def audit_logs(page: Page = Depends(), db: Session = Depends(get_db), _: M.User = Depends(require_roles("admin"))):
    rows = db.scalars(select(M.AuditLog).order_by(M.AuditLog.created_at.desc()).limit(page.limit).offset(page.offset))
    return [{**to_dict(a), "username": a.user.username if a.user else None} for a in rows]
