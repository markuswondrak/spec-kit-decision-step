"""Spec Kit decision workflow step."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from specify_cli.workflows.base import StepBase, StepContext, StepResult, StepStatus
from specify_cli.workflows.expressions import evaluate_expression

if __package__:
    from .backends import (
        SHIPPED_CAPABILITIES,
        BackendError,
        create_backend,
        normalize_backend_config,
        shipped_config_errors,
        validate_capabilities,
    )
    from .config import ConfigError, load_settings
else:  # Support direct loading by standalone test and authoring tools.
    from backends import (  # type: ignore[no-redef]
        SHIPPED_CAPABILITIES,
        BackendError,
        create_backend,
        normalize_backend_config,
        shipped_config_errors,
        validate_capabilities,
    )
    from config import ConfigError, load_settings  # type: ignore[no-redef]

_QUESTION_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")
_CHARS_PER_TOKEN = 4


class DecisionError(RuntimeError):
    """Execution failure safe to expose as a step error."""


def _render(value: Any, context: StepContext) -> Any:
    if isinstance(value, str):
        return evaluate_expression(value, context) if "{{" in value else value
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    return value


def _safe_file(path_value: Any, project_root: Path) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(path_value, str) or not path_value:
        raise DecisionError(f"Decision file path must resolve to a non-empty string, got {path_value!r}.")
    candidate = Path(path_value)
    if candidate.is_absolute():
        unresolved = candidate
    else:
        unresolved = project_root / candidate
    root = project_root.resolve()
    try:
        relative = unresolved.relative_to(project_root)
    except ValueError:
        try:
            relative = unresolved.resolve(strict=False).relative_to(root)
        except (OSError, ValueError):
            raise DecisionError(f"Decision file escapes the project root: {path_value}") from None
    current = project_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise DecisionError(f"Decision file path contains a symlink: {path_value}")
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError:
        raise DecisionError(f"Decision file not found: {path_value}") from None
    except (OSError, ValueError) as exc:
        raise DecisionError(f"Decision file is outside the project root or unreadable: {path_value}: {exc}") from exc
    if not resolved.is_file():
        raise DecisionError(f"Decision file is not a regular file: {path_value}")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise DecisionError(f"Could not read decision file {path_value}: {exc}") from exc
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, f"Warning: skipped non-UTF-8 decision file {path_value}."
    if "\x00" in content:
        return None, f"Warning: skipped binary decision file {path_value}."
    display_path = relative.as_posix()
    return {"path": _CONTROL_CHARS.sub("", display_path), "content": _CONTROL_CHARS.sub("", content)}, None


def _assemble_state(config: dict[str, Any], context: StepContext) -> Any:
    has_state = "state" in config
    file_values = config.get("files")
    resolved_state = _render(deepcopy(config.get("state")), context) if has_state else None
    if file_values is None:
        return resolved_state
    if not isinstance(file_values, list):
        raise DecisionError("Decision step 'files' must be a list of paths.")
    resolved_paths = [_render(value, context) for value in file_values]
    if context.project_root is None:
        raise DecisionError("Decision step cannot read files without context.project_root.")
    embedded: list[dict[str, str]] = []
    for value in resolved_paths:
        item, notice = _safe_file(value, Path(context.project_root))
        if notice:
            print(notice)
        if item is not None:
            embedded.append(item)
    if isinstance(resolved_state, dict):
        if "files" in resolved_state:
            raise DecisionError("Decision state uses reserved key 'files'.")
        state = resolved_state
        state["files"] = embedded
        return state
    if has_state:
        return {"state": resolved_state, "files": embedded}
    return {"files": embedded}


def _serialized_state(state: Any) -> str:
    try:
        return json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DecisionError(f"Decision state is not JSON-serializable: {exc}") from exc


def _truncate_state(state: Any, capabilities: dict[str, Any]) -> Any:
    text = _serialized_state(state)
    byte_limit = capabilities.get("max_state_bytes")
    if byte_limit is not None:
        encoded = text.encode("utf-8")
        if len(encoded) <= byte_limit:
            return state
        truncated = encoded[:byte_limit].decode("utf-8", errors="ignore")
        print(
            f"Warning: decision state exceeded {byte_limit} bytes and was truncated "
            "to the backend limit."
        )
        return truncated
    token_limit = capabilities.get("max_state_tokens")
    if token_limit is not None:
        char_limit = token_limit * _CHARS_PER_TOKEN
        if len(text) > char_limit:
            print(
                f"Warning: decision state exceeded the estimated {token_limit}-token "
                f"backend limit and was truncated using {_CHARS_PER_TOKEN} chars/token."
            )
            return text[:char_limit]
    return state


def _criteria_values(question: dict[str, Any]) -> list[str]:
    criteria = question.get("criteria")
    return list(criteria) if isinstance(criteria, (dict, list)) else []


def _response_errors(response: Any, questions: dict[str, Any]) -> list[str]:
    if not isinstance(response, dict):
        return ["backend response must be an object"]
    answers = response.get("answers")
    if not isinstance(answers, dict):
        return ["backend response field 'answers' must be an object"]
    errors: list[str] = []
    for question_id, question in questions.items():
        answer = answers.get(question_id)
        if not isinstance(answer, dict):
            errors.append(f"question {question_id!r}: missing object answer")
            continue
        question_type = question["type"]
        if "type" in answer and answer["type"] != question_type:
            errors.append(
                f"question {question_id!r}: answer type must be {question_type!r}, "
                f"got {answer['type']!r}"
            )
        if question_type == "choice":
            if "choice" not in answer:
                errors.append(f"question {question_id!r}: choice answer is missing 'choice'")
            elif answer["choice"] not in _criteria_values(question):
                errors.append(
                    f"question {question_id!r}: choice {answer['choice']!r} is not in configured criteria"
                )
        elif question_type == "score":
            score = answer.get("score")
            legend = answer.get("legend")
            if "score" not in answer:
                errors.append(f"question {question_id!r}: score answer is missing 'score'")
            elif isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                errors.append(f"question {question_id!r}: 'score' must be a finite number")
            if not isinstance(legend, dict) or not legend:
                errors.append(f"question {question_id!r}: score answer requires a non-empty 'legend' object")
            elif isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score):
                try:
                    points = [float(key) for key in legend]
                except (TypeError, ValueError):
                    errors.append(f"question {question_id!r}: legend keys must be numeric")
                else:
                    if not all(math.isfinite(point) for point in points):
                        errors.append(f"question {question_id!r}: legend keys must be finite")
                        continue
                    if score < min(points) or score > max(points):
                        errors.append(
                            f"question {question_id!r}: score {score!r} is outside legend range "
                            f"{min(points):g}..{max(points):g}"
                        )
        else:
            noul = answer.get("noul")
            if "noul" not in answer:
                errors.append(f"question {question_id!r}: noul answer is missing 'noul'")
            elif isinstance(noul, bool) or not isinstance(noul, (int, float)) or not math.isfinite(noul):
                errors.append(f"question {question_id!r}: 'noul' must be a finite number")
            elif not 0 <= noul <= 1:
                errors.append(f"question {question_id!r}: 'noul' must be between 0 and 1")
    return errors


def _question_errors(questions: Any, capabilities: dict[str, Any] | None) -> list[str]:
    if not isinstance(questions, dict) or not questions:
        return ["'questions' must be a non-empty mapping."]
    errors: list[str] = []
    max_questions = capabilities.get("max_questions") if capabilities else None
    if max_questions is not None and len(questions) > max_questions:
        errors.append(f"'questions' has {len(questions)} entries; backend limit is {max_questions}.")
    for question_id, question in questions.items():
        label = f"question {question_id!r}"
        if not isinstance(question_id, str) or not _QUESTION_ID.fullmatch(question_id):
            errors.append(f"{label}: id must match [A-Za-z_][A-Za-z0-9_]*.")
        if not isinstance(question, dict):
            errors.append(f"{label}: definition must be a mapping.")
            continue
        question_type = question.get("type")
        if question_type not in ("choice", "score", "noul"):
            errors.append(f"{label}: 'type' must be 'choice', 'score', or 'noul'.")
        instructions = question.get("instructions")
        if "instructions" not in question or instructions in (None, "", [], {}):
            errors.append(f"{label}: requires non-empty 'instructions'.")
        elif not isinstance(instructions, (str, dict, list)):
            errors.append(f"{label}: 'instructions' must be a string, mapping, or list.")
        criteria = question.get("criteria")
        if question_type == "choice":
            if not isinstance(criteria, (dict, list)) or not criteria:
                errors.append(f"{label}: choice 'criteria' must be a non-empty mapping or list.")
            else:
                options = list(criteria)
                if not all(isinstance(option, str) and option for option in options):
                    errors.append(f"{label}: choice option names must be non-empty strings.")
                if len(set(options)) != len(options):
                    errors.append(f"{label}: choice options must be unique.")
                if isinstance(criteria, dict) and not all(
                    value is None or isinstance(value, str) for value in criteria.values()
                ):
                    errors.append(f"{label}: choice descriptions must be strings or null.")
                limit = capabilities.get("max_choice_options") if capabilities else None
                if limit is not None and len(options) > limit:
                    errors.append(f"{label}: has {len(options)} options; backend limit is {limit}.")
        elif question_type == "score":
            if not isinstance(criteria, list) or len(criteria) < 2:
                errors.append(f"{label}: score 'criteria' must be an ordered list with at least 2 levels.")
            else:
                if not all(isinstance(level, str) and level for level in criteria):
                    errors.append(f"{label}: score levels must be non-empty strings.")
                if len(set(criteria)) != len(criteria):
                    errors.append(f"{label}: score levels must be unique.")
                limit = capabilities.get("max_score_levels") if capabilities else None
                if limit is not None and len(criteria) > limit:
                    errors.append(f"{label}: has {len(criteria)} levels; backend limit is {limit}.")
        elif question_type == "noul" and criteria is not None:
            if not isinstance(criteria, dict):
                errors.append(f"{label}: noul 'criteria' must use only 'true' and 'false' keys.")
            else:
                canonical = [
                    str(key).lower() if isinstance(key, bool) else key for key in criteria
                ]
                if not set(canonical).issubset({"true", "false"}):
                    errors.append(f"{label}: noul 'criteria' must use only 'true' and 'false' keys.")
                if len(set(canonical)) != len(canonical):
                    errors.append(f"{label}: noul 'criteria' repeats a canonical key.")
    return errors


class DecisionStep(StepBase):
    """Turn structured workflow state into typed probabilistic answers."""

    type_key = "decision"

    def _validate(self, config: dict[str, Any], project_root: Path | None) -> list[str]:
        errors = super().validate(config)
        step_id = config.get("id", "?")
        if "state" not in config and "files" not in config:
            errors.append(f"Decision step {step_id!r}: at least one of 'state' or 'files' is required.")
        if "files" in config and not isinstance(config.get("files"), list):
            errors.append(f"Decision step {step_id!r}: 'files' must be a list.")
        if isinstance(config.get("state"), dict) and "files" in config["state"]:
            errors.append(f"Decision step {step_id!r}: state key 'files' is reserved.")
        capabilities = None
        try:
            settings = load_settings(config, project_root)
            adapter, backend_config, custom = normalize_backend_config(settings.get("backend"))
            if not custom:
                capabilities = SHIPPED_CAPABILITIES[adapter]
                for error in shipped_config_errors(adapter, backend_config):
                    errors.append(f"Decision step {step_id!r}: {error}.")
        except (ConfigError, BackendError) as exc:
            errors.append(f"Decision step {step_id!r}: {exc}")
        for error in _question_errors(config.get("questions"), capabilities):
            errors.append(f"Decision step {step_id!r}: {error}")
        return errors

    def validate(self, config: dict[str, Any]) -> list[str]:
        return self._validate(config, Path.cwd())

    def execute(self, config: dict[str, Any], context: StepContext) -> StepResult:
        project_root = Path(context.project_root) if context.project_root else None
        static_errors = self._validate(config, project_root)
        if static_errors:
            return StepResult(status=StepStatus.FAILED, error="\n".join(static_errors))
        try:
            settings = load_settings(config, project_root)
            backend, adapter, custom = create_backend(settings.get("backend"))
            capabilities = validate_capabilities(backend.capabilities(), adapter)
            if custom:
                dynamic_errors = _question_errors(config["questions"], capabilities)
                if dynamic_errors:
                    raise DecisionError("\n".join(dynamic_errors))
            state = _truncate_state(_assemble_state(config, context), capabilities)
            response = backend.system_one(state, deepcopy(config["questions"]))
            errors = _response_errors(response, config["questions"])
            if errors:
                raise DecisionError(
                    "Malformed decision backend response:\n" + "\n".join(f"- {error}" for error in errors)
                )
            return StepResult(status=StepStatus.COMPLETED, output=response["answers"])
        except Exception as exc:
            return StepResult(
                status=StepStatus.FAILED,
                error=f"Decision step {config.get('id', '?')!r} failed: {exc}",
            )
