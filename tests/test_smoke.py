from __future__ import annotations

import json
from pathlib import Path

import pytest
from specify_cli import app
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


def test_dev_install_and_run_with_fake_backend(tmp_path, monkeypatch):
    package = Path(__file__).parents[1]
    project = tmp_path / "project"
    (project / ".specify").mkdir(parents=True)
    (project / "smoke_backend.py").write_text(
        "class SmokeBackend:\n"
        "    def __init__(self, config): pass\n"
        "    def capabilities(self):\n"
        "        return {\n"
        "            'max_state_bytes': None, 'max_state_tokens': None,\n"
        "            'max_questions': None, 'max_choice_options': None,\n"
        "            'max_score_levels': None, 'supports_probabilities': True,\n"
        "            'supports_confidence': True,\n"
        "        }\n"
        "    def system_one(self, state, questions):\n"
        "        return {'answers': {'route': {'type': 'choice', 'choice': 'bug'}}}\n",
        encoding="utf-8",
    )
    workflow = project / "decision.yml"
    workflow.write_text(
        'schema_version: "1.0"\n'
        "workflow:\n"
        "  id: decision-smoke\n"
        "  name: Decision Smoke\n"
        '  version: "1.0.0"\n'
        "steps:\n"
        "  - id: triage\n"
        "    type: decision\n"
        "    backend: smoke_backend:SmokeBackend\n"
        "    state: issue body\n"
        "    questions:\n"
        "      route:\n"
        "        type: choice\n"
        "        instructions: Classify it.\n"
        "        criteria: [bug, feature]\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.syspath_prepend(str(project))
    runner = CliRunner()

    installed = runner.invoke(
        app, ["workflow", "step", "add", "decision", "--dev", str(package)]
    )
    assert installed.exit_code == 0, installed.output

    run = runner.invoke(app, ["workflow", "run", str(workflow), "--json"])

    assert run.exit_code == 0, run.output
    payload = json.loads(run.stdout)
    assert payload["status"] == "completed"
    run_state = json.loads(
        (
            project
            / ".specify"
            / "workflows"
            / "runs"
            / payload["run_id"]
            / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        run_state["step_results"]["triage"]["output"]["route"]["choice"]
        == "bug"
    )
