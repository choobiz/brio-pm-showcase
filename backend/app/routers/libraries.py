"""Libraries landing page (R2Libraries). Office only, read-only."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_office
from app.db import get_db
from app.services import libraries as svc

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("/overview")
def libraries_overview(db: Session = Depends(get_db), _: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    return svc.overview(db)
