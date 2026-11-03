"""High-level Runtime Intelligence Engine orchestrating ingestion, analysis, and correlation."""

from pathlib import Path

from msk.common.errors import MSKError
from msk.knowledge.repository import KnowledgeRepository
from msk.runtime.correlator import RuntimeCorrelator
from msk.runtime.impact import RuntimeImpactAnalysisReport, RuntimeImpactEngine
from msk.runtime.models import (
    RuntimeCorrelationReport,
    RuntimeErrorGroup,
    RuntimeEvent,
    RuntimeStatusSummary,
)
from msk.runtime.parser import RuntimeLogParser
from msk.runtime.sanitizer import RuntimeSanitizer
from msk.runtime.storage import RuntimeStorage
from msk.storage.database import DatabaseManager


class RuntimeIntelligenceEngine:
    """Unified engine for MSK local-first runtime intelligence."""

    def __init__(
        self,
        db: DatabaseManager,
        repo: KnowledgeRepository,
        project_id: str,
        project_root: Path,
        project_name: str = "Unknown",
    ) -> None:
        self.db = db
        self.repo = repo
        self.project_id = project_id
        self.project_root = Path(project_root).resolve()
        self.project_name = project_name

        self.sanitizer = RuntimeSanitizer(project_root=self.project_root)
        self.parser = RuntimeLogParser(project_id=self.project_id, project_root=self.project_root)
        self.storage = RuntimeStorage(self.db)
        self.correlator = RuntimeCorrelator(
            repo=self.repo,
            project_id=self.project_id,
            project_root=self.project_root,
        )
        self.impact_engine = RuntimeImpactEngine(
            repo=self.repo,
            project_id=self.project_id,
            project_root=self.project_root,
        )

    def ingest_logs(
        self,
        text: str,
        source: str | None = None,
        save: bool = True,
    ) -> list[RuntimeEvent]:
        """Parse raw log text, scrub sensitive patterns, and optionally persist."""
        events = self.parser.parse_text(text, source=source)
        if save and events:
            self.storage.save_events(events)
        return events

    def ingest_log_file(
        self,
        file_path: Path | str,
        source: str | None = None,
        save: bool = True,
    ) -> list[RuntimeEvent]:
        """Read and ingest application log file safely."""
        p = Path(file_path)
        resolved = p if p.is_absolute() else (self.project_root / p)

        if not resolved.is_file():
            raise MSKError(
                f"Log file not found: '{file_path}'.",
                hint="Verify that the path to the log file is correct and accessible.",
            )

        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise MSKError(f"Failed to read log file '{file_path}': {exc}") from exc

        log_source = source or p.name
        return self.ingest_logs(content, source=log_source, save=save)

    def analyze_runtime_error(
        self,
        error_text: str,
        source: str | None = None,
        save: bool = True,
    ) -> tuple[RuntimeEvent, RuntimeImpactAnalysisReport]:
        """Analyze a runtime exception, correlating with static code and assessing impact."""
        events = self.ingest_logs(error_text, source=source, save=save)
        if not events:
            raise MSKError(
                "Unable to parse runtime error or stack trace from input.",
                hint="Provide a valid error message, stack trace, or log line.",
            )

        # Primary error event is either the first exception or the first event
        primary_event = next(
            (ev for ev in events if ev.event_type == "exception"), events[0]
        )
        impact_report = self.impact_engine.analyze_event_impact(primary_event)
        return primary_event, impact_report

    def correlate_event(self, event_id: str) -> RuntimeCorrelationReport:
        """Correlate a stored runtime event with the static knowledge model."""
        ev = self.storage.get_event_by_id(self.project_id, event_id)
        if not ev:
            raise MSKError(
                f"Runtime event not found: '{event_id}'.",
                hint="Run 'msk runtime history' to inspect recorded event IDs.",
            )
        return self.correlator.correlate_event(ev)

    def correlate_error(self, identifier: str) -> RuntimeCorrelationReport:
        """Correlate a runtime error group with the static knowledge model."""
        group = self.storage.get_error_by_id_or_fingerprint(self.project_id, identifier)
        if not group:
            raise MSKError(
                f"Runtime error not found for identifier: '{identifier}'.",
                hint="Run 'msk runtime errors' to see available error IDs and fingerprints.",
            )
        return self.correlator.correlate_error_group(group)

    def analyze_impact(
        self,
        identifier: str,
        max_depth: int = 3,
    ) -> RuntimeImpactAnalysisReport:
        """Evaluate downstream blast radius for an error group or event ID."""
        group = self.storage.get_error_by_id_or_fingerprint(self.project_id, identifier)
        if group:
            return self.impact_engine.analyze_error_group_impact(group, max_depth=max_depth)

        event = self.storage.get_event_by_id(self.project_id, identifier)
        if event:
            return self.impact_engine.analyze_event_impact(event, max_depth=max_depth)

        raise MSKError(
            f"No runtime error or event found matching identifier '{identifier}'.",
            hint="Run 'msk runtime errors' or 'msk runtime history' to inspect valid identifiers.",
        )

    def get_errors(
        self,
        unresolved_only: bool = False,
        severity: str | None = None,
        limit: int = 50,
    ) -> list[RuntimeErrorGroup]:
        """List observed runtime errors."""
        return self.storage.get_errors(
            self.project_id,
            unresolved_only=unresolved_only,
            severity=severity,
            limit=limit,
        )

    def get_events(
        self,
        limit: int = 50,
        level: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
    ) -> list[RuntimeEvent]:
        """List runtime events history."""
        return self.storage.get_events(
            self.project_id,
            limit=limit,
            level=level,
            event_type=event_type,
            since=since,
        )

    def resolve_error(self, identifier: str) -> bool:
        """Mark an error as resolved."""
        return self.storage.resolve_error(self.project_id, identifier)

    def prune(self, max_events: int = 1000, retention_days: int = 30) -> int:
        """Prune older events exceeding retention policy."""
        return self.storage.prune_events(
            self.project_id,
            max_events=max_events,
            retention_days=retention_days,
        )

    def get_status(self) -> RuntimeStatusSummary:
        """Fetch runtime intelligence summary statistics."""
        return self.storage.get_status_summary(self.project_id, self.project_name)
