#!/usr/bin/env bash
# Run the decision step end-to-end against the hosted Jev model.
set -euo pipefail

: "${TYPESAFE_API_KEY:?Set TYPESAFE_API_KEY before running this script.}"

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "$root/scripts/run-live-backend.sh" jev
