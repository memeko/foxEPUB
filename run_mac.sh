#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt

export FLASK_APP=app.py
export FLASK_ENV=development

open "http://127.0.0.1:5000" || true
python3 app.py
