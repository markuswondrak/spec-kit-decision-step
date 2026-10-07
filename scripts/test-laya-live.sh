#!/usr/bin/env bash
# Install Laya locally for this run, then exercise its in-process adapter.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "$root/scripts/run-live-backend.sh" laya --with 'laya[onnx]'
