#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "[build] Building SAHIIX AGI Docker image..."
docker build -t sahiix-agi:latest .
echo "[build] Tagged image: sahiix-agi:latest"
