from fastapi import HTTPException, Query
from sqlalchemy.orm import Session


def get_or_404(db: Session, model, obj_id, label=None):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, f"{label or model.__name__} {obj_id} not found")
    return obj


class Page:
    def __init__(self, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        self.limit, self.offset = limit, offset
