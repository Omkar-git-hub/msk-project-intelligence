# MSK Development Guide

## Environment Setup

### 1. Requirements
- Python 3.11+ (Python 3.14 supported)
- `uv` or `pip`
- Git

### 2. Setting Up Virtual Environment

```bash
uv venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# Install editable with dev dependencies
uv pip install -e ".[dev]"
```

### 3. Running Tests

```bash
pytest tests/
```

### 4. Running the CLI Locally

```bash
python -m msk.cli.main --help
# Or directly if installed:
msk --help
```
