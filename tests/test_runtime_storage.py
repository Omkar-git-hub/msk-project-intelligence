"""Tests for MSK Runtime Storage and retention management."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from msk.knowledge.models import ProjectEntity
from msk.knowledge.repository import KnowledgeRepository
from msk.runtime.models import (
    RuntimeEvent,
    RuntimeEventType,
    RuntimeLogLevel,
    StackFrame,
    StackTrace,
)
from msk.runtime.storage import RuntimeStorage
from msk.storage.database import DatabaseManager


def _setup_test_db(tmp_path: Path) -> tuple[DatabaseManager, str]:
    db_path = tmp_path / "test.db"
    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)
    proj_id = "proj_alpha"
    repo.save_project(
        ProjectEntity(
            id=proj_id,
            name="AlphaProject",
            root_path=str(tmp_path),
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
    )
    return db, proj_id


def test_save_and_retrieve_events(tmp_path: Path):
    db, proj_id = _setup_test_db(tmp_path)
    storage = RuntimeStorage(db)

    now = datetime.now(UTC).isoformat()
    event = RuntimeEvent(
        id="rte_001",
        project_id=proj_id,
        timestamp=now,
        level=RuntimeLogLevel.INFO.value,
        message="Service started successfully",
        event_type=RuntimeEventType.LOG.value,
        fingerprint="fp_service_started",
        created_at=now,
    )
    storage.save_events([event])

    retrieved = storage.get_events(proj_id, limit=10)
    assert len(retrieved) == 1
    assert retrieved[0].id == "rte_001"
    assert retrieved[0].message == "Service started successfully"


def test_error_group_aggregation(tmp_path: Path):
    db, proj_id = _setup_test_db(tmp_path)
    storage = RuntimeStorage(db)

    t1 = "2026-11-03T10:00:00Z"
    t2 = "2026-11-03T10:05:00Z"

    ev1 = RuntimeEvent(
        id="rte_err_1",
        project_id=proj_id,
        timestamp=t1,
        level=RuntimeLogLevel.ERROR.value,
        message="Connection reset by peer",
        event_type=RuntimeEventType.EXCEPTION.value,
        exception_type="ConnectionResetError",
        file_path="src/network.py",
        line_number=45,
        symbol_name="send_payload",
        fingerprint="fp_conn_reset",
        created_at=t1,
    )
    ev2 = RuntimeEvent(
        id="rte_err_2",
        project_id=proj_id,
        timestamp=t2,
        level=RuntimeLogLevel.ERROR.value,
        message="Connection reset by peer",
        event_type=RuntimeEventType.EXCEPTION.value,
        exception_type="ConnectionResetError",
        file_path="src/network.py",
        line_number=45,
        symbol_name="send_payload",
        fingerprint="fp_conn_reset",
        created_at=t2,
    )

    storage.save_events([ev1])
    storage.save_events([ev2])

    errors = storage.get_errors(proj_id)
    assert len(errors) == 1
    err = errors[0]
    assert err.occurrence_count == 2
    assert err.first_seen == t1
    assert err.last_seen == t2
    assert err.resolved is False


def test_resolve_error(tmp_path: Path):
    db, proj_id = _setup_test_db(tmp_path)
    storage = RuntimeStorage(db)

    now = datetime.now(UTC).isoformat()
    ev = RuntimeEvent(
        id="rte_err_3",
        project_id=proj_id,
        timestamp=now,
        level=RuntimeLogLevel.ERROR.value,
        message="Null reference",
        event_type=RuntimeEventType.EXCEPTION.value,
        exception_type="NullReference",
        fingerprint="fp_null_ref",
        created_at=now,
    )
    storage.save_events([ev])

    errors = storage.get_errors(proj_id, unresolved_only=True)
    assert len(errors) == 1

    ok = storage.resolve_error(proj_id, "fp_null_ref")
    assert ok is True

    unresolved = storage.get_errors(proj_id, unresolved_only=True)
    assert len(unresolved) == 0

    all_errors = storage.get_errors(proj_id, unresolved_only=False)
    assert len(all_errors) == 1
    assert all_errors[0].resolved is True


def test_stack_trace_roundtrip(tmp_path: Path):
    db, proj_id = _setup_test_db(tmp_path)
    storage = RuntimeStorage(db)

    st = StackTrace(
        exception_type="KeyError",
        message="Missing user key",
        frames=[
            StackFrame(
                file_path="src/auth.py",
                line_number=30,
                function_name="get_user",
                is_project_file=True,
            )
        ],
    )
    now = datetime.now(UTC).isoformat()
    ev = RuntimeEvent(
        id="rte_trace_1",
        project_id=proj_id,
        timestamp=now,
        level=RuntimeLogLevel.ERROR.value,
        message="KeyError: Missing user key",
        event_type=RuntimeEventType.EXCEPTION.value,
        exception_type="KeyError",
        file_path="src/auth.py",
        line_number=30,
        symbol_name="get_user",
        stack_trace=st,
        fingerprint="fp_key_error",
        created_at=now,
    )
    storage.save_events([ev])

    fetched = storage.get_event_by_id(proj_id, "rte_trace_1")
    assert fetched is not None
    assert fetched.stack_trace is not None
    assert fetched.stack_trace.exception_type == "KeyError"
    assert len(fetched.stack_trace.frames) == 1
    assert fetched.stack_trace.frames[0].function_name == "get_user"


def test_prune_events(tmp_path: Path):
    db, proj_id = _setup_test_db(tmp_path)
    storage = RuntimeStorage(db)

    now = datetime.now(UTC)
    old_time = (now - timedelta(days=40)).isoformat()
    recent_time = now.isoformat()

    ev_old = RuntimeEvent(
        id="rte_old",
        project_id=proj_id,
        timestamp=old_time,
        level=RuntimeLogLevel.INFO.value,
        message="Old log record",
        fingerprint="fp_old",
        created_at=old_time,
    )
    ev_new = RuntimeEvent(
        id="rte_new",
        project_id=proj_id,
        timestamp=recent_time,
        level=RuntimeLogLevel.INFO.value,
        message="New log record",
        fingerprint="fp_new",
        created_at=recent_time,
    )

    storage.save_events([ev_old, ev_new])
    pruned = storage.prune_events(proj_id, retention_days=30)
    assert pruned == 1

    remaining = storage.get_events(proj_id)
    assert len(remaining) == 1
    assert remaining[0].id == "rte_new"


def test_project_isolation(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)

    now = datetime.now(UTC).isoformat()
    repo.save_project(ProjectEntity(id="proj_1", name="P1", root_path=str(tmp_path / "1"), created_at=now, updated_at=now))
    repo.save_project(ProjectEntity(id="proj_2", name="P2", root_path=str(tmp_path / "2"), created_at=now, updated_at=now))

    storage = RuntimeStorage(db)
    ev1 = RuntimeEvent(
        id="rte_p1", project_id="proj_1", timestamp=now, level="INFO", message="Message for P1", fingerprint="fp1", created_at=now
    )
    ev2 = RuntimeEvent(
        id="rte_p2", project_id="proj_2", timestamp=now, level="INFO", message="Message for P2", fingerprint="fp2", created_at=now
    )
    storage.save_events([ev1, ev2])

    p1_events = storage.get_events("proj_1")
    p2_events = storage.get_events("proj_2")

    assert len(p1_events) == 1
    assert p1_events[0].id == "rte_p1"
    assert len(p2_events) == 1
    assert p2_events[0].id == "rte_p2"
