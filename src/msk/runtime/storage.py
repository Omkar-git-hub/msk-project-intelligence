"""Project-specific persistence and retention management for Runtime Intelligence."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from msk.knowledge.models import make_entity_id
from msk.runtime.models import (
    RuntimeErrorGroup,
    RuntimeEvent,
    RuntimeEventType,
    RuntimeLogLevel,
    RuntimeStatusSummary,
    StackTrace,
)
from msk.storage.database import DatabaseManager


class RuntimeStorage:
    """Stores and queries runtime events, error groups, and observations."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def save_events(self, events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        """Persist parsed runtime events and update aggregated error groups."""
        if not events:
            return []

        with self.db.transaction() as conn:
            for ev in events:
                # 1. Insert into runtime_events
                stack_json = ev.stack_trace.model_dump_json() if ev.stack_trace else None
                attrs_json = json.dumps(ev.attributes) if ev.attributes else None

                conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_events (
                        id, project_id, timestamp, level, source, message, event_type,
                        file_path, line_number, symbol_name, exception_type,
                        stack_trace_json, attributes_json, fingerprint, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.id,
                        ev.project_id,
                        ev.timestamp,
                        ev.level,
                        ev.source,
                        ev.message,
                        ev.event_type,
                        ev.file_path,
                        ev.line_number,
                        ev.symbol_name,
                        ev.exception_type,
                        stack_json,
                        attrs_json,
                        ev.fingerprint,
                        ev.created_at,
                    ),
                )

                # 2. If event is an error or exception, update runtime_errors aggregate
                is_error_event = (
                    ev.event_type == RuntimeEventType.EXCEPTION.value
                    or ev.level in (RuntimeLogLevel.ERROR.value, RuntimeLogLevel.CRITICAL.value)
                    or ev.exception_type is not None
                )

                if is_error_event:
                    cur = conn.execute(
                        "SELECT id, occurrence_count, first_seen FROM runtime_errors WHERE fingerprint = ? AND project_id = ?",
                        (ev.fingerprint, ev.project_id),
                    )
                    row = cur.fetchone()

                    if row:
                        conn.execute(
                            """
                            UPDATE runtime_errors
                            SET occurrence_count = occurrence_count + 1,
                                last_seen = ?,
                                sample_event_id = ?,
                                resolved = 0
                            WHERE fingerprint = ? AND project_id = ?
                            """,
                            (ev.timestamp, ev.id, ev.fingerprint, ev.project_id),
                        )
                    else:
                        err_id = make_entity_id("rterr", ev.project_id, ev.fingerprint)
                        exc_type = ev.exception_type or (
                            ev.stack_trace.exception_type if ev.stack_trace else ev.level
                        )
                        conn.execute(
                            """
                            INSERT INTO runtime_errors (
                                id, project_id, fingerprint, exception_type, message_template,
                                first_seen, last_seen, occurrence_count, severity,
                                file_path, line_number, symbol_name, sample_event_id, resolved
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 0)
                            """,
                            (
                                err_id,
                                ev.project_id,
                                ev.fingerprint,
                                exc_type,
                                ev.message,
                                ev.timestamp,
                                ev.timestamp,
                                ev.level,
                                ev.file_path,
                                ev.line_number,
                                ev.symbol_name,
                                ev.id,
                            ),
                        )
        return events

    def get_events(
        self,
        project_id: str,
        limit: int = 50,
        level: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
    ) -> list[RuntimeEvent]:
        """Query runtime events ordered by timestamp descending."""
        query = "SELECT * FROM runtime_events WHERE project_id = ?"
        params: list[Any] = [project_id]

        if level:
            query += " AND level = ?"
            params.append(level.upper())
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.lower())
        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(max(1, limit))

        with self.db.transaction() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        events: list[RuntimeEvent] = []
        for r in rows:
            st = StackTrace.model_validate_json(r["stack_trace_json"]) if r["stack_trace_json"] else None
            attrs = json.loads(r["attributes_json"]) if r["attributes_json"] else {}
            events.append(
                RuntimeEvent(
                    id=r["id"],
                    project_id=r["project_id"],
                    timestamp=r["timestamp"],
                    level=r["level"],
                    source=r["source"],
                    message=r["message"],
                    event_type=r["event_type"],
                    file_path=r["file_path"],
                    line_number=r["line_number"],
                    symbol_name=r["symbol_name"],
                    exception_type=r["exception_type"],
                    stack_trace=st,
                    attributes=attrs,
                    fingerprint=r["fingerprint"],
                    created_at=r["created_at"],
                )
            )
        return events

    def get_event_by_id(self, project_id: str, event_id: str) -> RuntimeEvent | None:
        """Fetch a specific runtime event by its ID."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_events WHERE id = ? AND project_id = ? LIMIT 1",
                (event_id, project_id),
            ).fetchone()

        if not row:
            return None

        st = StackTrace.model_validate_json(row["stack_trace_json"]) if row["stack_trace_json"] else None
        attrs = json.loads(row["attributes_json"]) if row["attributes_json"] else {}
        return RuntimeEvent(
            id=row["id"],
            project_id=row["project_id"],
            timestamp=row["timestamp"],
            level=row["level"],
            source=row["source"],
            message=row["message"],
            event_type=row["event_type"],
            file_path=row["file_path"],
            line_number=row["line_number"],
            symbol_name=row["symbol_name"],
            exception_type=row["exception_type"],
            stack_trace=st,
            attributes=attrs,
            fingerprint=row["fingerprint"],
            created_at=row["created_at"],
        )

    def get_errors(
        self,
        project_id: str,
        unresolved_only: bool = False,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[RuntimeErrorGroup]:
        """Retrieve aggregated error groups."""
        query = "SELECT * FROM runtime_errors WHERE project_id = ?"
        params: list[Any] = [project_id]

        if unresolved_only:
            query += " AND resolved = 0"
        if severity:
            query += " AND severity = ?"
            params.append(severity.upper())

        query += " ORDER BY occurrence_count DESC, last_seen DESC LIMIT ?"
        params.append(max(1, limit))

        with self.db.transaction() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        return [
            RuntimeErrorGroup(
                id=r["id"],
                project_id=r["project_id"],
                fingerprint=r["fingerprint"],
                exception_type=r["exception_type"],
                message_template=r["message_template"],
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                occurrence_count=r["occurrence_count"],
                severity=r["severity"],
                file_path=r["file_path"],
                line_number=r["line_number"],
                symbol_name=r["symbol_name"],
                sample_event_id=r["sample_event_id"],
                resolved=bool(r["resolved"]),
            )
            for r in rows
        ]

    def get_error_by_id_or_fingerprint(
        self, project_id: str, identifier: str
    ) -> RuntimeErrorGroup | None:
        """Fetch error group by error ID or fingerprint."""
        with self.db.transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM runtime_errors
                WHERE project_id = ? AND (id = ? OR fingerprint = ?)
                LIMIT 1
                """,
                (project_id, identifier, identifier),
            ).fetchone()

        if not row:
            return None

        return RuntimeErrorGroup(
            id=row["id"],
            project_id=row["project_id"],
            fingerprint=row["fingerprint"],
            exception_type=row["exception_type"],
            message_template=row["message_template"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            occurrence_count=row["occurrence_count"],
            severity=row["severity"],
            file_path=row["file_path"],
            line_number=row["line_number"],
            symbol_name=row["symbol_name"],
            sample_event_id=row["sample_event_id"],
            resolved=bool(row["resolved"]),
        )

    def resolve_error(self, project_id: str, identifier: str) -> bool:
        """Mark an error group as resolved."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE runtime_errors
                SET resolved = 1
                WHERE project_id = ? AND (id = ? OR fingerprint = ?)
                """,
                (project_id, identifier, identifier),
            )
            return cur.rowcount > 0

    def prune_events(
        self,
        project_id: str,
        max_events: int = 1000,
        retention_days: int = 30,
    ) -> int:
        """Prune older events exceeding retention days or max event count.

        Preserves error group summaries.
        """
        pruned_count = 0
        now = datetime.now(UTC)
        cutoff_date = (now - timedelta(days=max(1, retention_days))).isoformat()

        with self.db.transaction() as conn:
            # 1. Prune by retention date
            cur1 = conn.execute(
                "DELETE FROM runtime_events WHERE project_id = ? AND timestamp < ?",
                (project_id, cutoff_date),
            )
            pruned_count += cur1.rowcount

            # 2. Prune excess events beyond max_events
            row = conn.execute(
                "SELECT COUNT(*) as total FROM runtime_events WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            total = row["total"] if row else 0

            if total > max_events:
                excess = total - max_events
                # Delete oldest excess events
                cur2 = conn.execute(
                    """
                    DELETE FROM runtime_events
                    WHERE id IN (
                        SELECT id FROM runtime_events
                        WHERE project_id = ?
                        ORDER BY timestamp ASC
                        LIMIT ?
                    )
                    """,
                    (project_id, excess),
                )
                pruned_count += cur2.rowcount

        return pruned_count

    def get_status_summary(self, project_id: str, project_name: str) -> RuntimeStatusSummary:
        """Calculate summary statistics for status commands."""
        with self.db.transaction() as conn:
            row_events = conn.execute(
                "SELECT COUNT(*) as cnt, MAX(timestamp) as last_ts FROM runtime_events WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            total_events = row_events["cnt"] if row_events else 0
            last_ts = row_events["last_ts"] if row_events else None

            row_errors = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as unresolved FROM runtime_errors WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            total_errors = row_errors["total"] if row_errors and row_errors["total"] else 0
            unresolved = row_errors["unresolved"] if row_errors and row_errors["unresolved"] else 0

            row_correlated = conn.execute(
                """
                SELECT COUNT(DISTINCT file_path) as files_cnt,
                       COUNT(DISTINCT symbol_name) as symbols_cnt
                FROM runtime_events
                WHERE project_id = ? AND file_path IS NOT NULL
                """,
                (project_id,),
            ).fetchone()
            files_cnt = row_correlated["files_cnt"] if row_correlated else 0
            symbols_cnt = row_correlated["symbols_cnt"] if row_correlated else 0

        top_errors = self.get_errors(project_id=project_id, limit=5)

        return RuntimeStatusSummary(
            project_name=project_name,
            total_events=total_events,
            total_errors=total_errors,
            total_unresolved_errors=unresolved,
            last_event_timestamp=last_ts,
            top_errors=top_errors,
            correlated_files_count=files_cnt,
            correlated_symbols_count=symbols_cnt,
        )

    def clear_runtime_data(self, project_id: str) -> None:
        """Delete all runtime records for a specific project."""
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM runtime_events WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM runtime_errors WHERE project_id = ?", (project_id,))
