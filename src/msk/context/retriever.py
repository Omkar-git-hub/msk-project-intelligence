"""Context retrieval engine querying the MSK project knowledge model."""

import re
from collections import defaultdict
from pathlib import Path

from msk.common.errors import MSKError
from msk.config.models import PolicyConfig
from msk.context.budget import estimate_tokens
from msk.context.errors_parser import ParsedErrorContext, parse_error_text
from msk.context.models import (
    ContextItem,
    ContextItemType,
    ContextRequest,
    RelationshipEvidence,
)
from msk.context.privacy import PrivacyFilter
from msk.context.ranking import (
    WEIGHT_API_ENDPOINT,
    WEIGHT_CONTAINED_CONTAINING,
    WEIGHT_DIRECT_RELATIONSHIP,
    WEIGHT_EXACT_KEYWORD,
    WEIGHT_EXACT_TARGET,
    WEIGHT_INFRASTRUCTURE,
    WEIGHT_RELATED_TEST,
    WEIGHT_SECURITY_FINDING,
    WEIGHT_STACK_FRAME,
    WEIGHT_TRANSITIVE_DEPTH_2,
    WEIGHT_TRANSITIVE_DEPTH_3,
    sort_and_rank_items,
)
from msk.impact.engine import ImpactEngine
from msk.impact.models import ImpactClassification
from msk.knowledge.models import (
    ApiEndpointEntity,
    FileEntity,
    InfrastructureEntity,
    SecurityFindingEntity,
    SymbolEntity,
    TestEntity,
)
from msk.knowledge.repository import KnowledgeRepository

_STOP_WORDS = {
    "a", "an", "the", "in", "on", "of", "for", "with", "to", "from", "by", "at",
    "and", "or", "not", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "can", "could", "will", "would",
    "should", "how", "what", "where", "when", "why", "who", "which", "this",
    "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "fix", "error", "bug", "issue", "problem", "fail", "failed", "failing",
}


def _extract_keywords(text: str | None) -> list[str]:
    """Extract candidate identifier tokens from natural-language text."""
    if not text:
        return []
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text)
    return [w for w in words if len(w) >= 2 and w.lower() not in _STOP_WORDS]


class ContextRetriever:
    """Retrieves and scores relevant entities from MSK Knowledge Model."""

    def __init__(
        self,
        repo: KnowledgeRepository,
        project_id: str,
        project_root: Path,
        privacy_filter: PrivacyFilter,
        policy: PolicyConfig | None = None,
    ) -> None:
        self.repo = repo
        self.project_id = project_id
        self.project_root = Path(project_root).resolve()
        self.privacy = privacy_filter
        self.policy = policy

        # Pre-load knowledge entities
        self.files = self.repo.get_all_files(self.project_id)
        self.symbols = self.repo.get_all_symbols(self.project_id)
        self.tests = self.repo.get_all_tests(self.project_id)
        self.apis = self.repo.get_all_api_endpoints(self.project_id)
        self.infra = self.repo.get_all_infrastructure(self.project_id)
        self.security = self.repo.get_all_security_findings(self.project_id)

        # Indexing for lookup
        self.files_by_id: dict[str, FileEntity] = {f.id: f for f in self.files}
        self.files_by_path: dict[str, FileEntity] = {}
        for f in self.files:
            self.files_by_path[f.path] = f
            self.files_by_path[f.path.replace("\\", "/")] = f

        self.symbols_by_id: dict[str, SymbolEntity] = {s.id: s for s in self.symbols}
        self.symbols_by_file_id: dict[str, list[SymbolEntity]] = defaultdict(list)
        self.symbols_by_name: dict[str, list[SymbolEntity]] = defaultdict(list)
        for s in self.symbols:
            self.symbols_by_file_id[s.file_id].append(s)
            self.symbols_by_name[s.name].append(s)

        self.tests_by_file_id: dict[str, list[TestEntity]] = defaultdict(list)
        self.tests_by_target: dict[str, list[TestEntity]] = defaultdict(list)
        for t in self.tests:
            self.tests_by_file_id[t.file_id].append(t)
            if t.target_symbol:
                self.tests_by_target[t.target_symbol].append(t)

        self.apis_by_file_id: dict[str, list[ApiEndpointEntity]] = defaultdict(list)
        self.apis_by_handler: dict[str, list[ApiEndpointEntity]] = defaultdict(list)
        for a in self.apis:
            self.apis_by_file_id[a.file_id].append(a)
            if a.handler_symbol:
                self.apis_by_handler[a.handler_symbol].append(a)

        self.infra_by_file_id: dict[str, list[InfrastructureEntity]] = defaultdict(list)
        for i in self.infra:
            self.infra_by_file_id[i.file_id].append(i)

        self.security_by_file_id: dict[str, list[SecurityFindingEntity]] = defaultdict(list)
        for sec in self.security:
            self.security_by_file_id[sec.file_id].append(sec)

        self.warnings: list[str] = []
        self.relationships: list[RelationshipEvidence] = []
        self.related_tests: set[str] = set()

    def _normalize_path(self, path: str) -> str:
        """Normalize file path to POSIX project-relative format."""
        p = Path(path)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(self.project_root).as_posix()
            except ValueError:
                rel = p.as_posix()
        else:
            rel = p.as_posix()
        if rel.startswith("./"):
            rel = rel[2:]
        return rel

    def retrieve_candidates(self, request: ContextRequest) -> list[ContextItem]:
        """Collect and score all potential context candidate items for the request."""
        candidates_map: dict[str, ContextItem] = {}

        # 1. Parse error / stack trace if provided
        parsed_error: ParsedErrorContext = parse_error_text(request.error)

        # 2. Extract question keywords
        question_keywords = _extract_keywords(request.question)

        # 3. Process Target File
        target_file_ent: FileEntity | None = None
        if request.file:
            norm_file = self._normalize_path(request.file)
            target_file_ent = self.files_by_path.get(norm_file)
            if not target_file_ent:
                disk_path = self.project_root / norm_file
                if disk_path.exists():
                    self.warnings.append(
                        f"File '{norm_file}' exists on disk but is not indexed in MSK knowledge store. Run 'msk update'."
                    )
                else:
                    self.warnings.append(f"File '{norm_file}' was not found in project.")
            else:
                self._add_file_candidate(
                    candidates_map,
                    target_file_ent,
                    score=WEIGHT_EXACT_TARGET,
                    reason=f"Explicitly requested file '{target_file_ent.path}'",
                )
                # Add contained symbols
                for sym in self.symbols_by_file_id.get(target_file_ent.id, []):
                    self._add_symbol_candidate(
                        candidates_map,
                        sym,
                        target_file_ent,
                        score=WEIGHT_CONTAINED_CONTAINING,
                        reason=f"Symbol defined in requested file '{target_file_ent.path}'",
                    )
                # Run impact analysis on target file
                self._collect_file_impact(target_file_ent, candidates_map)

        # 4. Process Target Symbol
        target_symbol_ent: SymbolEntity | None = None
        if request.symbol:
            sym_candidates = self.symbols_by_name.get(request.symbol, [])
            if request.file and target_file_ent:
                sym_candidates = [s for s in sym_candidates if s.file_id == target_file_ent.id]

            if not sym_candidates:
                # Check for qualified name (Class.method)
                if "." in request.symbol:
                    p_name, c_name = request.symbol.split(".", 1)
                    pool = self.symbols_by_file_id.get(target_file_ent.id, []) if target_file_ent else self.symbols
                    sym_candidates = [s for s in pool if s.parent_symbol == p_name and s.name == c_name]

            if not sym_candidates:
                self.warnings.append(f"Symbol '{request.symbol}' was not found in project knowledge model.")
            else:
                target_symbol_ent = sym_candidates[0]
                parent_file = self.files_by_id.get(target_symbol_ent.file_id)
                if parent_file:
                    display_name = (
                        f"{target_symbol_ent.parent_symbol}.{target_symbol_ent.name}"
                        if target_symbol_ent.parent_symbol
                        else target_symbol_ent.name
                    )
                    self._add_symbol_candidate(
                        candidates_map,
                        target_symbol_ent,
                        parent_file,
                        score=WEIGHT_EXACT_TARGET,
                        reason=f"Explicitly requested symbol '{display_name}'",
                    )
                    self._add_file_candidate(
                        candidates_map,
                        parent_file,
                        score=WEIGHT_CONTAINED_CONTAINING,
                        reason=f"File containing requested symbol '{display_name}'",
                    )
                    # Run impact analysis on target symbol
                    self._collect_symbol_impact(target_symbol_ent, parent_file, candidates_map)

        # 5. Process Stack Trace and Error Frames
        self._process_parsed_error(parsed_error, candidates_map)

        # 6. Process Query Keywords
        self._process_keywords(question_keywords, candidates_map)

        # 7. Include APIs, Infra, Security, Tests matching selected files
        self._enrich_selected_entities(candidates_map)

        # Sort and return
        raw_items = list(candidates_map.values())
        return sort_and_rank_items(raw_items)

    def _is_item_blocked_by_policy(
        self,
        file_path: str | None = None,
        category: str | None = None,
    ) -> bool:
        """Check if an item is prohibited by active policy rules."""
        if not self.policy:
            return False

        from msk.policy.integration import PolicyIntegrationService

        if file_path and PolicyIntegrationService.is_file_blocked(file_path, self.policy):
            if file_path not in self.privacy.excluded_files:
                self.privacy.excluded_files.append(file_path)
            return True

        if category and PolicyIntegrationService.is_category_blocked(category, self.policy):
            return True

        return False

    def _add_file_candidate(
        self,
        candidates: dict[str, ContextItem],
        file_ent: FileEntity,
        score: float,
        reason: str,
    ) -> None:
        """Register or boost a file context candidate."""
        if not self.privacy.check_and_record_file(file_ent.path):
            return
        if self._is_item_blocked_by_policy(file_path=file_ent.path, category=ContextItemType.FILE):
            return

        item_id = f"file:{file_ent.id}"
        if item_id in candidates:
            candidates[item_id].relevance_score = min(150.0, candidates[item_id].relevance_score + (score * 0.5))
            if reason not in candidates[item_id].reasons:
                candidates[item_id].reasons.append(reason)
            return

        contained_syms = [s.name for s in self.symbols_by_file_id.get(file_ent.id, [])]
        syms_summary = ", ".join(contained_syms[:8]) + ("..." if len(contained_syms) > 8 else "")

        snippet = (
            f"File: {file_ent.path}\n"
            f"Language: {file_ent.language or 'unknown'} | Type: {file_ent.file_type} | Size: {file_ent.size_bytes} bytes\n"
            f"Imports: {', '.join(file_ent.imports) if file_ent.imports else 'none'}\n"
            f"Contained Symbols ({len(contained_syms)}): {syms_summary or 'none'}"
        )

        candidates[item_id] = ContextItem(
            id=item_id,
            name=file_ent.path,
            item_type=ContextItemType.FILE,
            file_path=file_ent.path,
            relevance_score=score,
            reasons=[reason],
            snippet=snippet,
            estimated_tokens=estimate_tokens(snippet),
            metadata={
                "language": file_ent.language,
                "file_type": file_ent.file_type,
                "size_bytes": file_ent.size_bytes,
                "imports": file_ent.imports,
                "is_test": file_ent.is_test,
            },
        )

    def _add_symbol_candidate(
        self,
        candidates: dict[str, ContextItem],
        sym: SymbolEntity,
        parent_file: FileEntity,
        score: float,
        reason: str,
    ) -> None:
        """Register or boost a symbol context candidate."""
        if not self.privacy.check_and_record_file(parent_file.path):
            return
        if self._is_item_blocked_by_policy(file_path=parent_file.path, category=ContextItemType.SYMBOL):
            return

        item_id = f"symbol:{sym.id}"
        display_name = f"{sym.parent_symbol}.{sym.name}" if sym.parent_symbol else sym.name

        if item_id in candidates:
            candidates[item_id].relevance_score = min(150.0, candidates[item_id].relevance_score + (score * 0.5))
            if reason not in candidates[item_id].reasons:
                candidates[item_id].reasons.append(reason)
            return

        calls_str = ", ".join(sym.calls) if sym.calls else "none"
        snippet = (
            f"Symbol: {display_name} ({sym.type})\n"
            f"Defined in: {parent_file.path}:{sym.line_start}-{sym.line_end} [{sym.visibility}]\n"
            f"Calls: {calls_str}"
        )
        if sym.is_api_endpoint and sym.api_route:
            snippet += f"\nAPI Route: {sym.api_method or 'ANY'} {sym.api_route}"

        candidates[item_id] = ContextItem(
            id=item_id,
            name=display_name,
            item_type=ContextItemType.SYMBOL,
            file_path=parent_file.path,
            relevance_score=score,
            reasons=[reason],
            snippet=snippet,
            estimated_tokens=estimate_tokens(snippet),
            metadata={
                "symbol_type": sym.type,
                "parent_symbol": sym.parent_symbol,
                "line_start": sym.line_start,
                "line_end": sym.line_end,
                "visibility": sym.visibility,
                "is_test": sym.is_test,
                "is_api_endpoint": sym.is_api_endpoint,
                "api_route": sym.api_route,
                "api_method": sym.api_method,
                "calls": sym.calls,
            },
        )

    def _collect_file_impact(self, file_ent: FileEntity, candidates: dict[str, ContextItem]) -> None:
        """Run Phase 4 Impact Analysis on target file and incorporate downstream callers, tests, and paths."""
        try:
            impact_engine = ImpactEngine(self.repo, self.project_id, self.project_root)
            report = impact_engine.analyze_file(file_ent.path, max_depth=3)
            for item in report.items:
                score = (
                    WEIGHT_DIRECT_RELATIONSHIP
                    if item.classification == ImpactClassification.DIRECT
                    else (WEIGHT_TRANSITIVE_DEPTH_2 if item.depth == 2 else WEIGHT_TRANSITIVE_DEPTH_3)
                )

                # Record relationship evidence
                self.relationships.append(
                    RelationshipEvidence(
                        source=file_ent.path,
                        target=item.name,
                        relationship_type=item.relationship_type,
                        depth=item.depth,
                        path=item.path,
                        reason=item.reason,
                    )
                )

                if item.type == "test":
                    self.related_tests.add(f"{item.name} ({item.file_path})")

                # Add to candidates
                aff_file = self.files_by_path.get(item.file_path)
                if not aff_file:
                    continue

                if item.type == "file":
                    self._add_file_candidate(
                        candidates,
                        aff_file,
                        score=score,
                        reason=f"Downstream {item.classification} impact (depth {item.depth}): {item.reason}",
                    )
                elif item.type == "symbol":
                    raw_name = item.name.split(".")[-1]
                    syms = [s for s in self.symbols_by_name.get(raw_name, []) if s.file_id == aff_file.id]
                    if syms:
                        self._add_symbol_candidate(
                            candidates,
                            syms[0],
                            aff_file,
                            score=score,
                            reason=f"Downstream {item.classification} caller (depth {item.depth}): {item.reason}",
                        )
                elif item.type == "test":
                    self._add_test_candidate(
                        candidates,
                        item.name,
                        aff_file,
                        score=WEIGHT_RELATED_TEST,
                        reason=f"Test affected by changes to '{file_ent.path}': {item.reason}",
                    )
        except MSKError:
            pass

    def _collect_symbol_impact(
        self,
        sym: SymbolEntity,
        parent_file: FileEntity,
        candidates: dict[str, ContextItem],
    ) -> None:
        """Run Phase 4 Impact Analysis on target symbol."""
        try:
            impact_engine = ImpactEngine(self.repo, self.project_id, self.project_root)
            report = impact_engine.analyze_symbol(sym.name, file_path=parent_file.path, max_depth=3)
            for item in report.items:
                score = (
                    WEIGHT_DIRECT_RELATIONSHIP
                    if item.classification == ImpactClassification.DIRECT
                    else (WEIGHT_TRANSITIVE_DEPTH_2 if item.depth == 2 else WEIGHT_TRANSITIVE_DEPTH_3)
                )

                self.relationships.append(
                    RelationshipEvidence(
                        source=f"{sym.parent_symbol}.{sym.name}" if sym.parent_symbol else sym.name,
                        target=item.name,
                        relationship_type=item.relationship_type,
                        depth=item.depth,
                        path=item.path,
                        reason=item.reason,
                    )
                )

                if item.type == "test":
                    self.related_tests.add(f"{item.name} ({item.file_path})")

                aff_file = self.files_by_path.get(item.file_path)
                if not aff_file:
                    continue

                if item.type == "symbol":
                    raw_name = item.name.split(".")[-1]
                    s_list = [s for s in self.symbols_by_name.get(raw_name, []) if s.file_id == aff_file.id]
                    if s_list:
                        self._add_symbol_candidate(
                            candidates,
                            s_list[0],
                            aff_file,
                            score=score,
                            reason=f"Impact caller (depth {item.depth}): {item.reason}",
                        )
                elif item.type == "file":
                    self._add_file_candidate(
                        candidates,
                        aff_file,
                        score=score,
                        reason=f"Impacted file: {item.reason}",
                    )
                elif item.type == "test":
                    self._add_test_candidate(
                        candidates,
                        item.name,
                        aff_file,
                        score=WEIGHT_RELATED_TEST,
                        reason=f"Test targeting '{sym.name}': {item.reason}",
                    )
        except MSKError:
            pass

    def _process_parsed_error(
        self,
        parsed: ParsedErrorContext,
        candidates: dict[str, ContextItem],
    ) -> None:
        """Match stack frame locations, files, and error symbols against knowledge base."""
        # Process locations with line numbers
        for loc in parsed.locations:
            matched_file = self._find_matching_file(loc.file_path)
            if matched_file:
                reason = (
                    f"Referenced in stack trace at line {loc.line_number}"
                    if loc.line_number
                    else "Referenced in stack trace"
                )
                self._add_file_candidate(candidates, matched_file, score=WEIGHT_STACK_FRAME, reason=reason)

                # If a specific symbol in that file spans this line
                if loc.line_number:
                    file_syms = self.symbols_by_file_id.get(matched_file.id, [])
                    for s in file_syms:
                        if s.line_start <= loc.line_number <= s.line_end:
                            self._add_symbol_candidate(
                                candidates,
                                s,
                                matched_file,
                                score=WEIGHT_STACK_FRAME + 5.0,
                                reason=f"Failing execution frame in error stack trace (line {loc.line_number})",
                            )

        # Process extracted files
        for f_path in parsed.extracted_files:
            matched_file = self._find_matching_file(f_path)
            if matched_file:
                self._add_file_candidate(
                    candidates,
                    matched_file,
                    score=WEIGHT_STACK_FRAME,
                    reason=f"Mentioned in error diagnostic context ('{f_path}')",
                )

        # Process extracted symbols
        for sym_name in parsed.extracted_symbols:
            matching_syms = self.symbols_by_name.get(sym_name, [])
            for sym in matching_syms:
                p_file = self.files_by_id.get(sym.file_id)
                if p_file:
                    self._add_symbol_candidate(
                        candidates,
                        sym,
                        p_file,
                        score=WEIGHT_STACK_FRAME,
                        reason=f"Symbol '{sym_name}' identified in error or stack trace",
                    )

    def _find_matching_file(self, raw_path: str) -> FileEntity | None:
        """Find FileEntity matching raw path by exact relative path or filename suffix."""
        norm = self._normalize_path(raw_path)
        if norm in self.files_by_path:
            return self.files_by_path[norm]

        # Suffix / basename lookup
        basename = Path(raw_path).name
        candidates = [f for f in self.files if f.path.endswith(basename)]
        if len(candidates) == 1:
            return candidates[0]
        # Prefer exact match ending with norm
        for c in candidates:
            if c.path.endswith(norm):
                return c
        return candidates[0] if candidates else None

    def _process_keywords(
        self,
        keywords: list[str],
        candidates: dict[str, ContextItem],
    ) -> None:
        """Match query keywords against symbol names and file paths."""
        for kw in keywords:
            kw_lower = kw.lower()

            # Exact symbol name match
            if kw in self.symbols_by_name:
                for sym in self.symbols_by_name[kw]:
                    p_file = self.files_by_id.get(sym.file_id)
                    if p_file:
                        self._add_symbol_candidate(
                            candidates,
                            sym,
                            p_file,
                            score=WEIGHT_EXACT_KEYWORD,
                            reason=f"Symbol name matches query keyword '{kw}'",
                        )

            # Substring symbol name match
            for name, s_list in self.symbols_by_name.items():
                if kw_lower in name.lower() and name != kw:
                    for sym in s_list:
                        p_file = self.files_by_id.get(sym.file_id)
                        if p_file:
                            self._add_symbol_candidate(
                                candidates,
                                sym,
                                p_file,
                                score=WEIGHT_EXACT_KEYWORD * 0.6,
                                reason=f"Symbol '{name}' contains query keyword '{kw}'",
                            )

            # File name match
            for f in self.files:
                f_name = Path(f.path).name.lower()
                if kw_lower == f_name or kw_lower in f.path.lower():
                    self._add_file_candidate(
                        candidates,
                        f,
                        score=WEIGHT_EXACT_KEYWORD * 0.8,
                        reason=f"File path matches query keyword '{kw}'",
                    )

    def _add_test_candidate(
        self,
        candidates: dict[str, ContextItem],
        test_name: str,
        test_file: FileEntity,
        score: float,
        reason: str,
    ) -> None:
        """Add a test context item."""
        if not self.privacy.check_and_record_file(test_file.path):
            return
        if self._is_item_blocked_by_policy(file_path=test_file.path, category=ContextItemType.TEST):
            return

        item_id = f"test:{test_file.id}:{test_name}"
        if item_id in candidates:
            candidates[item_id].relevance_score = min(150.0, candidates[item_id].relevance_score + (score * 0.5))
            if reason not in candidates[item_id].reasons:
                candidates[item_id].reasons.append(reason)
            return

        snippet = f"Test: {test_name}\nDefined in: {test_file.path}\nReason: {reason}"
        candidates[item_id] = ContextItem(
            id=item_id,
            name=test_name,
            item_type=ContextItemType.TEST,
            file_path=test_file.path,
            relevance_score=score,
            reasons=[reason],
            snippet=snippet,
            estimated_tokens=estimate_tokens(snippet),
            metadata={"test_name": test_name, "test_file": test_file.path},
        )
        self.related_tests.add(f"{test_name} ({test_file.path})")

    def _enrich_selected_entities(self, candidates: dict[str, ContextItem]) -> None:
        """Enrich candidates with associated tests, APIs, infrastructure, and security findings."""
        # Find all files referenced in existing candidates
        referenced_file_paths = {item.file_path for item in candidates.values()}

        for f_path in referenced_file_paths:
            f_ent = self.files_by_path.get(f_path)
            if not f_ent:
                continue

            # Associated Tests targeting symbols in this file
            file_syms = self.symbols_by_file_id.get(f_ent.id, [])
            for sym in file_syms:
                for t in self.tests_by_target.get(sym.name, []):
                    t_file = self.files_by_id.get(t.file_id)
                    if t_file:
                        self._add_test_candidate(
                            candidates,
                            t.name,
                            t_file,
                            score=WEIGHT_RELATED_TEST,
                            reason=f"Test '{t.name}' targets symbol '{sym.name}'",
                        )

            # Associated APIs
            for api in self.apis_by_file_id.get(f_ent.id, []):
                if self._is_item_blocked_by_policy(file_path=f_ent.path, category=ContextItemType.API_ENDPOINT):
                    continue
                item_id = f"api:{api.id}"
                if item_id not in candidates:
                    snippet = f"API Route: {api.http_method} {api.route}\nHandler: {api.handler_symbol or 'unknown'} in {f_ent.path}"
                    candidates[item_id] = ContextItem(
                        id=item_id,
                        name=f"{api.http_method} {api.route}",
                        item_type=ContextItemType.API_ENDPOINT,
                        file_path=f_ent.path,
                        relevance_score=WEIGHT_API_ENDPOINT,
                        reasons=[f"API endpoint defined in '{f_ent.path}'"],
                        snippet=snippet,
                        estimated_tokens=estimate_tokens(snippet),
                        metadata={"route": api.route, "method": api.http_method, "handler": api.handler_symbol},
                    )

            # Associated Infrastructure
            for infra in self.infra_by_file_id.get(f_ent.id, []):
                if self._is_item_blocked_by_policy(file_path=f_ent.path, category=ContextItemType.INFRASTRUCTURE):
                    continue
                item_id = f"infra:{infra.id}"
                if item_id not in candidates:
                    snippet = f"Infrastructure: {infra.kind}\nDefined in: {f_ent.path}\nDetails: {infra.details or 'none'}"
                    candidates[item_id] = ContextItem(
                        id=item_id,
                        name=f"{infra.kind} ({f_ent.path})",
                        item_type=ContextItemType.INFRASTRUCTURE,
                        file_path=f_ent.path,
                        relevance_score=WEIGHT_INFRASTRUCTURE,
                        reasons=[f"Infrastructure configuration defined in '{f_ent.path}'"],
                        snippet=snippet,
                        estimated_tokens=estimate_tokens(snippet),
                        metadata={"kind": infra.kind, "path": infra.path, "details": infra.details},
                    )

            # Associated Security Findings
            for sec in self.security_by_file_id.get(f_ent.id, []):
                if self._is_item_blocked_by_policy(file_path=f_ent.path, category=ContextItemType.SECURITY):
                    continue
                item_id = f"sec:{sec.id}"
                if item_id not in candidates:
                    snippet = f"Security Finding: {sec.rule_id} [{sec.severity}]\nFile: {f_ent.path}:{sec.line_number or 1}\nDescription: {sec.description}"
                    candidates[item_id] = ContextItem(
                        id=item_id,
                        name=f"{sec.rule_id} ({f_ent.path})",
                        item_type=ContextItemType.SECURITY,
                        file_path=f_ent.path,
                        relevance_score=WEIGHT_SECURITY_FINDING,
                        reasons=[f"Security finding on '{f_ent.path}' ({sec.severity})"],
                        snippet=snippet,
                        estimated_tokens=estimate_tokens(snippet),
                        metadata={"rule_id": sec.rule_id, "severity": sec.severity, "line_number": sec.line_number},
                    )
