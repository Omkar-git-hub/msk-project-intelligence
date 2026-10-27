"""Integration tests for MSK Phase 7 Policy Integration across subsystems."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from msk.cli.main import app
from msk.config.models import ExternalLLMPolicy, PolicyAction, PolicyConfig
from msk.context.models import ContextRequest
from msk.context.privacy import PrivacyFilter
from msk.context.retriever import ContextRetriever
from msk.knowledge.repository import KnowledgeRepository
from msk.knowledge.serializer import export_from_repository
from msk.policy.integration import PolicyIntegrationService
from msk.policy.models import ContextCategory
from msk.storage.database import DatabaseManager

runner = CliRunner()


@pytest.fixture
def integrated_test_repo(tmp_path: Path) -> tuple[Path, KnowledgeRepository, str]:
    """Create an initialized test repo with files and knowledge base for testing."""
    repo_root = tmp_path / "integrated_repo"
    repo_root.mkdir()

    # Create dummy source files
    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "service.py").write_text(
        "def process_order(order_id):\n    validate_order(order_id)\n    return True\n\n"
        "def validate_order(order_id):\n    return bool(order_id)\n",
        encoding="utf-8",
    )
    (src_dir / "secrets_config.py").write_text(
        "# Internal secret handling module\nSECRET_KEY_NAME = 'synthetic_key_identifier'\n",
        encoding="utf-8",
    )
    test_dir = repo_root / "tests"
    test_dir.mkdir()
    (test_dir / "test_service.py").write_text(
        "from src.service import process_order\n\ndef test_process_order():\n    assert process_order(1) is True\n",
        encoding="utf-8",
    )

    # Initialize MSK
    from msk.cli.init import run_init
    from msk.config.loader import load_project_config

    run_init(root=repo_root, force=True)

    msk_dir = repo_root / ".msk"
    db_path = msk_dir / "msk.db"
    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)

    config = load_project_config(repo_root)
    assert config is not None

    return (repo_root, repo, config.id)


def test_policy_integration_service_file_blocking():
    """Verify PolicyIntegrationService file pattern checks and allowed overrides."""
    policy = PolicyConfig(
        blocked_files=["src/secrets_.*", "config/.*\\.pem"],
        allowed_files=["src/secrets_public\\.py"],
    )

    assert PolicyIntegrationService.is_file_blocked("src/secrets_config.py", policy) is True
    assert PolicyIntegrationService.is_file_blocked("src/secrets_public.py", policy) is False
    assert PolicyIntegrationService.is_file_blocked("src/service.py", policy) is False


def test_policy_integration_service_category_blocking():
    """Verify PolicyIntegrationService category rule checking."""
    policy = PolicyConfig(
        category_rules={
            ContextCategory.TESTS.value: PolicyAction.BLOCK,
            ContextCategory.ARCHITECTURE.value: PolicyAction.ALLOW,
        }
    )

    assert PolicyIntegrationService.is_category_blocked("tests", policy) is True
    assert PolicyIntegrationService.is_category_blocked("architecture", policy) is False
    assert PolicyIntegrationService.is_category_blocked("source_code", policy) is False


def test_context_retriever_filters_blocked_files_early(integrated_test_repo):
    """Verify ContextRetriever eliminates blocked candidate files before budget selection."""
    repo_root, repo, project_id = integrated_test_repo
    privacy = PrivacyFilter()

    # Policy blocking secrets_config.py
    policy = PolicyConfig(
        blocked_files=["src/secrets_config\\.py"],
    )

    retriever = ContextRetriever(
        repo=repo,
        project_id=project_id,
        project_root=repo_root,
        privacy_filter=privacy,
        policy=policy,
    )

    candidates = retriever.retrieve_candidates(ContextRequest(file="src/secrets_config.py"))
    # The file and its contained symbols should be excluded early
    assert not any(c.file_path == "src/secrets_config.py" for c in candidates)
    assert "src/secrets_config.py" in privacy.excluded_files


def test_context_retriever_filters_blocked_categories_early(integrated_test_repo):
    """Verify ContextRetriever excludes entities matching blocked category rules."""
    repo_root, repo, project_id = integrated_test_repo
    privacy = PrivacyFilter()

    # Policy blocking tests category
    policy = PolicyConfig(
        category_rules={
            ContextCategory.TESTS.value: PolicyAction.BLOCK,
        }
    )

    retriever = ContextRetriever(
        repo=repo,
        project_id=project_id,
        project_root=repo_root,
        privacy_filter=privacy,
        policy=policy,
    )

    candidates = retriever.retrieve_candidates(ContextRequest(file="src/service.py"))
    # Tests targeting service.py should NOT be enriched
    assert not any(c.item_type == "test" for c in candidates)


def test_impact_cli_local_target_default(integrated_test_repo):
    """Verify 'msk impact' default local target operates cleanly."""
    repo_root, _, _ = integrated_test_repo

    result = runner.invoke(app, ["impact", "--file", "src/service.py", "--root", str(repo_root)])
    assert result.exit_code == 0
    assert "MSK Impact Analysis" in result.output
    assert "process_order" in result.output


def test_impact_cli_external_target_blocked_by_default(integrated_test_repo):
    """Verify 'msk impact --target external' is blocked by default privacy policy."""
    repo_root, _, _ = integrated_test_repo

    # Default policy has external_llm.enabled = False
    result = runner.invoke(
        app,
        ["impact", "--file", "src/service.py", "--target", "external", "--root", str(repo_root)],
    )
    assert result.exit_code == 1
    assert "External transmission blocked by policy" in result.output


def test_impact_cli_external_target_allowed_when_policy_permits(integrated_test_repo):
    """Verify 'msk impact --target external' succeeds when enabled by policy."""
    repo_root, _, _ = integrated_test_repo
    msk_dir = repo_root / ".msk"

    # Configure permissive policy allowing external AI
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["openai"]),
    )
    (msk_dir / "policy.json").write_text(json.dumps(policy.model_dump()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "impact",
            "--file",
            "src/service.py",
            "--target",
            "external",
            "--provider",
            "openai",
            "--root",
            str(repo_root),
        ],
    )
    assert result.exit_code == 0
    assert "Policy:" in result.output
    assert "ALLOW" in result.output
    assert "openai" in result.output


def test_doctor_reports_policy_health(integrated_test_repo):
    """Verify 'msk doctor' includes privacy policy diagnostic check."""
    repo_root, _, _ = integrated_test_repo
    msk_dir = repo_root / ".msk"

    # Write a valid policy.json
    policy = PolicyConfig()
    (msk_dir / "policy.json").write_text(json.dumps(policy.model_dump()), encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--root", str(repo_root)])
    assert result.exit_code == 0
    assert "Privacy Policy" in result.output
    assert "PASS" in result.output


def test_status_displays_policy_section(integrated_test_repo):
    """Verify 'msk status' displays active policy configuration card."""
    repo_root, _, _ = integrated_test_repo
    msk_dir = repo_root / ".msk"

    policy = PolicyConfig(project_sensitivity="confidential")
    (msk_dir / "policy.json").write_text(json.dumps(policy.model_dump()), encoding="utf-8")

    result = runner.invoke(app, ["status", "--root", str(repo_root)])
    assert result.exit_code == 0
    assert "Policy:" in result.output
    assert "Confidential" in result.output
    assert "Disabled (Local-only)" in result.output


def test_knowledge_export_includes_policy_json(integrated_test_repo):
    """Verify knowledge export generates .msk/exports/policy.json."""
    repo_root, repo, project_id = integrated_test_repo
    export_dir = repo_root / ".msk" / "exports"

    policy = PolicyConfig(project_sensitivity="internal")
    export_from_repository(repo, project_id, export_dir, policy=policy)

    policy_export = export_dir / "policy.json"
    assert policy_export.is_file()

    exported_data = json.loads(policy_export.read_text(encoding="utf-8"))
    assert exported_data["version"] == "1.0.0"
    assert exported_data["project_sensitivity"] == "internal"
