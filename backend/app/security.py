"""Authentication & authorization: PBKDF2 password hashing, JWT tokens, role-based guards."""
import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import config
from .database import get_db
from .models import User, AuditLog

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


PASSWORD_RULE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,64}$")


def validate_password_strength(pw: str):
    if not PASSWORD_RULE.match(pw):
        raise HTTPException(422, "Password must be 8-64 characters and contain letters and numbers")


def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(minutes=config.ACCESS_TOKEN_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_exc = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                             headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
    except Exception:
        raise cred_exc
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise cred_exc
    return user


def require_roles(*roles: str):
    """Dependency factory: `Depends(require_roles('admin','plant_head'))`."""
    def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Role '{user.role}' is not allowed to perform this action")
        return user
    return _guard


# convenient role groups
ANY = ("admin", "plant_head", "technician", "operator")
MANAGERS = ("admin", "plant_head")
MAINT = ("admin", "plant_head", "technician")
FLOOR = ("admin", "plant_head", "operator")


def audit(db: Session, user: User | None, action: str, entity: str | None = None,
          entity_id=None, detail=None):
    db.add(AuditLog(user_id=user.id if user else None, action=action, entity=entity,
                    entity_id=str(entity_id) if entity_id is not None else None,
                    detail=json.dumps(detail, default=str) if isinstance(detail, (dict, list)) else detail))
