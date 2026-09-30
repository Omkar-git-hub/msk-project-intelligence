$ErrorActionPreference = "Stop"

python -m nuitka `
    --standalone `
    --onefile `
    --output-filename=msk.exe `
    --include-package=msk `
    --assume-yes-for-downloads `
    src\msk\cli\main.py