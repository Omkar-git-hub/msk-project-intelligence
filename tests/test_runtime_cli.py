"""Tests for MSK Runtime CLI commands."""

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from msk.cli.main import app
from msk.knowledge.models import FileEntity, ProjectEntity, SymbolEntity
from msk.knowledge.repository import KnowledgeRepository
from msk.storage.database import DatabaseManager

runner = CliRunner()


def _init_cli_project(tmp_path: Path):
    msk_dir = tmp_path / ".msk"
    msk_dir.mkdir(parents=True)
    db_path = msk_dir / "msk.db"
    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)

    now = datetime.now(UTC).isoformat()
    proj_id = "proj_cli_test"
    repo.save_project(
        ProjectEntity(
            id=proj_id,
            name="CliProject",
            root_path=str(tmp_path),
            created_at=now,
            updated_at=now,
        )
    )

    # Save minimal project config
    cfg_file = msk_dir / "project.json"
    cfg_file.write_text(
        json.dumps({
            "id": proj_id,
            "name": "CliProject",
            "root": str(tmp_path),
            "created_at": now,
            "updated_at": now,
        }),
        encoding="utf-8",
    )

    # Add sample source file and symbol
    f = FileEntity(
        id="f_sample",
        project_id=proj_id,
        path="src/service.py",
        language="python",
        size_bytes=400,
        content_hash="h_sample",
        structural_hash="s_sample",
        modified_at=now,
    )
    repo.save_files([f])

    s = SymbolEntity(
        id="s_sample",
        file_id="f_sample",
        name="handle_request",
        type="function",
        line_start=10,
        line_end=30,
    )
    repo.save_symbols([s])

    return tmp_path


def test_runtime_status_empty(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    result = runner.invoke(app, ["runtime", "status", "--root", str(root)])
    assert result.exit_code == 0
    assert "MSK Runtime Intelligence Status" in result.output
    assert "CliProject" in result.output


def test_runtime_status_json(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    result = runner.invoke(app, ["runtime", "status", "--root", str(root), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["project_name"] == "CliProject"
    assert data["total_events"] == 0


def test_runtime_logs_raw(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    raw_log = "2026-11-03 14:00:00 [ERROR] Connection refused to cache\n"
    result = runner.invoke(
        app,
        ["runtime", "logs", "--raw", raw_log, "--root", str(root)],
    )
    assert result.exit_code == 0
    assert "Log Ingestion Summary" in result.output
    assert "Parsed 1 runtime events" in result.output


def test_runtime_logs_file(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    log_file = root / "app.log"
    log_file.write_text("2026-11-03 14:05:00 [WARNING] Request took longer than expected\n")

    result = runner.invoke(
        app,
        ["runtime", "logs", "--file", str(log_file), "--root", str(root), "--json"],
    )
    assert result.exit_code == 0
    events = json.loads(result.output)
    assert len(events) == 1
    assert events[0]["level"] == "WARNING"


def test_runtime_analyze_raw(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    tb = """
Traceback (most recent call last):
  File "src/service.py", line 18, in handle_request
    raise ValueError("Missing parameter")
ValueError: Missing parameter
"""
    result = runner.invoke(
        app,
        ["runtime", "analyze", "--raw", tb, "--root", str(root)],
    )
    assert result.exit_code == 0
    assert "Observed Runtime Facts" in result.output
    assert "ValueError" in result.output
    assert "handle_request" in result.output


def test_runtime_analyze_json(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    tb = """
Traceback (most recent call last):
  File "src/service.py", line 18, in handle_request
    raise ValueError("Missing parameter")
ValueError: Missing parameter
"""
    result = runner.invoke(
        app,
        ["runtime", "analyze", "--raw", tb, "--root", str(root), "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["observed_facts"]["exception_type"] == "ValueError"
    assert data["observed_facts"]["symbol_name"] == "handle_request"


def test_runtime_errors_listing_and_resolve(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    tb = """
Traceback (most recent call last):
  File "src/service.py", line 20, in handle_request
    raise KeyError("user_id")
KeyError: user_id
"""
    runner.invoke(app, ["runtime", "analyze", "--raw", tb, "--root", str(root)])

    # List errors
    res_list = runner.invoke(app, ["runtime", "errors", "--root", str(root), "--json"])
    assert res_list.exit_code == 0
    errs = json.loads(res_list.output)
    assert len(errs) >= 1
    err_id = errs[0]["id"]

    # Resolve error
    res_resolve = runner.invoke(
        app,
        ["runtime", "errors", "--resolve", err_id, "--root", str(root)],
    )
    assert res_resolve.exit_code == 0
    assert "Successfully marked error" in res_resolve.output

    # List unresolved
    res_unres = runner.invoke(
        app,
        ["runtime", "errors", "--unresolved", "--root", str(root), "--json"],
    )
    assert res_unres.exit_code == 0
    unres_errs = json.loads(res_unres.output)
    assert len(unres_errs) == 0


def test_runtime_history(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    log_text = "2026-11-03 12:00:00 [INFO] Task A started\n2026-11-03 12:01:00 [INFO] Task B completed\n"
    runner.invoke(app, ["runtime", "logs", "--raw", log_text, "--root", str(root)])

    result = runner.invoke(app, ["runtime", "history", "--root", str(root), "--json"])
    assert result.exit_code == 0
    events = json.loads(result.output)
    assert len(events) == 2


def test_runtime_correlate_latest(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    tb = """
Traceback (most recent call last):
  File "src/service.py", line 12, in handle_request
    raise RuntimeError("Operation failed")
RuntimeError: Operation failed
"""
    runner.invoke(app, ["runtime", "analyze", "--raw", tb, "--root", str(root)])

    result = runner.invoke(app, ["runtime", "correlate", "--latest", "--root", str(root)])
    assert result.exit_code == 0
    assert "Runtime to Static Code Correlation" in result.output
    assert "handle_request" in result.output


def test_runtime_prune(tmp_path: Path):
    root = _init_cli_project(tmp_path)
    log_text = "2026-11-03 10:00:00 [INFO] Old event\n"
    runner.invoke(app, ["runtime", "logs", "--raw", log_text, "--root", str(root)])

    res_clear = runner.invoke(app, ["runtime", "prune", "--clear", "--root", str(root)])
    assert res_clear.exit_code == 0
    assert "Cleared all runtime events" in res_clear.output


def test_runtime_uninitialized_project(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = runner.invoke(app, ["runtime", "status", "--root", str(empty_dir)])
    assert result.exit_code != 0
    assert "No MSK project initialized" in result.output
