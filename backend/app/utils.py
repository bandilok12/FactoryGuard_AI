"""Small shared helpers (plant-local clock, shift resolution, serialization)."""
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

from .config import PLANT_TZ


def now_local() -> datetime:
    """Naive datetime in plant local time (all timestamps are stored in plant time)."""
    return datetime.now(ZoneInfo(PLANT_TZ)).replace(tzinfo=None, microsecond=0)


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def shift_for(dt: datetime, shifts) -> object | None:
    """Return the Shift whose window contains dt (handles overnight shifts)."""
    t = dt.time()
    for s in shifts:
        a, b = _t(s.start_time), _t(s.end_time)
        if (a <= b and a <= t < b) or (a > b and (t >= a or t < b)):
            return s
    return None


def production_date(dt: datetime, shift) -> date:
    """Night shift that crosses midnight belongs to the previous calendar day."""
    if shift and _t(shift.start_time) > _t(shift.end_time) and dt.time() < _t(shift.end_time):
        from datetime import timedelta
        return (dt - timedelta(days=1)).date()
    return dt.date()


def to_dict(obj, exclude=("password_hash",)) -> dict:
    if obj is None:
        return None
    out = {}
    for c in obj.__table__.columns:
        if c.name in exclude:
            continue
        v = getattr(obj, c.name)
        out[c.name] = v.isoformat() if isinstance(v, (datetime, date)) else v
    return out
