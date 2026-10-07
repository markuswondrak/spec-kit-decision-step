#!/usr/bin/env bash
# Run the shared end-to-end workflow against one installed decision backend.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  printf 'Usage: %s BACKEND [uv run options...]\n' "$0" >&2
  exit 64
fi

backend=$1
shift

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
spec_kit_dir=${SPEC_KIT_DIR:-"$root/../spec-kit"}

if [[ ! -f "$spec_kit_dir/pyproject.toml" ]]; then
  printf 'SPEC_KIT_DIR must point to a Spec Kit checkout, got: %s\n' "$spec_kit_dir" >&2
  exit 2
fi

project=$(mktemp -d "${TMPDIR:-/tmp}/spec-kit-decision-${backend}.XXXXXX")
cleanup() {
  if [[ ${KEEP_LIVE_TEST_PROJECT:-0} == 1 ]]; then
    printf 'Live test project retained at %s\n' "$project"
  else
    rm -rf -- "$project"
  fi
}
trap cleanup EXIT

mkdir -p "$project/.specify/decision"
cp "$root/examples/live/decision-workflow.yml" "$project/workflow.yml"
cp "$root/examples/live/issue.md" "$project/issue.md"

case $backend in
  jev)
    cat > "$project/.specify/decision/local.yml" <<'EOF'
backend:
  adapter: jev
  base_url: https://api.typesafe.ai
  api_key_env: TYPESAFE_API_KEY
  model_id: jev-latest
  timeout: 30
EOF
    ;;
  laya)
    cat > "$project/.specify/decision/local.yml" <<'EOF'
backend:
  adapter: laya
  mode: inprocess
EOF
    ;;
  *)
    printf 'Unsupported live backend: %s\n' "$backend" >&2
    exit 64
    ;;
esac

# The positional arguments are uv options, not workflow arguments. Keep the
# commands explicit so options are placed before the `specify` entry point.
uv_options=("$@")
(
  cd "$project"
  uv run --project "$spec_kit_dir" "${uv_options[@]}" specify workflow step add decision --dev "$root" --force
  uv run --project "$spec_kit_dir" "${uv_options[@]}" specify workflow run workflow.yml \
    --input 'issue_title=Session token appears in application logs' --json > run.json
)

PROJECT="$project" python3 - <<'PY'
import json
import os
from pathlib import Path

project = Path(os.environ["PROJECT"])
outcome = json.loads((project / "run.json").read_text(encoding="utf-8"))
if outcome.get("status") != "completed":
    raise SystemExit(f"Workflow did not complete: {outcome}")

state_path = project / ".specify" / "workflows" / "runs" / outcome["run_id"] / "state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
answers = state["step_results"]["assess"]["output"]

if answers["category"]["choice"] not in {"bug", "feature", "security"}:
    raise SystemExit("Category is not one of the configured choices.")
if not 0 <= answers["impact"]["score"] <= 3:
    raise SystemExit("Impact score is outside the configured range.")
if not 0 <= answers["requires_security_review"]["noul"] <= 1:
    raise SystemExit("Security-review probability is outside 0..1.")

print(json.dumps({"run_id": outcome["run_id"], "answers": answers}, indent=2))
PY
