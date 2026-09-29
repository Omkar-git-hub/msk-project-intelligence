"""Project root and ecosystem detection."""

from pathlib import Path

# Well-known root markers ordered by specificity
_ROOT_MARKERS = [
    ".msk",
    ".git",
    "pyproject.toml",
    "setup.py",
    "pom.xml",
    "build.gradle",
    "package.json",
    "Cargo.toml",
    "go.mod",
]

_ECOSYSTEM_MARKERS: dict[str, list[str]] = {
    "python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile", "poetry.lock"],
    "java_maven": ["pom.xml"],
    "java_gradle": ["build.gradle", "build.gradle.kts", "settings.gradle"],
    "node": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
    "docker": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml"],
    "kubernetes": ["k8s", "kubernetes", "helm", "Chart.yaml"],
    "terraform": ["main.tf", "terraform.tf"],
}


def find_project_root(start_dir: str | Path | None = None) -> Path:
    """Traverse upwards to find the project root directory.

    If no marker is found, returns start_dir (or current working directory).
    """
    current = Path(start_dir or Path.cwd()).resolve()

    # First check if current or any parent has an MSK anchor
    temp = current
    while True:
        if (temp / ".msk").is_dir():
            return temp
        if (temp / ".git").exists():
            return temp
        if temp.parent == temp:
            break
        temp = temp.parent

    # Next check for project manifest files
    temp = current
    while True:
        for marker in _ROOT_MARKERS:
            if (temp / marker).exists():
                return temp
        if temp.parent == temp:
            break
        temp = temp.parent

    return current


def detect_ecosystems(root: str | Path) -> list[str]:
    """Detect ecosystems and frameworks present in the project."""
    root_path = Path(root).resolve()
    ecosystems: list[str] = []

    for eco, markers in _ECOSYSTEM_MARKERS.items():
        for marker in markers:
            target = root_path / marker
            if target.exists():
                ecosystems.append(eco)
                break

    return ecosystems
