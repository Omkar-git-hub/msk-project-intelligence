"""Implementation of incremental 'msk update' command."""

from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from msk.analyzer.analyzer import analyze_file
from msk.common.errors import NotAProjectError
from msk.common.hashing import sha256_bytes
from msk.config.loader import get_msk_dir, load_project_config, save_project_config
from msk.knowledge.builder import KnowledgeBuilder
from msk.knowledge.repository import KnowledgeRepository
from msk.project.detector import find_project_root
from msk.project.scanner import ProjectScanner
from msk.storage.database import DatabaseManager

console = Console()


def run_update(root: Path | None = None, verbose: bool = False) -> None:
    """Incrementally update project knowledge based on detected file changes."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    if not msk_dir.is_dir() or not db_path.is_file():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' first.",
        )

    config = load_project_config(project_root)
    if not config:
        raise NotAProjectError(
            f"MSK configuration missing at {msk_dir / 'project.json'}",
            hint="Run 'msk init --force' to recreate.",
        )

    db_manager = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_manager)

    # 1. Fetch currently recorded files
    recorded_files = {f.path: f for f in repo.get_all_files(config.id)}

    # 2. Scan current workspace
    scanner = ProjectScanner(project_root)
    scanned = {sf.relative_path: sf for sf in scanner.scan()}

    added_paths = set(scanned.keys()) - set(recorded_files.keys())
    deleted_paths = set(recorded_files.keys()) - set(scanned.keys())
    common_paths = set(scanned.keys()) & set(recorded_files.keys())

    modified_paths: set[str] = set()
    for path in common_paths:
        sf = scanned[path]
        rec = recorded_files[path]
        try:
            current_hash = sha256_bytes(sf.absolute_path.read_bytes())
            if current_hash != rec.content_hash:
                modified_paths.add(path)
        except OSError:
            modified_paths.add(path)

    total_changes = len(added_paths) + len(modified_paths) + len(deleted_paths)

    if total_changes == 0:
        console.print("[bold green][OK] Everything up to date.[/bold green] No changes detected.")
        return

    console.print(f"[bold cyan]Detected {total_changes} change(s):[/bold cyan] "
                  f"[green]{len(added_paths)} added[/green], "
                  f"[yellow]{len(modified_paths)} modified[/yellow], "
                  f"[red]{len(deleted_paths)} deleted[/red]")

    # 3. Process deleted files
    if deleted_paths:
        with db_manager.transaction() as conn:
            for d_path in deleted_paths:
                file_row = conn.execute("SELECT id FROM files WHERE path = ?", (d_path,)).fetchone()
                if file_row:
                    f_id = file_row["id"]
                    conn.execute("DELETE FROM relationships WHERE source_id = ? OR target_id = ?", (f_id, f_id))
                    conn.execute("DELETE FROM files WHERE id = ?", (f_id,))

    # 4. Analyze added and modified files
    paths_to_analyze = added_paths | modified_paths
    analyzed_results = []
    for path in sorted(scanned.keys()):
        sf = scanned[path]
        if path in paths_to_analyze:
            analyzed_results.append(analyze_file(sf, project_root))
        else:
            analyzed_results.append(analyze_file(sf, project_root))

    # 5. Rebuild and refresh knowledge model
    repo.clear_project_data(config.id)
    builder = KnowledgeBuilder(config.id, project_root, repo)
    builder.build_and_save(analyzed_results, export_exports=True)

    # 6. Update timestamp
    now_iso = datetime.now(UTC).isoformat()
    config.updated_at = now_iso
    save_project_config(project_root, config)

    proj_entity = repo.get_project(config.id)
    if proj_entity:
        proj_entity.updated_at = now_iso
        repo.save_project(proj_entity)

    console.print("[bold green][OK] Knowledge model successfully updated.[/bold green]")
