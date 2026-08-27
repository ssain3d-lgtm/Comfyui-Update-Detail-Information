#!/usr/bin/env sh
# ComfyUI update report -- Linux / macOS launcher.
# Windows users: run Update-Report.bat instead.
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PY=""
for candidate in     "$DIR/venv/bin/python"     "$DIR/.venv/bin/python"     "$DIR/ComfyUI/venv/bin/python"     "$DIR/ComfyUI/.venv/bin/python"; do
    if [ -x "$candidate" ]; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    PY=$(command -v python3 || command -v python || true)
fi
if [ -z "$PY" ]; then
    echo "[ERROR] No python found. Activate the ComfyUI venv and re-run." >&2
    exit 1
fi

exec "$PY" "$DIR/_update-report/update_report.py" "$@"
