"""SQLite engine (WAL) with a single-writer strategy.

All writes go through :func:`write_session`, which serialises writers inside this
process with an RLock (DownTube runs as a single process). Readers use
:func:`read_session` and never block writers thanks to WAL.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None
_write_lock = threading.RLock()


def _on_connect(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_engine(url: str | None = None, db_path: Path | None = None) -> Engine:
    """Create (or replace) the global engine. ``url`` wins over ``db_path``."""
    global _engine, _Session
    if url is None:
        assert db_path is not None
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
    _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)
    event.listen(_engine, "connect", _on_connect)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


def _factory() -> sessionmaker[Session]:
    if _Session is None:
        raise RuntimeError("database engine not initialised")
    return _Session


@contextmanager
def read_session() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
    finally:
        s.close()  # no rollback: keep loaded attributes usable after the block (expire_on_commit=False)


@contextmanager
def write_session() -> Iterator[Session]:
    """Serialised read-write transaction; commits on success, rolls back on error."""
    with _write_lock:
        s = _factory()()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a read-write session for the request (serialised commit)."""
    s = _factory()()
    try:
        yield s
        if s.new or s.dirty or s.deleted:
            with _write_lock:
                s.commit()
        else:
            s.rollback()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


def write_lock() -> threading.RLock:
    return _write_lock
