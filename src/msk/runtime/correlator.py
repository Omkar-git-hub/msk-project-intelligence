"""Correlates runtime observations with MSK static project knowledge."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from msk.graph.relationships import RelationshipType
from msk.knowledge.models import (
    FileEntity,
    SymbolEntity,
    make_entity_id,
)
from msk.knowledge.repository import KnowledgeRepository
from msk.runtime.models import (
    RuntimeCorrelationReport,
    RuntimeErrorGroup,
    RuntimeEvent,
    RuntimeInferredImpact,
    RuntimeObservedFacts,
)


class RuntimeCorrelator:
    """Bridges runtime events and exceptions to the static knowledge graph."""

    def __init__(
        self,
        repo: KnowledgeRepository,
        project_id: str,
        project_root: Path,
    ) -> None:
        self.repo = repo
        self.project_id = project_id
        self.project_root = Path(project_root).resolve()

        # Cache knowledge entities for performant lookup
        self.files = self.repo.get_all_files(self.project_id)
        self.symbols = self.repo.get_all_symbols(self.project_id)
        self.relationships = self.repo.get_all_relationships(self.project_id)
        self.tests = self.repo.get_all_tests(self.project_id)
        self.apis = self.repo.get_all_api_endpoints(self.project_id)

        # Index structures
        self.files_by_id: dict[str, FileEntity] = {f.id: f for f in self.files}
        self.files_by_norm_path: dict[str, FileEntity] = {
            f.path.replace("\\", "/"): f for f in self.files
        }
        self.symbols_by_id: dict[str, SymbolEntity] = {s.id: s for s in self.symbols}
        self.symbols_by_file_id: dict[str, list[SymbolEntity]] = {}
        for s in self.symbols:
            self.symbols_by_file_id.setdefault(s.file_id, []).append(s)

    def resolve_file(self, raw_path: str | None) -> FileEntity | None:
        """Resolve a raw path from stack trace to an indexed project file."""
        if not raw_path:
            return None

        norm = raw_path.replace("\\", "/").strip().lstrip("./")

        # 1. Exact match
        if norm in self.files_by_norm_path:
            return self.files_by_norm_path[norm]

        # 2. Match relative to project root
        p = Path(raw_path)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(self.project_root).as_posix()
                if rel in self.files_by_norm_path:
                    return self.files_by_norm_path[rel]
            except ValueError:
                pass

        # 3. Suffix match (e.g. app/src/payment.py -> src/payment.py or payment.py)
        for indexed_path, f in self.files_by_norm_path.items():
            if norm.endswith(indexed_path) or indexed_path.endswith(norm):
                return f

        return None

    def resolve_symbol(
        self,
        file_entity: FileEntity,
        line_number: int | None,
        function_name: str | None,
    ) -> SymbolEntity | None:
        """Resolve the enclosing or named symbol within an affected file."""
        file_symbols = self.symbols_by_file_id.get(file_entity.id, [])
        if not file_symbols:
            return None

        # 1. If line number is known, find enclosing symbols
        if line_number is not None:
            enclosing: list[SymbolEntity] = [
                s for s in file_symbols if s.line_start <= line_number <= s.line_end
            ]
            if enclosing:
                # If function_name also matches, prioritize it
                if function_name:
                    for s in enclosing:
                        if s.name == function_name or (s.parent_symbol and f"{s.parent_symbol}.{s.name}" == function_name):
                            return s
                # Return innermost (smallest range)
                enclosing.sort(key=lambda s: s.line_end - s.line_start)
                return enclosing[0]

        # 2. Match by function name
        if function_name:
            # Clean package or method prefixes (e.g. Class.method -> method)
            short_name = function_name.split(".")[-1]
            for s in file_symbols:
                if s.name == function_name or s.name == short_name:
                    return s

        return None

    def find_callers_and_callees(
        self, symbol: SymbolEntity
    ) -> tuple[list[str], list[str]]:
        """Identify callers and callees connected to the symbol via call graph."""
        callers: list[str] = []
        callees: list[str] = []

        for r in self.relationships:
            if r.relationship_type == RelationshipType.SYMBOL_CALLS_SYMBOL:
                # Callers: source calls target (symbol)
                if r.target_id == symbol.id:
                    caller_sym = self.symbols_by_id.get(r.source_id)
                    if caller_sym:
                        c_file = self.files_by_id.get(caller_sym.file_id)
                        file_label = f" ({c_file.path})" if c_file else ""
                        callers.append(f"{caller_sym.name}{file_label}")

                # Callees: symbol calls target
                if r.source_id == symbol.id:
                    callee_sym = self.symbols_by_id.get(r.target_id)
                    if callee_sym:
                        c_file = self.files_by_id.get(callee_sym.file_id)
                        file_label = f" ({c_file.path})" if c_file else ""
                        callees.append(f"{callee_sym.name}{file_label}")

        return sorted(set(callers)), sorted(set(callees))

    def find_dependent_files(self, file_entity: FileEntity) -> list[str]:
        """Find other project files that import or depend on the affected file."""
        dependent: list[str] = []
        for r in self.relationships:
            if (
                r.relationship_type == RelationshipType.FILE_DEPENDS_ON_FILE
                and r.target_id == file_entity.id
            ):
                source_file = self.files_by_id.get(r.source_id)
                if source_file and source_file.id != file_entity.id:
                    dependent.append(source_file.path)
        return sorted(set(dependent))

    def find_related_tests(
        self, file_entity: FileEntity, symbol: SymbolEntity | None
    ) -> list[str]:
        """Discover tests covering the affected symbol or file."""
        related_tests: list[str] = []

        # 1. Tests directly targeting symbol name
        if symbol:
            for t in self.tests:
                if t.target_symbol and (
                    t.target_symbol == symbol.name
                    or t.target_symbol.endswith(f".{symbol.name}")
                ):
                    t_file = self.files_by_id.get(t.file_id)
                    loc = f" in {t_file.path}" if t_file else ""
                    related_tests.append(f"{t.name}{loc}")

        # 2. Tests in files that test or import this file
        file_stem = Path(file_entity.path).stem
        for t in self.tests:
            t_file = self.files_by_id.get(t.file_id)
            if t_file:
                t_stem = Path(t_file.path).stem
                if (
                    t_file.id == file_entity.id
                    or f"test_{file_stem}" in t_stem
                    or f"{file_stem}_test" in t_stem
                ):
                    related_tests.append(f"{t.name} in {t_file.path}")

        return sorted(set(related_tests))

    def find_related_apis(
        self,
        file_entity: FileEntity,
        symbol: SymbolEntity | None,
        caller_names: list[str],
    ) -> list[str]:
        """Detect API endpoints whose handlers call or are called by the affected symbol."""
        related_apis: list[str] = []

        for api in self.apis:
            handler = api.handler_symbol or ""
            is_match = False

            if symbol and (handler == symbol.name or handler.endswith(f".{symbol.name}")):
                is_match = True
            elif any(handler and c.startswith(handler) for c in caller_names):
                is_match = True
            elif api.file_id == file_entity.id:
                is_match = True

            if is_match:
                handler_suffix = f" (handler: {api.handler_symbol})" if api.handler_symbol else ""
                related_apis.append(f"{api.http_method} {api.route}{handler_suffix}")

        return sorted(set(related_apis))

    def correlate_event(self, event: RuntimeEvent) -> RuntimeCorrelationReport:
        """Perform comprehensive correlation of a single runtime event."""
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

        inferred = self._correlate_locus(
            raw_file=event.file_path,
            line_number=event.line_number,
            raw_symbol=event.symbol_name,
            frames=event.stack_trace.frames if event.stack_trace else [],
        )

        return RuntimeCorrelationReport(
            correlation_id=make_entity_id("cor", self.project_id, event.id),
            project_id=self.project_id,
            fingerprint=event.fingerprint,
            observed_facts=observed,
            inferred_impact=inferred,
            correlated_at=datetime.now(UTC).isoformat(),
        )

    def correlate_error_group(
        self, error_group: RuntimeErrorGroup
    ) -> RuntimeCorrelationReport:
        """Perform correlation of an aggregated error group."""
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

        inferred = self._correlate_locus(
            raw_file=error_group.file_path,
            line_number=error_group.line_number,
            raw_symbol=error_group.symbol_name,
            frames=[],
        )

        return RuntimeCorrelationReport(
            correlation_id=make_entity_id("cor", self.project_id, error_group.id),
            project_id=self.project_id,
            fingerprint=error_group.fingerprint,
            observed_facts=observed,
            inferred_impact=inferred,
            correlated_at=datetime.now(UTC).isoformat(),
        )

    def _correlate_locus(
        self,
        raw_file: str | None,
        line_number: int | None,
        raw_symbol: str | None,
        frames: list[Any],
    ) -> RuntimeInferredImpact:
        """Resolve locus against knowledge model and extract downstream dependencies."""
        file_entity = self.resolve_file(raw_file)

        # Fallback to project frames if primary raw_file did not resolve
        if not file_entity and frames:
            for frame in reversed(frames):
                frame_file = getattr(frame, "file_path", None)
                cand = self.resolve_file(frame_file)
                if cand:
                    file_entity = cand
                    line_number = getattr(frame, "line_number", None)
                    raw_symbol = getattr(frame, "function_name", None)
                    break

        if not file_entity:
            return RuntimeInferredImpact(
                recommended_actions=[
                    "Check if log file path matches project directory structure.",
                    "Run 'msk update' to index any newly created source files.",
                ]
            )

        symbol_entity = self.resolve_symbol(file_entity, line_number, raw_symbol)
        callers, callees = (
            self.find_callers_and_callees(symbol_entity) if symbol_entity else ([], [])
        )
        dependent_files = self.find_dependent_files(file_entity)
        related_tests = self.find_related_tests(file_entity, symbol_entity)
        related_apis = self.find_related_apis(file_entity, symbol_entity, callers)

        total_downstream = len(callers) + len(dependent_files) + len(related_apis)

        actions: list[str] = []
        if related_tests:
            actions.append(f"Execute test suite to reproduce: {', '.join(related_tests[:3])}")
        else:
            actions.append(f"Add regression test targeting {file_entity.path}")

        if callers:
            actions.append(f"Inspect upstream caller: {callers[0]}")

        if related_apis:
            actions.append(f"Verify API endpoint integrity: {related_apis[0]}")

        return RuntimeInferredImpact(
            affected_file=file_entity.path,
            affected_symbol=symbol_entity.name if symbol_entity else raw_symbol,
            callers=callers,
            callees=callees,
            dependent_files=dependent_files,
            related_tests=related_tests,
            related_apis=related_apis,
            downstream_impact_count=total_downstream,
            recommended_actions=actions,
        )
