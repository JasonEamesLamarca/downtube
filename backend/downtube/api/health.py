from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from downtube import __version__
from downtube.db import read_session
from downtube.schemas import Health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health() -> Health:
    db_ok = True
    try:
        with read_session() as s:
            s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    # worker/scheduler/archive checks are wired in by the backend + download modules.
    from downtube import runtime_status

    st = runtime_status.snapshot()
    ok = db_ok and st["worker"] and st["scheduler"] and st["archive_writable"]
    return Health(status="ok" if ok else "degraded", version=__version__, db=db_ok, **st)
