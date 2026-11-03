"""Runtime Impact Analysis Engine for MSK."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from msk.impact.engine import ImpactEngine
from msk.knowledge.models import FileEntity, SymbolEntity, make_entity_id
from msk.knowledge.repository import KnowledgeRepository
from msk.runtime.correlator import RuntimeCorrelator
from msk.runtime.models import (
    RuntimeErrorGroup,
    RuntimeEvent,
    RuntimeObservedFacts,
)


class RuntimeImpactPossibility(BaseModel):
    """An inferred potential consequence of a runtime failure."""

    name: str
    item_type: str  # symbol, file, api_endpoint, test
    category: str
    classification: str  # direct, transitive
    depth: int
    reason: str
    file_path: str | None = None


class RuntimeImpactAnalysisReport(BaseModel):
    """Complete runtime impact report cleanly separating empirical facts from static inferences."""

    report_id: str
    project_id: str
    fingerprint: str
    observed_facts: RuntimeObservedFacts
    direct_impacts: list[RuntimeImpactPossibility] = Field(default_factory=list)
    transitive_impacts: list[RuntimeImpactPossibility] = Field(default_factory=list)
    impacted_apis: list[str] = Field(default_factory=list)
    recommended_tests: list[str] = Field(default_factory=list)
    total_blast_radius: int = 0
    investigation_checklist: list[str] = Field(default_factory=list)
    analyzed_at: str


class RuntimeImpactEngine:
    """Calculates downstream blast radius of runtime exceptions and failures."""

    def __init__(
        self,
        repo: KnowledgeRepository,
        project_id: str,
        project_root: Path,
    ) -> None:
        self.repo = repo
        self.project_id = project_id
        self.project_root = Path(project_root).resolve()
        self.correlator = RuntimeCorrelator(repo, project_id, project_root)
        self.static_impact_engine = ImpactEngine(repo, project_id, project_root)

    def analyze_event_impact(
        self,
        event: RuntimeEvent,
        max_depth: int = 3,
    ) -> RuntimeImpactAnalysisReport:
        """Analyze potential downstream consequences of an individual runtime event."""
        observed = RuntimeObservedFacts(
            event_id=event.id,
            timestamp=event.timestamp,
            exception_type=event.exception_type or event.level,
            error_message=event.message,
            file_path=event.file_path,
            line_number=event.line_number,
            symbol_name=event.symbol_name,
            occurrence_count=1,
            frames=event.stack_trace.frames if event.stack_trace else [],
            observed_attributes=event.attributes,
        )

        return self._generate_report(
            identifier=event.id,
            fingerprint=event.fingerprint,
            observed=observed,
            max_depth=max_depth,
            frames=event.stack_trace.frames if event.stack_trace else [],
        )

    def analyze_error_group_impact(
        self,
        error_group: RuntimeErrorGroup,
        max_depth: int = 3,
    ) -> RuntimeImpactAnalysisReport:
        """Analyze potential downstream consequences of an aggregated error group."""
        observed = RuntimeObservedFacts(
            event_id=error_group.sample_event_id or error_group.id,
            timestamp=error_group.last_seen,
            exception_type=error_group.exception_type,
            error_message=error_group.message_template,
            file_path=error_group.file_path,
            line_number=error_group.line_number,
            symbol_name=error_group.symbol_name,
            occurrence_count=error_group.occurrence_count,
            frames=[],
            observed_attributes={},
        )

        return self._generate_report(
            identifier=error_group.id,
            fingerprint=error_group.fingerprint,
            observed=observed,
            max_depth=max_depth,
            frames=[],
        )

    def _generate_report(
        self,
        identifier: str,
        fingerprint: str,
        observed: RuntimeObservedFacts,
        max_depth: int,
        frames: list[Any],
    ) -> RuntimeImpactAnalysisReport:
        """Combine empirical observations with static graph traversal."""
        file_entity = self.correlator.resolve_file(observed.file_path)

        # Fallback to stack frames if primary path not resolved
        if not file_entity and frames:
            for frame in reversed(frames):
                frame_file = getattr(frame, "file_path", None)
                cand = self.correlator.resolve_file(frame_file)
                if cand:
                    file_entity = cand
                    observed.file_path = cand.path
                    observed.line_number = getattr(frame, "line_number", None)
                    observed.symbol_name = getattr(frame, "function_name", None)
                    break

        if not file_entity:
            # Unindexed file or external library
            checklist = [
                f"Verify if file '{observed.file_path or 'unknown'}' is part of this project repository.",
                "Run 'msk update' to index any newly created or untracked source files.",
                "If the error originates in an external dependency, check dependency version specifications.",
            ]
            return RuntimeImpactAnalysisReport(
                report_id=make_entity_id("rimp", self.project_id, identifier),
                project_id=self.project_id,
                fingerprint=fingerprint,
                observed_facts=observed,
                direct_impacts=[],
                transitive_impacts=[],
                impacted_apis=[],
                recommended_tests=[],
                total_blast_radius=0,
                investigation_checklist=checklist,
                analyzed_at=datetime.now(UTC).isoformat(),
            )

        symbol_entity = self.correlator.resolve_symbol(
            file_entity, observed.line_number, observed.symbol_name
        )

        direct_impacts: list[RuntimeImpactPossibility] = []
        transitive_impacts: list[RuntimeImpactPossibility] = []

        # Run static impact engine traversal
        try:
            if symbol_entity:
                static_report = self.static_impact_engine.analyze_symbol(
                    symbol_name=symbol_entity.name,
                    file_path=file_entity.path,
                    max_depth=max_depth,
                )
            else:
                static_report = self.static_impact_engine.analyze_file(
                    file_path=file_entity.path,
                    max_depth=max_depth,
                )

            for item in static_report.items:
                possibility = RuntimeImpactPossibility(
                    name=item.name,
                    item_type=item.type,
                    category=item.category,
                    classification=item.classification,
                    depth=item.depth,
                    reason=item.reason,
                    file_path=item.file_path,
                )
                if item.classification == "direct":
                    direct_impacts.append(possibility)
                else:
                    transitive_impacts.append(possibility)
        except Exception:
            # Graceful fallback to basic correlator relationships
            self._populate_fallback_impacts(file_entity, symbol_entity, direct_impacts)

        # Detect affected APIs and tests
        callers, _ = (
            self.correlator.find_callers_and_callees(symbol_entity)
            if symbol_entity
            else ([], [])
        )
        impacted_apis = self.correlator.find_related_apis(
            file_entity, symbol_entity, callers
        )
        recommended_tests = self.correlator.find_related_tests(file_entity, symbol_entity)

        # Formulate actionable investigation checklist
        checklist = self._build_investigation_checklist(
            observed=observed,
            file_entity=file_entity,
            symbol_entity=symbol_entity,
            callers=callers,
            impacted_apis=impacted_apis,
            recommended_tests=recommended_tests,
        )

        total_blast = len(direct_impacts) + len(transitive_impacts) + len(impacted_apis)

        return RuntimeImpactAnalysisReport(
            report_id=make_entity_id("rimp", self.project_id, identifier),
            project_id=self.project_id,
            fingerprint=fingerprint,
            observed_facts=observed,
            direct_impacts=direct_impacts,
            transitive_impacts=transitive_impacts,
            impacted_apis=impacted_apis,
            recommended_tests=recommended_tests,
            total_blast_radius=total_blast,
            investigation_checklist=checklist,
            analyzed_at=datetime.now(UTC).isoformat(),
        )

    def _populate_fallback_impacts(
        self,
        file_entity: FileEntity,
        symbol_entity: SymbolEntity | None,
        direct_impacts: list[RuntimeImpactPossibility],
    ) -> None:
        """Fallback to direct file dependencies if static traversal fails."""
        dependent_files = self.correlator.find_dependent_files(file_entity)
        for dep in dependent_files:
            direct_impacts.append(
                RuntimeImpactPossibility(
                    name=dep,
                    item_type="file",
                    category="dependent_file",
                    classification="direct",
                    depth=1,
                    reason=f"File '{dep}' imports affected file '{file_entity.path}'",
                    file_path=dep,
                )
            )

    def _build_investigation_checklist(
        self,
        observed: RuntimeObservedFacts,
        file_entity: FileEntity,
        symbol_entity: SymbolEntity | None,
        callers: list[str],
        impacted_apis: list[str],
        recommended_tests: list[str],
    ) -> list[str]:
        """Generate structured debugging checklist for developers."""
        checklist: list[str] = []

        loc_str = f"{file_entity.path}"
        if observed.line_number:
            loc_str += f":{observed.line_number}"
        if symbol_entity:
            loc_str += f" in '{symbol_entity.name}'"

        checklist.append(
            f"Inspect observed failure site at {loc_str} for cause of {observed.exception_type}."
        )

        if recommended_tests:
            checklist.append(
                f"Run test suite to reproduce failure locally: {recommended_tests[0]}."
            )
        else:
            checklist.append(
                f"No direct test covering {file_entity.path} discovered. Consider adding a test case."
            )

        if callers:
            checklist.append(
                f"Review call parameters from caller '{callers[0]}' to check for invalid inputs."
            )

        if impacted_apis:
            checklist.append(
                f"Check user-facing impact on route '{impacted_apis[0]}'."
            )

        if observed.occurrence_count > 1:
            checklist.append(
                f"Error has occurred {observed.occurrence_count} times; investigate if triggered by retry or high-traffic flow."
            )

        return checklist
