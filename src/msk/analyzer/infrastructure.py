"""Infrastructure and deployment configuration analyzer."""

from pathlib import Path
import re
from pydantic import BaseModel


class InfrastructureComponent(BaseModel):
    """Represents an identified infrastructure configuration file."""

    kind: str  # docker, docker-compose, github-actions, kubernetes, terraform, makefile
    path: str
    details: str | None = None


INFRA_RULES = [
    ("docker", r"(?:^|/)Dockerfile(?:\..+)?$", "Docker container configuration"),
    ("docker-compose", r"(?:^|/)(?:docker-)?compose\.ya?ml$", "Docker Compose multi-container configuration"),
    ("github-actions", r"^\.github/workflows/.*\.ya?ml$", "GitHub Actions CI/CD workflow"),
    ("kubernetes", r"(?:^|/)(?:k8s|kubernetes)/.*\.ya?ml$|(?:^|/)Chart\.ya?ml$", "Kubernetes / Helm manifest"),
    ("terraform", r".*\.tf(?:vars)?$", "Terraform infrastructure as code"),
    ("makefile", r"(?:^|/)Makefile$|.*\.mk$", "Make automation script"),
    ("procfile", r"(?:^|/)Procfile$", "Procfile process runner configuration"),
]


def detect_infrastructure_file(relative_path: str | Path) -> InfrastructureComponent | None:
    """Check if a file represents an infrastructure or deployment configuration."""
    posix_path = Path(relative_path).as_posix()
    filename = Path(relative_path).name

    for kind, pattern, desc in INFRA_RULES:
        if re.search(pattern, posix_path, re.IGNORECASE) or re.search(pattern, filename, re.IGNORECASE):
            return InfrastructureComponent(
                kind=kind,
                path=posix_path,
                details=desc,
            )

    return None
