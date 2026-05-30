#!/usr/bin/env bash
# Builds the React frontend into backend/static/ so FastAPI can serve it.
# Intended to be invoked by app.yaml's command pipeline and by local devs.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"

echo "[build_frontend] installing dependencies (npm ci)…"
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi

echo "[build_frontend] type-checking…"
npm run lint

echo "[build_frontend] building production bundle…"
npm run build

OUT="$ROOT/backend/static"
echo "[build_frontend] built to: $OUT"
ls -la "$OUT" | head -20
