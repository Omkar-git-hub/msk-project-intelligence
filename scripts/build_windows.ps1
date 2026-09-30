$ErrorActionPreference = "Stop"

python -m nuitka `
    --standalone `
    --onefile `
    --output-filename=msk.exe `
    --include-package=msk `
    src\msk\cli\main.py