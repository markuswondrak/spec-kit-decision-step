from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from fake_backend import FakeBackend
from specify_cli.workflows.base import StepContext, StepStatus

pytestmark = pytest.mark.integration


def _questions() -> dict[str, Any]:
    return {
        "category": {
            "type": "choice",
            "instructions": "Classify it.",
            "criteria": {"bug": "Broken", "feature": "New"},
        },
        "severity": {
            "type": "score",
            "instructions": "Score impact.",
            "criteria": ["minor", "major", "critical"],
        },
        "security": {
            "type": "noul",
            "instructions": "Security issue?",
        },
    }


def _answers() -> dict[str, Any]:
    return {
        "category": {
            "type": "choice",
            "choice": "bug",
            "probabilities": {"bug": 0.8, "feature": 0.2},
            "confidence": 0.8,
        },
        "severity": {
            "type": "score",
            "score": 1.5,
            "legend": {"0": "minor", "1": "major", "2": "critical"},
            "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
            "confidence": 0.6,
        },
        "security": {"type": "noul", "noul": 0.1},
    }


def _config(**updates: Any) -> dict[str, Any]:
    value = {
        "id": "triage",
        "type": "decision",
        "backend": "fake_backend:FakeBackend",
        "state": {"title": "broken"},
        "questions": _questions(),
    }
    value.update(updates)
    return value


@pytest.fixture(autouse=True)
def reset_backend() -> None:
    FakeBackend.reset()
    FakeBackend.response = {"model": "fake", "answers": _answers(), "usage": {}}


def _execute(decision_module, config: dict[str, Any], root: Path, **context: Any):
    return decision_module.DecisionStep().execute(
        config, StepContext(project_root=str(root), **context)
    )


def test_executes_all_question_types_and_drops_metadata(decision_module, tmp_path):
    result = _execute(decision_module, _config(), tmp_path)

    assert result.status is StepStatus.COMPLETED
    assert result.output == _answers()
    assert "model" not in result.output
    assert "usage" not in result.output


def test_validate_reports_all_static_errors(decision_module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = {
        "id": "bad",
        "backend": "unknown",
        "files": "not-a-list",
        "questions": {
            "bad-id": {"type": "wrong"},
            "score": {"type": "score", "instructions": "x", "criteria": ["one"]},
        },
    }

    errors = decision_module.DecisionStep().validate(config)

    assert len(errors) >= 5
    combined = "\n".join(errors)
    assert "files" in combined
    assert "Unknown decision backend" in combined
    assert "id must match" in combined
    assert "instructions" in combined
    assert "at least 2 levels" in combined


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda answers: answers.pop("security"), "'security': missing object answer"),
        (lambda answers: answers["category"].update(choice="Bug"), "not in configured criteria"),
        (lambda answers: answers["severity"].pop("legend"), "requires a non-empty 'legend'"),
        (lambda answers: answers["severity"].update(score=4), "outside legend range"),
        (lambda answers: answers["security"].pop("noul"), "missing 'noul'"),
    ],
)
def test_malformed_response_fails(decision_module, tmp_path, mutate, message):
    answers = _answers()
    mutate(answers)
    FakeBackend.response = {"answers": answers}

    result = _execute(decision_module, _config(), tmp_path)

    assert result.status is StepStatus.FAILED
    assert "Malformed decision backend response" in result.error
    assert message in result.error


def test_response_reports_all_errors(decision_module, tmp_path):
    FakeBackend.response = {
        "answers": {
            "category": {"choice": "other"},
            "severity": {"score": 99},
            "security": {},
        }
    }

    result = _execute(decision_module, _config(), tmp_path)

    assert result.status is StepStatus.FAILED
    assert result.error.count("question '") >= 3


def test_response_rejects_wrong_type_invalid_probability_and_nonfinite_legend(
    decision_module, tmp_path
):
    answers = _answers()
    answers["category"]["type"] = "score"
    answers["severity"]["legend"] = {"NaN": "bad"}
    answers["security"]["noul"] = 1.5
    FakeBackend.response = {"answers": answers}

    result = _execute(decision_module, _config(), tmp_path)

    assert result.status is StepStatus.FAILED
    assert "answer type" in result.error
    assert "legend keys must be finite" in result.error
    assert "between 0 and 1" in result.error


def test_nested_state_preserves_typed_expression_values(decision_module, tmp_path):
    config = _config(
        state={
            "issue": {
                "labels": "{{ inputs.labels }}",
                "count": "{{ inputs.count }}",
                "summary": "Issue {{ inputs.count }}",
            }
        }
    )

    result = _execute(
        decision_module,
        config,
        tmp_path,
        inputs={"labels": ["bug", "urgent"], "count": 7},
    )

    assert result.status is StepStatus.COMPLETED
    state = FakeBackend.calls[0][0]
    assert state["issue"]["labels"] == ["bug", "urgent"]
    assert state["issue"]["count"] == 7
    assert state["issue"]["summary"] == "Issue 7"


def test_files_are_embedded_after_expression_resolution(decision_module, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "issue.txt"
    source.write_text("line one\nline two\x1b[31m", encoding="utf-8")
    config = _config(
        state={"repo": "demo"},
        files=["{{ inputs.path }}"],
    )

    result = _execute(
        decision_module, config, tmp_path, inputs={"path": "docs/issue.txt"}
    )

    assert result.status is StepStatus.COMPLETED
    embedded = FakeBackend.calls[0][0]["files"][0]
    assert embedded["path"] == "docs/issue.txt"
    assert "\x1b" not in embedded["content"]


@pytest.mark.parametrize("path", ["missing.txt", "../outside.txt"])
def test_missing_or_traversing_file_fails(decision_module, tmp_path, path):
    result = _execute(decision_module, _config(files=[path]), tmp_path)

    assert result.status is StepStatus.FAILED
    assert path in result.error


def test_symlinked_file_fails(decision_module, tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    result = _execute(decision_module, _config(files=["link.txt"]), tmp_path)

    assert result.status is StepStatus.FAILED
    assert "symlink" in result.error


def test_binary_file_is_skipped_with_notice(decision_module, tmp_path, capsys):
    (tmp_path / "binary.dat").write_bytes(b"hello\x00world")

    result = _execute(decision_module, _config(files=["binary.dat"]), tmp_path)

    assert result.status is StepStatus.COMPLETED
    assert FakeBackend.calls[0][0]["files"] == []
    assert "skipped binary decision file" in capsys.readouterr().out


def test_reserved_files_state_key_fails_validation(decision_module, tmp_path):
    result = _execute(
        decision_module, _config(state={"files": "user value"}, files=[]), tmp_path
    )

    assert result.status is StepStatus.FAILED
    assert "reserved" in result.error


def test_byte_truncation_degrades_state_to_text(decision_module, tmp_path, capsys):
    FakeBackend.capability_values["max_state_bytes"] = 20
    config = _config(state={"body": "x" * 100})

    result = _execute(decision_module, config, tmp_path)

    assert result.status is StepStatus.COMPLETED
    state = FakeBackend.calls[0][0]
    assert isinstance(state, str)
    assert len(state.encode("utf-8")) <= 20
    assert "exceeded 20 bytes" in capsys.readouterr().out


def test_token_truncation_uses_four_character_estimate(decision_module, tmp_path, capsys):
    FakeBackend.capability_values["max_state_tokens"] = 5

    result = _execute(decision_module, _config(state="x" * 100), tmp_path)

    assert result.status is StepStatus.COMPLETED
    assert len(FakeBackend.calls[0][0]) == 20
    assert "4 chars/token" in capsys.readouterr().out


def test_state_that_fits_remains_structured(decision_module, tmp_path):
    FakeBackend.capability_values["max_state_bytes"] = 1000

    result = _execute(decision_module, _config(state={"typed": True}), tmp_path)

    assert result.status is StepStatus.COMPLETED
    assert FakeBackend.calls[0][0] == {"typed": True}


def test_layered_config_precedence(decision_module, tmp_path):
    config_dir = tmp_path / ".specify" / "decision"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yml").write_text(
        "backend:\n  adapter: fake_backend:FakeBackend\n  shared: project\n  project: yes\n",
        encoding="utf-8",
    )
    (config_dir / "local.yml").write_text(
        "backend:\n  shared: local\n  local: yes\n", encoding="utf-8"
    )
    config = _config()
    config.pop("backend")
    config["config"] = {"backend": {"shared": "instance", "instance": True}}

    result = _execute(decision_module, config, tmp_path)

    assert result.status is StepStatus.COMPLETED
    backend_config = FakeBackend.calls[0][2]
    assert backend_config["adapter"] == "fake_backend:FakeBackend"
    assert backend_config["project"] is True
    assert backend_config["local"] is True
    assert backend_config["instance"] is True
    assert backend_config["shared"] == "instance"


def test_string_backend_replaces_inherited_mapping(decision_module, tmp_path):
    config_dir = tmp_path / ".specify" / "decision"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yml").write_text(
        "backend:\n  adapter: jev\n  api_key_env: PRIVATE_KEY\n", encoding="utf-8"
    )

    result = _execute(
        decision_module,
        _config(backend="fake_backend:FakeBackend"),
        tmp_path,
    )

    assert result.status is StepStatus.COMPLETED
    assert FakeBackend.calls[0][2] == {}


def test_backend_mapping_replaces_options_when_switching_adapters(decision_module, tmp_path):
    step = decision_module.DecisionStep()
    config = _config(backend={"adapter": "laya", "mode": "inprocess"})

    errors = step._validate(config, tmp_path)

    assert not any("unsupported key" in error for error in errors)


def test_project_config_rejects_per_instance_fields(decision_module, tmp_path):
    config_dir = tmp_path / ".specify" / "decision"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yml").write_text("state: forbidden\n", encoding="utf-8")

    result = _execute(decision_module, _config(), tmp_path)

    assert result.status is StepStatus.FAILED
    assert "only 'backend' is configurable" in result.error


def test_shipped_backend_rejects_unknown_settings(decision_module, tmp_path):
    config = _config(backend={"adapter": "jev", "base_ur1": "https://wrong.test"})

    result = _execute(decision_module, config, tmp_path)

    assert result.status is StepStatus.FAILED
    assert "unsupported key(s): base_ur1" in result.error


def test_noul_criteria_accepts_yaml_boolean_keys(decision_module, tmp_path):
    questions = _questions()
    questions["security"]["criteria"] = yaml.safe_load(
        "true: risky\nfalse: safe\n"
    )

    result = _execute(decision_module, _config(questions=questions), tmp_path)

    assert result.status is StepStatus.COMPLETED


@pytest.mark.parametrize("instructions", [1, True])
def test_instructions_reject_scalar_types(
    decision_module, tmp_path, instructions
):
    questions = _questions()
    questions["security"]["instructions"] = instructions

    result = _execute(decision_module, _config(questions=questions), tmp_path)

    assert result.status is StepStatus.FAILED
    assert "string, mapping, or list" in result.error


def test_custom_capability_limits_are_checked_at_execute(decision_module, tmp_path):
    FakeBackend.capability_values["max_questions"] = 2
    step = decision_module.DecisionStep()
    config = _config()

    assert not any("backend limit" in error for error in step.validate(config))
    result = step.execute(config, StepContext(project_root=str(tmp_path)))

    assert result.status is StepStatus.FAILED
    assert "backend limit is 2" in result.error
    assert FakeBackend.calls == []


def test_plugin_missing_methods_fails_clearly(decision_module, tmp_path):
    config = _config(backend="fake_backend:MissingMethods")

    result = _execute(decision_module, config, tmp_path)

    assert result.status is StepStatus.FAILED
    assert "missing required method(s)" in result.error


def test_backend_exception_becomes_failed_result(decision_module, tmp_path):
    config = _config(backend="fake_backend:ExplodingBackend")

    result = _execute(decision_module, config, tmp_path)

    assert result.status is StepStatus.FAILED
    assert "backend offline" in result.error


def test_shared_step_instance_is_stateless_under_concurrency(decision_module, tmp_path):
    step = decision_module.DecisionStep()

    def run(index: int):
        config = _config(state={"index": "{{ item }}"})
        context = StepContext(project_root=str(tmp_path), item=index, inside_fan_out=True)
        return step.execute(config, context)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(run, range(20)))

    assert all(result.status is StepStatus.COMPLETED for result in results)
    assert sorted(call[0]["index"] for call in FakeBackend.calls) == list(range(20))


def test_resume_reexecutes_without_retained_state(decision_module, tmp_path):
    step = decision_module.DecisionStep()
    first = step.execute(
        _config(state="first"), StepContext(project_root=str(tmp_path))
    )
    second = step.execute(
        _config(state="second"),
        StepContext(project_root=str(tmp_path), is_resume=True),
    )

    assert first.status is StepStatus.COMPLETED
    assert second.status is StepStatus.COMPLETED
    assert [call[0] for call in FakeBackend.calls] == ["first", "second"]


def test_package_metadata_matches_step_type(decision_module):
    root = Path(decision_module.__file__).parent
    metadata = yaml.safe_load((root / "step.yml").read_text(encoding="utf-8"))

    assert metadata["step"]["type_key"] == decision_module.DecisionStep.type_key
    assert metadata["step"]["version"] == "0.9.0"
