"""Audit logging for MSK policy enforcement decisions.

CRITICAL PRIVACY GUARANTEE:
This module logs minimal metadata (decision outcome, rule/reason ID, non-sensitive category metadata).
It NEVER stores or logs raw source code, secrets, API tokens, credentials, or full sensitive context.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from msk.config.loader import get_msk_dir
from msk.policy.models import PolicyAuditRecord, PolicyDecision, PolicyDecisionOutcome
from msk.storage.database import DatabaseManager

logger = logging.getLogger("msk.policy.audit")


class PolicyAuditor:
    """Records privacy-safe policy decision audit events."""

    def __init__(self, db: DatabaseManager | None = None, project_root: str | Path | None = None) -> None:
        self.db = db
        self.project_root = Path(project_root).resolve() if project_root else None

    def record_decision(
        self,
        project_id: str,
        decision: PolicyDecision,
        categories: list[str] | None = None,
        item_count: int = 0,
    ) -> PolicyAuditRecord:
        """Create and persist a policy audit record from a policy decision."""
        record_id = f"audit_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(UTC).isoformat()

        # Sanitize category names to prevent leaking filenames or code
        safe_categories = sorted(set(categories or decision.allowed_categories + decision.redacted_categories))

        record = PolicyAuditRecord(
            id=record_id,
            timestamp=now_iso,
            project_id=project_id,
            destination=decision.destination,
            provider=decision.provider,
            outcome=decision.outcome,
            reason_code=decision.reason_code,
            rule_id=decision.rule_id,
            categories=safe_categories,
            item_count=item_count,
            redactions_count=decision.redactions_count,
            explanation=decision.explanation,
        )

        self._persist_record(record)
        return record

    def _persist_record(self, record: PolicyAuditRecord) -> None:
        """Persist audit record to SQLite database and .msk/policy_audit.jsonl."""
        # 1. Persist to SQLite
        if self.db:
            try:
                with self.db.transaction() as conn:
                    conn.execute(
                        """
                        INSERT INTO policy_audit_logs (
                            id, project_id, timestamp, destination, provider,
                            outcome, reason_code, rule_id, categories_json,
                            items_count, redactions_count, explanation
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.id,
                            record.project_id,
                            record.timestamp,
                            record.destination,
                            record.provider,
                            record.outcome.value,
                            record.reason_code,
                            record.rule_id,
                            json.dumps(record.categories),
                            record.item_count,
                            record.redactions_count,
                            record.explanation,
                        ),
                    )
            except Exception as exc:
                logger.debug("Failed to write audit record to sqlite: %s", exc)

        # 2. Persist to .msk/policy_audit.jsonl
        if self.project_root:
            try:
                msk_dir = get_msk_dir(self.project_root)
                msk_dir.mkdir(parents=True, exist_ok=True)
                audit_file = msk_dir / "policy_audit.jsonl"
                with audit_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record.model_dump()) + "\n")
            except Exception as exc:
                logger.debug("Failed to write audit record to JSONL: %s", exc)

    def get_recent_records(
        self,
        limit: int = 50,
        project_id: str | None = None,
    ) -> list[PolicyAuditRecord]:
        """Retrieve recent policy audit records."""
        records: list[PolicyAuditRecord] = []

        if self.db:
            try:
                with self.db.transaction() as conn:
                    query = "SELECT * FROM policy_audit_logs"
                    params: list[str | int] = []
                    if project_id:
                        query += " WHERE project_id = ?"
                        params.append(project_id)
                    query += " ORDER BY timestamp DESC LIMIT ?"
                    params.append(limit)

                    rows = conn.execute(query, tuple(params)).fetchall()
                    for r in rows:
                        categories = json.loads(r["categories_json"]) if r["categories_json"] else []
                        records.append(
                            PolicyAuditRecord(
                                id=r["id"],
                                timestamp=r["timestamp"],
                                project_id=r["project_id"],
                                destination=r["destination"],
                                provider=r["provider"],
                                outcome=PolicyDecisionOutcome(r["outcome"]),
                                reason_code=r["reason_code"],
                                rule_id=r["rule_id"],
                                categories=categories,
                                item_count=r["items_count"],
                                redactions_count=r["redactions_count"],
                                explanation=r["explanation"],
                            )
                        )
                return records
            except Exception as exc:
                logger.debug("Failed to fetch audit records from sqlite: %s", exc)

        # Fallback to JSONL file
        if self.project_root:
            audit_file = get_msk_dir(self.project_root) / "policy_audit.jsonl"
            if audit_file.is_file():
                try:
                    lines = audit_file.read_text(encoding="utf-8").splitlines()
                    for line in reversed(lines[-limit:]):
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if project_id and data.get("project_id") != project_id:
                            continue
                        records.append(PolicyAuditRecord.model_validate(data))
                except Exception as exc:
                    logger.debug("Failed to read audit records from JSONL: %s", exc)

        return records
