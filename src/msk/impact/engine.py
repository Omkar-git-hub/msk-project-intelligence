"""Core Impact Analysis Engine for MSK."""

from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path

from msk.common.errors import MSKError
from msk.graph.relationships import RelationshipType
from msk.impact.models import (
    ImpactCategory,
    ImpactClassification,
    ImpactItem,
    ImpactReport,
)
from msk.knowledge.models import (
    ApiEndpointEntity,
    DependencyEntity,
    FileEntity,
    InfrastructureEntity,
    RelationshipEntity,
    SymbolEntity,
    TestEntity,
)
from msk.knowledge.repository import KnowledgeRepository


class ImpactEngine:
    """Calculates deterministic direct and transitive impact across the project knowledge model."""

    def __init__(self, repo: KnowledgeRepository, project_id: str, project_root: Path) -> None:
        self.repo = repo
        self.project_id = project_id
        self.project_root = Path(project_root).resolve()

        # Load project entity
        self.project = self.repo.get_project(self.project_id)
        if not self.project:
            with self.repo.db.transaction() as conn:
                row = conn.execute("SELECT * FROM projects WHERE name = ? LIMIT 1", (self.project_id,)).fetchone()
                if not row:
                    row = conn.execute("SELECT * FROM projects LIMIT 1").fetchone()
                if row:
                    from msk.knowledge.models import ProjectEntity

                    self.project = ProjectEntity(
                        id=row["id"],
                        name=row["name"],
                        root_path=row["root_path"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                    self.project_id = self.project.id

        self.project_name = self.project.name if self.project else "Unknown"

        # Load all knowledge entities into memory for performant graph traversal
        self.files = self.repo.get_all_files(self.project_id)
        self.symbols = self.repo.get_all_symbols(self.project_id)
        self.relationships = self.repo.get_all_relationships(self.project_id)
        self.tests = self.repo.get_all_tests(self.project_id)
        self.apis = self.repo.get_all_api_endpoints(self.project_id)
        self.infra = self.repo.get_all_infrastructure(self.project_id)
        self.dependencies = self.repo.get_all_dependencies(self.project_id)

        # Build indexing dictionaries
        self.files_by_id: dict[str, FileEntity] = {f.id: f for f in self.files}
        self.files_by_path: dict[str, FileEntity] = {}
        for f in self.files:
            self.files_by_path[f.path] = f
            norm = f.path.replace("\\", "/")
            self.files_by_path[norm] = f

        self.symbols_by_id: dict[str, SymbolEntity] = {s.id: s for s in self.symbols}
        self.symbols_by_file_id: dict[str, list[SymbolEntity]] = defaultdict(list)
        self.symbols_by_name: dict[str, list[SymbolEntity]] = defaultdict(list)
        for s in self.symbols:
            self.symbols_by_file_id[s.file_id].append(s)
            self.symbols_by_name[s.name].append(s)

        # Relationship adjacency indices
        # in_edges: target_id -> list of edges pointing to target_id (e.g. caller -> callee)
        self.in_edges: dict[str, list[RelationshipEntity]] = defaultdict(list)
        self.out_edges: dict[str, list[RelationshipEntity]] = defaultdict(list)
        for r in self.relationships:
            self.in_edges[r.target_id].append(r)
            self.out_edges[r.source_id].append(r)

        # Entity cross-reference indices
        self.tests_by_target_symbol: dict[str, list[TestEntity]] = defaultdict(list)
        self.tests_by_file_id: dict[str, list[TestEntity]] = defaultdict(list)
        for t in self.tests:
            if t.target_symbol:
                self.tests_by_target_symbol[t.target_symbol].append(t)
            self.tests_by_file_id[t.file_id].append(t)

        self.apis_by_handler: dict[str, list[ApiEndpointEntity]] = defaultdict(list)
        self.apis_by_file_id: dict[str, list[ApiEndpointEntity]] = defaultdict(list)
        for a in self.apis:
            if a.handler_symbol:
                self.apis_by_handler[a.handler_symbol].append(a)
            self.apis_by_file_id[a.file_id].append(a)

        self.infra_by_file_id: dict[str, list[InfrastructureEntity]] = defaultdict(list)
        for i in self.infra:
            self.infra_by_file_id[i.file_id].append(i)

        self.deps_by_target: dict[str, list[DependencyEntity]] = defaultdict(list)
        self.deps_by_source: dict[str, list[DependencyEntity]] = defaultdict(list)
        for d in self.dependencies:
            self.deps_by_target[d.target.lower()].append(d)
            norm_source = d.source.replace("\\", "/")
            self.deps_by_source[norm_source].append(d)

    def _verify_non_empty(self) -> None:
        """Ensure knowledge graph is not empty before querying."""
        if not self.files:
            raise MSKError(
                "MSK project knowledge model is empty.",
                hint="Run 'msk init --force' to index project files.",
            )

    def _normalize_file_path(self, file_path: str) -> str:
        """Normalize file path to POSIX relative path from project root."""
        p = Path(file_path)
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

    def analyze_file(
        self,
        file_path: str,
        max_depth: int = 3,
        direct_only: bool = False,
    ) -> ImpactReport:
        """Analyze potential direct and transitive downstream impact of modifying a project file."""
        self._verify_non_empty()
        rel_path = self._normalize_file_path(file_path)

        # Verify existence in indexed knowledge base or filesystem
        target_file = self.files_by_path.get(rel_path)
        if not target_file:
            disk_exists = (self.project_root / rel_path).exists()
            if disk_exists:
                raise MSKError(
                    f"File '{rel_path}' exists on disk but is not indexed in MSK knowledge model.",
                    hint="Run 'msk update' to index new or modified files.",
                )
            raise MSKError(
                f"File not found in project: '{rel_path}'.",
                hint="Verify the file path relative to project root.",
            )

        depth_limit = 1 if direct_only else max(1, max_depth)
        items_map: dict[tuple[str, str, str], ImpactItem] = {}
        visited_depths: dict[str, int] = {target_file.id: 0}

        # Queue for BFS expansion: (entity_type, entity_id, display_name, file_path, current_depth, path_history)
        queue: deque[tuple[str, str, str, str, int, list[str]]] = deque()

        # -------------------------------------------------------------------
        # 1. Direct Impact (Depth 1)
        # -------------------------------------------------------------------

        # A. Contained Symbols
        contained_symbols = self.symbols_by_file_id.get(target_file.id, [])
        for sym in contained_symbols:
            sym_name = f"{sym.parent_symbol}.{sym.name}" if sym.parent_symbol else sym.name
            item_key = ("symbol", sym_name, target_file.path)
            item = ImpactItem(
                name=sym_name,
                type="symbol",
                file_path=target_file.path,
                category=ImpactCategory.CONTAINED_SYMBOL,
                classification=ImpactClassification.DIRECT,
                depth=1,
                relationship_type=RelationshipType.FILE_CONTAINS_SYMBOL,
                path=[target_file.path, sym_name],
                reason=f"Symbol '{sym_name}' ({sym.type}) is defined in changed file '{target_file.path}'",
                evidence={
                    "edge_type": RelationshipType.FILE_CONTAINS_SYMBOL,
                    "source": target_file.path,
                    "target": sym_name,
                },
            )
            items_map[item_key] = item
            visited_depths[sym.id] = 1
            queue.append(("symbol", sym.id, sym_name, target_file.path, 1, [target_file.path, sym_name]))

        # B. Directly Dependent Files (Files importing this file)
        for rel in self.in_edges.get(target_file.id, []):
            if rel.relationship_type == RelationshipType.FILE_DEPENDS_ON_FILE:
                dep_file = self.files_by_id.get(rel.source_id)
                if dep_file and dep_file.id != target_file.id:
                    item_key = ("file", dep_file.path, dep_file.path)
                    item = ImpactItem(
                        name=dep_file.path,
                        type="file",
                        file_path=dep_file.path,
                        category=ImpactCategory.DEPENDENT_FILE,
                        classification=ImpactClassification.DIRECT,
                        depth=1,
                        relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                        path=[target_file.path, dep_file.path],
                        reason=f"File '{dep_file.path}' directly depends on or imports '{target_file.path}'",
                        evidence={
                            "edge_type": RelationshipType.FILE_DEPENDS_ON_FILE,
                            "source": dep_file.path,
                            "target": target_file.path,
                        },
                    )
                    items_map[item_key] = item
                    visited_depths[dep_file.id] = 1
                    queue.append(
                        ("file", dep_file.id, dep_file.path, dep_file.path, 1, [target_file.path, dep_file.path])
                    )

        # C. Callers of Contained Symbols from other files
        for sym in contained_symbols:
            sym_name = f"{sym.parent_symbol}.{sym.name}" if sym.parent_symbol else sym.name
            for rel in self.in_edges.get(sym.id, []):
                if rel.relationship_type == RelationshipType.SYMBOL_CALLS_SYMBOL:
                    caller_sym = self.symbols_by_id.get(rel.source_id)
                    if caller_sym and caller_sym.file_id != target_file.id:
                        caller_file = self.files_by_id.get(caller_sym.file_id)
                        c_file_path = caller_file.path if caller_file else "unknown"
                        caller_name = (
                            f"{caller_sym.parent_symbol}.{caller_sym.name}"
                            if caller_sym.parent_symbol
                            else caller_sym.name
                        )
                        item_key = ("symbol", caller_name, c_file_path)
                        item = ImpactItem(
                            name=caller_name,
                            type="symbol",
                            file_path=c_file_path,
                            category=ImpactCategory.CALLER,
                            classification=ImpactClassification.DIRECT,
                            depth=1,
                            relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
                            path=[target_file.path, sym_name, caller_name],
                            reason=f"Symbol '{caller_name}' in '{c_file_path}' calls '{sym_name}'",
                            evidence={
                                "edge_type": RelationshipType.SYMBOL_CALLS_SYMBOL,
                                "caller": caller_name,
                                "callee": sym_name,
                            },
                        )
                        items_map[item_key] = item
                        visited_depths[caller_sym.id] = 1
                        queue.append(
                            ("symbol", caller_sym.id, caller_name, c_file_path, 1, [target_file.path, caller_name])
                        )

                        # Also ensure caller file is marked as dependent file
                        if caller_file and caller_file.id != target_file.id:
                            f_key = ("file", caller_file.path, caller_file.path)
                            if f_key not in items_map:
                                items_map[f_key] = ImpactItem(
                                    name=caller_file.path,
                                    type="file",
                                    file_path=caller_file.path,
                                    category=ImpactCategory.DEPENDENT_FILE,
                                    classification=ImpactClassification.DIRECT,
                                    depth=1,
                                    relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                    path=[target_file.path, caller_file.path],
                                    reason=f"File '{caller_file.path}' contains caller '{caller_name}' of '{sym_name}'",
                                    evidence={"caller": caller_name, "target_file": target_file.path},
                                )
                                visited_depths[caller_file.id] = 1
                                queue.append(
                                    (
                                        "file",
                                        caller_file.id,
                                        caller_file.path,
                                        caller_file.path,
                                        1,
                                        [target_file.path, caller_file.path],
                                    )
                                )

        # D. Manifest Dependencies declared in target file (if manifest)
        norm_t_path = target_file.path.replace("\\", "/")
        manifest_deps = self.deps_by_source.get(norm_t_path, [])
        for dep in manifest_deps:
            d_key = ("dependency", dep.target, target_file.path)
            items_map[d_key] = ImpactItem(
                name=dep.target,
                type="dependency",
                file_path=target_file.path,
                category=ImpactCategory.DEPENDENCY,
                classification=ImpactClassification.DIRECT,
                depth=1,
                relationship_type="MANIFEST_DEPENDENCY",
                path=[target_file.path, dep.target],
                reason=f"Package dependency '{dep.target}' (spec: {dep.version_spec or 'any'}) declared in '{target_file.path}'",
                evidence={"package": dep.target, "spec": dep.version_spec, "source": target_file.path},
            )

        # E. Direct Tests
        # 1. Tests targeting any contained symbol
        for sym in contained_symbols:
            for rel in self.in_edges.get(sym.id, []):
                if rel.relationship_type == RelationshipType.TEST_TARGETS_SYMBOL:
                    t_sym = self.symbols_by_id.get(rel.source_id)
                    if t_sym:
                        t_file = self.files_by_id.get(t_sym.file_id)
                        t_path = t_file.path if t_file else "unknown"
                        t_key = ("test", t_sym.name, t_path)
                        items_map[t_key] = ImpactItem(
                            name=t_sym.name,
                            type="test",
                            file_path=t_path,
                            category=ImpactCategory.TEST,
                            classification=ImpactClassification.DIRECT,
                            depth=1,
                            relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                            path=[target_file.path, sym.name, t_sym.name],
                            reason=f"Test '{t_sym.name}' in '{t_path}' targets symbol '{sym.name}'",
                            evidence={
                                "test": t_sym.name,
                                "target_symbol": sym.name,
                                "edge": RelationshipType.TEST_TARGETS_SYMBOL,
                            },
                        )

            # Tests matching target_symbol from tests table
            for t_ent in self.tests_by_target_symbol.get(sym.name, []):
                t_file = self.files_by_id.get(t_ent.file_id)
                t_path = t_file.path if t_file else "unknown"
                t_key = ("test", t_ent.name, t_path)
                if t_key not in items_map:
                    items_map[t_key] = ImpactItem(
                        name=t_ent.name,
                        type="test",
                        file_path=t_path,
                        category=ImpactCategory.TEST,
                        classification=ImpactClassification.DIRECT,
                        depth=1,
                        relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                        path=[target_file.path, sym.name, t_ent.name],
                        reason=f"Test '{t_ent.name}' ({t_ent.framework or 'test'}) targets symbol '{sym.name}'",
                        evidence={"test": t_ent.name, "framework": t_ent.framework, "target_symbol": sym.name},
                    )

        # 2. Tests in test files that depend directly on target file
        for rel in self.in_edges.get(target_file.id, []):
            if rel.relationship_type == RelationshipType.FILE_DEPENDS_ON_FILE:
                dep_f = self.files_by_id.get(rel.source_id)
                if dep_f and dep_f.is_test:
                    for t_ent in self.tests_by_file_id.get(dep_f.id, []):
                        t_key = ("test", t_ent.name, dep_f.path)
                        if t_key not in items_map:
                            items_map[t_key] = ImpactItem(
                                name=t_ent.name,
                                type="test",
                                file_path=dep_f.path,
                                category=ImpactCategory.TEST,
                                classification=ImpactClassification.DIRECT,
                                depth=1,
                                relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                path=[target_file.path, dep_f.path, t_ent.name],
                                reason=f"Test '{t_ent.name}' is in test file '{dep_f.path}' which depends on '{target_file.path}'",
                                evidence={"test": t_ent.name, "test_file": dep_f.path},
                            )

        # F. Direct API Endpoints
        for sym in contained_symbols:
            for api in self.apis_by_handler.get(sym.name, []):
                if api.file_id == target_file.id or sym.is_api_endpoint:
                    a_name = f"{api.http_method} {api.route}"
                    a_key = ("api_endpoint", a_name, target_file.path)
                    items_map[a_key] = ImpactItem(
                        name=a_name,
                        type="api_endpoint",
                        file_path=target_file.path,
                        category=ImpactCategory.API_ENDPOINT,
                        classification=ImpactClassification.DIRECT,
                        depth=1,
                        relationship_type="API_HANDLER",
                        path=[target_file.path, sym.name, a_name],
                        reason=f"API endpoint '{a_name}' is defined in '{target_file.path}' (handler: {sym.name})",
                        evidence={"route": api.route, "method": api.http_method, "handler": sym.name},
                    )

        # G. Infrastructure
        for infra in self.infra_by_file_id.get(target_file.id, []):
            i_key = ("infrastructure", infra.kind, target_file.path)
            items_map[i_key] = ImpactItem(
                name=f"{infra.kind} ({target_file.path})",
                type="infrastructure",
                file_path=target_file.path,
                category=ImpactCategory.INFRASTRUCTURE,
                classification=ImpactClassification.DIRECT,
                depth=1,
                relationship_type="INFRASTRUCTURE_DEFINITION",
                path=[target_file.path],
                reason=f"Infrastructure configuration '{infra.kind}' is defined in '{target_file.path}'",
                evidence={"kind": infra.kind, "path": infra.path, "details": infra.details},
            )

        # -------------------------------------------------------------------
        # 2. Transitive Traversal (Depth 2 to depth_limit)
        # -------------------------------------------------------------------
        while queue:
            curr_type, curr_id, curr_name, curr_fpath, curr_depth, curr_path = queue.popleft()
            if curr_depth >= depth_limit:
                continue

            next_depth = curr_depth + 1

            if curr_type == "symbol":
                curr_sym = self.symbols_by_id.get(curr_id)
                if not curr_sym:
                    continue

                # A. Transitive Callers: who calls curr_sym?
                for rel in self.in_edges.get(curr_sym.id, []):
                    if rel.relationship_type == RelationshipType.SYMBOL_CALLS_SYMBOL:
                        up_sym = self.symbols_by_id.get(rel.source_id)
                        if up_sym and up_sym.id != curr_sym.id:
                            up_file = self.files_by_id.get(up_sym.file_id)
                            up_fpath = up_file.path if up_file else "unknown"
                            up_name = f"{up_sym.parent_symbol}.{up_sym.name}" if up_sym.parent_symbol else up_sym.name
                            item_key = ("symbol", up_name, up_fpath)

                            # Record if not visited or found at lower depth
                            prev_depth = visited_depths.get(up_sym.id)
                            if prev_depth is None or next_depth < prev_depth:
                                visited_depths[up_sym.id] = next_depth
                                new_path = curr_path + [up_name]
                                items_map[item_key] = ImpactItem(
                                    name=up_name,
                                    type="symbol",
                                    file_path=up_fpath,
                                    category=ImpactCategory.CALLER,
                                    classification=ImpactClassification.TRANSITIVE,
                                    depth=next_depth,
                                    relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
                                    path=new_path,
                                    reason=f"Symbol '{up_name}' calls '{curr_name}' along dependency path",
                                    evidence={"caller": up_name, "callee": curr_name, "path": new_path},
                                )
                                queue.append(("symbol", up_sym.id, up_name, up_fpath, next_depth, new_path))

                            # Also record containing file of transitive caller
                            if up_file and up_file.id != target_file.id:
                                f_key = ("file", up_file.path, up_file.path)
                                f_prev = visited_depths.get(up_file.id)
                                if f_prev is None or next_depth < f_prev:
                                    visited_depths[up_file.id] = next_depth
                                    f_path = curr_path + [up_file.path]
                                    items_map[f_key] = ImpactItem(
                                        name=up_file.path,
                                        type="file",
                                        file_path=up_file.path,
                                        category=ImpactCategory.DEPENDENT_FILE,
                                        classification=ImpactClassification.TRANSITIVE,
                                        depth=next_depth,
                                        relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                        path=f_path,
                                        reason=f"File '{up_file.path}' contains transitive caller '{up_name}'",
                                        evidence={"caller": up_name, "file": up_file.path},
                                    )
                                    queue.append(("file", up_file.id, up_file.path, up_file.path, next_depth, f_path))

                # B. Transitive Tests targeting curr_sym
                for t_ent in self.tests_by_target_symbol.get(curr_sym.name, []):
                    t_file = self.files_by_id.get(t_ent.file_id)
                    t_path = t_file.path if t_file else "unknown"
                    t_key = ("test", t_ent.name, t_path)
                    if t_key not in items_map:
                        items_map[t_key] = ImpactItem(
                            name=t_ent.name,
                            type="test",
                            file_path=t_path,
                            category=ImpactCategory.TEST,
                            classification=ImpactClassification.TRANSITIVE,
                            depth=next_depth,
                            relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                            path=curr_path + [t_ent.name],
                            reason=f"Test '{t_ent.name}' targets '{curr_name}' affected transitively",
                            evidence={"test": t_ent.name, "target_symbol": curr_sym.name},
                        )

                # C. Transitive API Endpoints handled by curr_sym
                for api in self.apis_by_handler.get(curr_sym.name, []):
                    a_name = f"{api.http_method} {api.route}"
                    a_key = ("api_endpoint", a_name, curr_fpath)
                    if a_key not in items_map:
                        items_map[a_key] = ImpactItem(
                            name=a_name,
                            type="api_endpoint",
                            file_path=curr_fpath,
                            category=ImpactCategory.API_ENDPOINT,
                            classification=ImpactClassification.TRANSITIVE,
                            depth=next_depth,
                            relationship_type="API_HANDLER",
                            path=curr_path + [a_name],
                            reason=f"API endpoint '{a_name}' handled by '{curr_name}' affected transitively",
                            evidence={"route": api.route, "method": api.http_method, "handler": curr_sym.name},
                        )

            elif curr_type == "file":
                curr_file = self.files_by_id.get(curr_id)
                if not curr_file:
                    continue

                # Transitive Dependent Files: files depending on curr_file
                for rel in self.in_edges.get(curr_file.id, []):
                    if rel.relationship_type == RelationshipType.FILE_DEPENDS_ON_FILE:
                        dep_f = self.files_by_id.get(rel.source_id)
                        if dep_f and dep_f.id != target_file.id:
                            f_key = ("file", dep_f.path, dep_f.path)
                            prev_d = visited_depths.get(dep_f.id)
                            if prev_d is None or next_depth < prev_d:
                                visited_depths[dep_f.id] = next_depth
                                new_f_path = curr_path + [dep_f.path]
                                items_map[f_key] = ImpactItem(
                                    name=dep_f.path,
                                    type="file",
                                    file_path=dep_f.path,
                                    category=ImpactCategory.DEPENDENT_FILE,
                                    classification=ImpactClassification.TRANSITIVE,
                                    depth=next_depth,
                                    relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                    path=new_f_path,
                                    reason=f"File '{dep_f.path}' depends on '{curr_name}'",
                                    evidence={"source": dep_f.path, "target": curr_name},
                                )
                                queue.append(("file", dep_f.id, dep_f.path, dep_f.path, next_depth, new_f_path))

                                # If dep_f is a test file, record its tests
                                if dep_f.is_test:
                                    for t_ent in self.tests_by_file_id.get(dep_f.id, []):
                                        t_key = ("test", t_ent.name, dep_f.path)
                                        if t_key not in items_map:
                                            items_map[t_key] = ImpactItem(
                                                name=t_ent.name,
                                                type="test",
                                                file_path=dep_f.path,
                                                category=ImpactCategory.TEST,
                                                classification=ImpactClassification.TRANSITIVE,
                                                depth=next_depth,
                                                relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                                path=new_f_path + [t_ent.name],
                                                reason=f"Test '{t_ent.name}' is in test file '{dep_f.path}' which depends transitively on changed file",
                                                evidence={"test": t_ent.name, "test_file": dep_f.path},
                                            )

        return self._build_report(
            target_type="file",
            target_name=target_file.path,
            target_file=target_file.path,
            max_depth=depth_limit,
            items=list(items_map.values()),
        )

    def analyze_symbol(
        self,
        symbol_name: str,
        file_path: str | None = None,
        max_depth: int = 3,
        direct_only: bool = False,
    ) -> ImpactReport:
        """Analyze potential direct and transitive downstream impact of modifying a symbol."""
        self._verify_non_empty()
        depth_limit = 1 if direct_only else max(1, max_depth)

        # 1. Resolve matching symbol
        target_sym = self._resolve_symbol(symbol_name, file_path)
        target_file = self.files_by_id[target_sym.file_id]
        display_sym_name = (
            f"{target_sym.parent_symbol}.{target_sym.name}" if target_sym.parent_symbol else target_sym.name
        )

        items_map: dict[tuple[str, str, str], ImpactItem] = {}
        visited_depths: dict[str, int] = {target_sym.id: 0}
        queue: deque[tuple[str, str, str, str, int, list[str]]] = deque()

        # -------------------------------------------------------------------
        # 1. Direct Impact (Depth 1)
        # -------------------------------------------------------------------

        # A. Containing File
        f_key = ("file", target_file.path, target_file.path)
        items_map[f_key] = ImpactItem(
            name=target_file.path,
            type="file",
            file_path=target_file.path,
            category=ImpactCategory.CONTAINED_SYMBOL,
            classification=ImpactClassification.DIRECT,
            depth=1,
            relationship_type=RelationshipType.FILE_CONTAINS_SYMBOL,
            path=[display_sym_name, target_file.path],
            reason=f"File '{target_file.path}' defines symbol '{display_sym_name}'",
            evidence={"file": target_file.path, "symbol": display_sym_name},
        )

        # B. Parent / Child Symbols
        if target_sym.type == "class":
            # Child methods / constructors
            for child in self.symbols_by_file_id.get(target_sym.file_id, []):
                if child.parent_symbol == target_sym.name and child.id != target_sym.id:
                    c_name = f"{target_sym.name}.{child.name}"
                    c_key = ("symbol", c_name, target_file.path)
                    items_map[c_key] = ImpactItem(
                        name=c_name,
                        type="symbol",
                        file_path=target_file.path,
                        category=ImpactCategory.CHILD_SYMBOL,
                        classification=ImpactClassification.DIRECT,
                        depth=1,
                        relationship_type="CLASS_MEMBER",
                        path=[display_sym_name, c_name],
                        reason=f"Method '{child.name}' is a member of class '{target_sym.name}'",
                        evidence={"class": target_sym.name, "member": child.name},
                    )
                    visited_depths[child.id] = 1
                    queue.append(("symbol", child.id, c_name, target_file.path, 1, [display_sym_name, c_name]))

        elif target_sym.parent_symbol:
            # Parent class
            for parent in self.symbols_by_file_id.get(target_sym.file_id, []):
                if parent.name == target_sym.parent_symbol and parent.id != target_sym.id:
                    p_key = ("symbol", parent.name, target_file.path)
                    items_map[p_key] = ImpactItem(
                        name=parent.name,
                        type="symbol",
                        file_path=target_file.path,
                        category=ImpactCategory.PARENT_SYMBOL,
                        classification=ImpactClassification.DIRECT,
                        depth=1,
                        relationship_type="CLASS_DECLARATION",
                        path=[display_sym_name, parent.name],
                        reason=f"Class '{parent.name}' declares method '{target_sym.name}'",
                        evidence={"class": parent.name, "member": target_sym.name},
                    )
                    visited_depths[parent.id] = 1
                    queue.append(
                        ("symbol", parent.id, parent.name, target_file.path, 1, [display_sym_name, parent.name])
                    )

        # C. Direct Callers: symbols that call target_sym
        for rel in self.in_edges.get(target_sym.id, []):
            if rel.relationship_type == RelationshipType.SYMBOL_CALLS_SYMBOL:
                caller = self.symbols_by_id.get(rel.source_id)
                if caller and caller.id != target_sym.id:
                    c_file = self.files_by_id.get(caller.file_id)
                    c_fpath = c_file.path if c_file else "unknown"
                    c_name = f"{caller.parent_symbol}.{caller.name}" if caller.parent_symbol else caller.name
                    item_key = ("symbol", c_name, c_fpath)
                    items_map[item_key] = ImpactItem(
                        name=c_name,
                        type="symbol",
                        file_path=c_fpath,
                        category=ImpactCategory.CALLER,
                        classification=ImpactClassification.DIRECT,
                        depth=1,
                        relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
                        path=[display_sym_name, c_name],
                        reason=f"Symbol '{c_name}' in '{c_fpath}' directly calls '{display_sym_name}'",
                        evidence={
                            "caller": c_name,
                            "callee": display_sym_name,
                            "edge": RelationshipType.SYMBOL_CALLS_SYMBOL,
                        },
                    )
                    visited_depths[caller.id] = 1
                    queue.append(("symbol", caller.id, c_name, c_fpath, 1, [display_sym_name, c_name]))

                    # Also mark caller's file as affected
                    if c_file and c_file.id != target_file.id:
                        cf_key = ("file", c_file.path, c_file.path)
                        if cf_key not in items_map:
                            items_map[cf_key] = ImpactItem(
                                name=c_file.path,
                                type="file",
                                file_path=c_file.path,
                                category=ImpactCategory.DEPENDENT_FILE,
                                classification=ImpactClassification.DIRECT,
                                depth=1,
                                relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                path=[display_sym_name, c_file.path],
                                reason=f"File '{c_file.path}' contains direct caller '{c_name}' of '{display_sym_name}'",
                                evidence={"caller": c_name, "file": c_file.path},
                            )
                            visited_depths[c_file.id] = 1
                            queue.append(
                                ("file", c_file.id, c_file.path, c_file.path, 1, [display_sym_name, c_file.path])
                            )

        # D. Importers: files that import target_sym
        for rel in self.in_edges.get(target_sym.id, []):
            if rel.relationship_type == RelationshipType.SYMBOL_IMPORTS_SYMBOL:
                imp_file = self.files_by_id.get(rel.source_id)
                if imp_file and imp_file.id != target_file.id:
                    imp_key = ("file", imp_file.path, imp_file.path)
                    if imp_key not in items_map:
                        items_map[imp_key] = ImpactItem(
                            name=imp_file.path,
                            type="file",
                            file_path=imp_file.path,
                            category=ImpactCategory.IMPORTER,
                            classification=ImpactClassification.DIRECT,
                            depth=1,
                            relationship_type=RelationshipType.SYMBOL_IMPORTS_SYMBOL,
                            path=[display_sym_name, imp_file.path],
                            reason=f"File '{imp_file.path}' directly imports symbol '{display_sym_name}'",
                            evidence={"importer_file": imp_file.path, "symbol": display_sym_name},
                        )
                        visited_depths[imp_file.id] = 1
                        queue.append(
                            ("file", imp_file.id, imp_file.path, imp_file.path, 1, [display_sym_name, imp_file.path])
                        )

        # E. Direct Tests Targeting target_sym
        for rel in self.in_edges.get(target_sym.id, []):
            if rel.relationship_type == RelationshipType.TEST_TARGETS_SYMBOL:
                t_sym = self.symbols_by_id.get(rel.source_id)
                if t_sym:
                    t_file = self.files_by_id.get(t_sym.file_id)
                    t_path = t_file.path if t_file else "unknown"
                    t_key = ("test", t_sym.name, t_path)
                    items_map[t_key] = ImpactItem(
                        name=t_sym.name,
                        type="test",
                        file_path=t_path,
                        category=ImpactCategory.TEST,
                        classification=ImpactClassification.DIRECT,
                        depth=1,
                        relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                        path=[display_sym_name, t_sym.name],
                        reason=f"Test '{t_sym.name}' in '{t_path}' directly targets '{display_sym_name}'",
                        evidence={"test": t_sym.name, "target_symbol": display_sym_name},
                    )

        for t_ent in self.tests_by_target_symbol.get(target_sym.name, []):
            t_file = self.files_by_id.get(t_ent.file_id)
            t_path = t_file.path if t_file else "unknown"
            t_key = ("test", t_ent.name, t_path)
            if t_key not in items_map:
                items_map[t_key] = ImpactItem(
                    name=t_ent.name,
                    type="test",
                    file_path=t_path,
                    category=ImpactCategory.TEST,
                    classification=ImpactClassification.DIRECT,
                    depth=1,
                    relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                    path=[display_sym_name, t_ent.name],
                    reason=f"Test '{t_ent.name}' targets symbol '{display_sym_name}'",
                    evidence={"test": t_ent.name, "target_symbol": display_sym_name},
                )

        # F. Direct API Endpoints handled by target_sym
        for api in self.apis_by_handler.get(target_sym.name, []):
            if api.file_id == target_sym.file_id or target_sym.is_api_endpoint:
                a_name = f"{api.http_method} {api.route}"
                a_key = ("api_endpoint", a_name, target_file.path)
                items_map[a_key] = ImpactItem(
                    name=a_name,
                    type="api_endpoint",
                    file_path=target_file.path,
                    category=ImpactCategory.API_ENDPOINT,
                    classification=ImpactClassification.DIRECT,
                    depth=1,
                    relationship_type="API_HANDLER",
                    path=[display_sym_name, a_name],
                    reason=f"API endpoint '{a_name}' is handled by '{display_sym_name}'",
                    evidence={"route": api.route, "method": api.http_method, "handler": target_sym.name},
                )

        # -------------------------------------------------------------------
        # 2. Transitive Traversal (Depth 2 to depth_limit)
        # -------------------------------------------------------------------
        while queue:
            curr_type, curr_id, curr_name, curr_fpath, curr_depth, curr_path = queue.popleft()
            if curr_depth >= depth_limit:
                continue

            next_depth = curr_depth + 1

            if curr_type == "symbol":
                curr_sym = self.symbols_by_id.get(curr_id)
                if not curr_sym:
                    continue

                # Callers of curr_sym
                for rel in self.in_edges.get(curr_sym.id, []):
                    if rel.relationship_type == RelationshipType.SYMBOL_CALLS_SYMBOL:
                        up_sym = self.symbols_by_id.get(rel.source_id)
                        if up_sym and up_sym.id != target_sym.id and up_sym.id != curr_sym.id:
                            up_file = self.files_by_id.get(up_sym.file_id)
                            up_fpath = up_file.path if up_file else "unknown"
                            up_name = f"{up_sym.parent_symbol}.{up_sym.name}" if up_sym.parent_symbol else up_sym.name
                            item_key = ("symbol", up_name, up_fpath)

                            prev_depth = visited_depths.get(up_sym.id)
                            if prev_depth is None or next_depth < prev_depth:
                                visited_depths[up_sym.id] = next_depth
                                new_path = curr_path + [up_name]
                                items_map[item_key] = ImpactItem(
                                    name=up_name,
                                    type="symbol",
                                    file_path=up_fpath,
                                    category=ImpactCategory.CALLER,
                                    classification=ImpactClassification.TRANSITIVE,
                                    depth=next_depth,
                                    relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
                                    path=new_path,
                                    reason=f"Symbol '{up_name}' calls '{curr_name}' along call chain",
                                    evidence={"caller": up_name, "callee": curr_name, "path": new_path},
                                )
                                queue.append(("symbol", up_sym.id, up_name, up_fpath, next_depth, new_path))

                            # Containing file of transitive caller
                            if up_file and up_file.id != target_file.id:
                                f_key = ("file", up_file.path, up_file.path)
                                f_prev = visited_depths.get(up_file.id)
                                if f_prev is None or next_depth < f_prev:
                                    visited_depths[up_file.id] = next_depth
                                    f_path = curr_path + [up_file.path]
                                    items_map[f_key] = ImpactItem(
                                        name=up_file.path,
                                        type="file",
                                        file_path=up_file.path,
                                        category=ImpactCategory.DEPENDENT_FILE,
                                        classification=ImpactClassification.TRANSITIVE,
                                        depth=next_depth,
                                        relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                        path=f_path,
                                        reason=f"File '{up_file.path}' contains transitive caller '{up_name}'",
                                        evidence={"caller": up_name, "file": up_file.path},
                                    )
                                    queue.append(("file", up_file.id, up_file.path, up_file.path, next_depth, f_path))

                # Transitive tests
                for t_ent in self.tests_by_target_symbol.get(curr_sym.name, []):
                    t_file = self.files_by_id.get(t_ent.file_id)
                    t_path = t_file.path if t_file else "unknown"
                    t_key = ("test", t_ent.name, t_path)
                    if t_key not in items_map:
                        items_map[t_key] = ImpactItem(
                            name=t_ent.name,
                            type="test",
                            file_path=t_path,
                            category=ImpactCategory.TEST,
                            classification=ImpactClassification.TRANSITIVE,
                            depth=next_depth,
                            relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                            path=curr_path + [t_ent.name],
                            reason=f"Test '{t_ent.name}' targets '{curr_name}' affected transitively",
                            evidence={"test": t_ent.name, "target_symbol": curr_sym.name},
                        )

                # Transitive APIs
                for api in self.apis_by_handler.get(curr_sym.name, []):
                    a_name = f"{api.http_method} {api.route}"
                    a_key = ("api_endpoint", a_name, curr_fpath)
                    if a_key not in items_map:
                        items_map[a_key] = ImpactItem(
                            name=a_name,
                            type="api_endpoint",
                            file_path=curr_fpath,
                            category=ImpactCategory.API_ENDPOINT,
                            classification=ImpactClassification.TRANSITIVE,
                            depth=next_depth,
                            relationship_type="API_HANDLER",
                            path=curr_path + [a_name],
                            reason=f"API endpoint '{a_name}' handled by '{curr_name}' affected transitively",
                            evidence={"route": api.route, "method": api.http_method, "handler": curr_sym.name},
                        )

            elif curr_type == "file":
                curr_file = self.files_by_id.get(curr_id)
                if not curr_file:
                    continue

                for rel in self.in_edges.get(curr_file.id, []):
                    if rel.relationship_type == RelationshipType.FILE_DEPENDS_ON_FILE:
                        dep_f = self.files_by_id.get(rel.source_id)
                        if dep_f and dep_f.id != target_file.id:
                            f_key = ("file", dep_f.path, dep_f.path)
                            prev_d = visited_depths.get(dep_f.id)
                            if prev_d is None or next_depth < prev_d:
                                visited_depths[dep_f.id] = next_depth
                                new_f_path = curr_path + [dep_f.path]
                                items_map[f_key] = ImpactItem(
                                    name=dep_f.path,
                                    type="file",
                                    file_path=dep_f.path,
                                    category=ImpactCategory.DEPENDENT_FILE,
                                    classification=ImpactClassification.TRANSITIVE,
                                    depth=next_depth,
                                    relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                    path=new_f_path,
                                    reason=f"File '{dep_f.path}' depends on '{curr_name}'",
                                    evidence={"source": dep_f.path, "target": curr_name},
                                )
                                queue.append(("file", dep_f.id, dep_f.path, dep_f.path, next_depth, new_f_path))

        return self._build_report(
            target_type="symbol",
            target_name=display_sym_name,
            target_file=target_file.path,
            max_depth=depth_limit,
            items=list(items_map.values()),
        )

    def analyze_dependency(
        self,
        dependency_name: str,
        max_depth: int = 3,
        direct_only: bool = False,
    ) -> ImpactReport:
        """Analyze potential direct and transitive downstream impact of modifying an external dependency."""
        self._verify_non_empty()
        dep_name_clean = dependency_name.strip()
        dep_name_lower = dep_name_clean.lower()
        depth_limit = 1 if direct_only else max(1, max_depth)

        # Lookup in manifest dependencies
        matched_deps = self.deps_by_target.get(dep_name_lower, [])

        # Also search for files directly importing this dependency
        importing_files: list[FileEntity] = []
        for f in self.files:
            for imp_name in f.imports:
                if imp_name.lower() == dep_name_lower:
                    importing_files.append(f)
                    break

        if not matched_deps and not importing_files:
            raise MSKError(
                f"Dependency '{dep_name_clean}' was not found in project manifests or file imports.",
                hint="Check dependency name in requirements.txt, pyproject.toml, package.json, etc.",
            )

        items_map: dict[tuple[str, str, str], ImpactItem] = {}
        visited_depths: dict[str, int] = {}
        queue: deque[tuple[str, str, str, str, int, list[str]]] = deque()

        # 1. Direct manifest source files
        for dep in matched_deps:
            m_path = dep.source.replace("\\", "/")
            f_key = ("file", m_path, m_path)
            items_map[f_key] = ImpactItem(
                name=m_path,
                type="file",
                file_path=m_path,
                category=ImpactCategory.DEPENDENCY,
                classification=ImpactClassification.DIRECT,
                depth=1,
                relationship_type="MANIFEST_DECLARATION",
                path=[dep_name_clean, m_path],
                reason=f"Manifest file '{m_path}' declares dependency '{dep.target}' (spec: {dep.version_spec or 'any'})",
                evidence={"package": dep.target, "version_spec": dep.version_spec, "source": m_path},
            )

        # 2. Files importing the dependency
        for f in importing_files:
            f_key = ("file", f.path, f.path)
            items_map[f_key] = ImpactItem(
                name=f.path,
                type="file",
                file_path=f.path,
                category=ImpactCategory.IMPORTER,
                classification=ImpactClassification.DIRECT,
                depth=1,
                relationship_type="PACKAGE_IMPORT",
                path=[dep_name_clean, f.path],
                reason=f"File '{f.path}' directly imports package '{dep_name_clean}'",
                evidence={"package": dep_name_clean, "file": f.path},
            )
            visited_depths[f.id] = 1
            queue.append(("file", f.id, f.path, f.path, 1, [dep_name_clean, f.path]))

        # 3. Transitive Traversal
        while queue:
            curr_type, curr_id, curr_name, curr_fpath, curr_depth, curr_path = queue.popleft()
            if curr_depth >= depth_limit:
                continue

            next_depth = curr_depth + 1
            if curr_type == "file":
                curr_file = self.files_by_id.get(curr_id)
                if not curr_file:
                    continue

                for rel in self.in_edges.get(curr_file.id, []):
                    if rel.relationship_type == RelationshipType.FILE_DEPENDS_ON_FILE:
                        dep_f = self.files_by_id.get(rel.source_id)
                        if dep_f:
                            f_key = ("file", dep_f.path, dep_f.path)
                            prev_d = visited_depths.get(dep_f.id)
                            if prev_d is None or next_depth < prev_d:
                                visited_depths[dep_f.id] = next_depth
                                new_path = curr_path + [dep_f.path]
                                items_map[f_key] = ImpactItem(
                                    name=dep_f.path,
                                    type="file",
                                    file_path=dep_f.path,
                                    category=ImpactCategory.DEPENDENT_FILE,
                                    classification=ImpactClassification.TRANSITIVE,
                                    depth=next_depth,
                                    relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                    path=new_path,
                                    reason=f"File '{dep_f.path}' depends on '{curr_name}'",
                                    evidence={"source": dep_f.path, "target": curr_name},
                                )
                                queue.append(("file", dep_f.id, dep_f.path, dep_f.path, next_depth, new_path))

                                if dep_f.is_test:
                                    for t_ent in self.tests_by_file_id.get(dep_f.id, []):
                                        t_key = ("test", t_ent.name, dep_f.path)
                                        if t_key not in items_map:
                                            items_map[t_key] = ImpactItem(
                                                name=t_ent.name,
                                                type="test",
                                                file_path=dep_f.path,
                                                category=ImpactCategory.TEST,
                                                classification=ImpactClassification.TRANSITIVE,
                                                depth=next_depth,
                                                relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                                                path=new_path + [t_ent.name],
                                                reason=f"Test '{t_ent.name}' is in test file '{dep_f.path}' which depends on '{curr_name}'",
                                                evidence={"test": t_ent.name, "test_file": dep_f.path},
                                            )

        return self._build_report(
            target_type="dependency",
            target_name=dep_name_clean,
            target_file=matched_deps[0].source if matched_deps else None,
            max_depth=depth_limit,
            items=list(items_map.values()),
        )

    def _resolve_symbol(self, symbol_name: str, file_path: str | None = None) -> SymbolEntity:
        """Resolve a symbol name, optionally disambiguated by file path."""
        target_file_id: str | None = None
        if file_path:
            rel_p = self._normalize_file_path(file_path)
            f_ent = self.files_by_path.get(rel_p)
            if not f_ent:
                disk_exists = (self.project_root / rel_p).exists()
                if disk_exists:
                    raise MSKError(
                        f"File '{rel_p}' exists on disk but is not indexed in MSK knowledge model.",
                        hint="Run 'msk update' to index new or modified files.",
                    )
                raise MSKError(
                    f"File not found in project: '{rel_p}'.",
                    hint="Verify the file path relative to project root.",
                )
            target_file_id = f_ent.id

        # Match exact symbol name or qualified name (Parent.Child)
        candidates: list[SymbolEntity] = []
        is_qualified = "." in symbol_name
        parent_part, child_part = ("", "")
        if is_qualified:
            parent_part, child_part = symbol_name.split(".", 1)

        pool = self.symbols_by_file_id.get(target_file_id, []) if target_file_id else self.symbols

        for s in pool:
            if is_qualified:
                if s.parent_symbol == parent_part and s.name == child_part:
                    candidates.append(s)
            else:
                if s.name == symbol_name:
                    candidates.append(s)

        if not candidates:
            if file_path:
                raise MSKError(
                    f"Symbol '{symbol_name}' was not found in file '{file_path}'.",
                    hint="Verify symbol spelling or run 'msk update' if newly added.",
                )
            raise MSKError(
                f"Symbol '{symbol_name}' was not found in project knowledge model.",
                hint="Verify symbol spelling or run 'msk update' if newly added.",
            )

        if len(candidates) > 1:
            files_with_symbol = sorted({self.files_by_id[c.file_id].path for c in candidates})
            if len(files_with_symbol) > 1:
                files_list = "\n".join(f"  - {f}" for f in files_with_symbol)
                raise MSKError(
                    f"Ambiguous symbol '{symbol_name}' found in multiple files:\n{files_list}\n"
                    f"Please specify --file to disambiguate.",
                    hint=f"e.g. msk impact --symbol {symbol_name} --file {files_with_symbol[0]}",
                )

        return candidates[0]

    def _build_report(
        self,
        target_type: str,
        target_name: str,
        target_file: str | None,
        max_depth: int,
        items: list[ImpactItem],
    ) -> ImpactReport:
        """Deduplicate and sort items into a deterministic ImpactReport."""
        # Stable sorting key
        category_order = {
            ImpactCategory.CONTAINED_SYMBOL: 0,
            ImpactCategory.PARENT_SYMBOL: 1,
            ImpactCategory.CHILD_SYMBOL: 2,
            ImpactCategory.CALLER: 3,
            ImpactCategory.IMPORTER: 4,
            ImpactCategory.DEPENDENT_FILE: 5,
            ImpactCategory.TEST: 6,
            ImpactCategory.API_ENDPOINT: 7,
            ImpactCategory.INFRASTRUCTURE: 8,
            ImpactCategory.DEPENDENCY: 9,
        }

        def sort_key(item: ImpactItem) -> tuple[int, int, str, str, str]:
            return (
                item.depth,
                category_order.get(item.category, 50),  # type: ignore[arg-type]
                item.type,
                item.file_path,
                item.name,
            )

        items.sort(key=sort_key)

        direct_count = sum(1 for i in items if i.classification == ImpactClassification.DIRECT)
        transitive_count = sum(1 for i in items if i.classification == ImpactClassification.TRANSITIVE)

        summary_by_type: dict[str, int] = defaultdict(int)
        summary_by_category: dict[str, int] = defaultdict(int)
        for i in items:
            summary_by_type[i.type] += 1
            summary_by_category[str(i.category)] += 1

        now_iso = datetime.now(UTC).isoformat()

        return ImpactReport(
            target_type=target_type,
            target_name=target_name,
            target_file=target_file,
            project_name=self.project_name,
            analyzed_at=now_iso,
            max_depth=max_depth,
            total_affected=len(items),
            direct_count=direct_count,
            transitive_count=transitive_count,
            summary_by_type=dict(sorted(summary_by_type.items())),
            summary_by_category=dict(sorted(summary_by_category.items())),
            items=items,
            warnings=[],
        )
