"""System One backend adapters shipped with the decision step."""

from __future__ import annotations

import importlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any


SHIPPED_CAPABILITIES: dict[str, dict[str, Any]] = {
    "jev": {
        "max_state_bytes": None,
        "max_state_tokens": 32_000,
        "max_questions": None,
        "max_choice_options": 255,
        "max_score_levels": 10,
        "supports_probabilities": True,
        "supports_confidence": True,
    },
    "laya": {
        "max_state_bytes": None,
        "max_state_tokens": 512,
        "max_questions": 64,
        "max_choice_options": 100,
        "max_score_levels": 32,
        "supports_probabilities": True,
        "supports_confidence": True,
    },
}

_CAPABILITY_KEYS = frozenset(next(iter(SHIPPED_CAPABILITIES.values())))
_SHIPPED_CONFIG_KEYS = frozenset(
    {
        "adapter",
        "timeout",
        "base_url",
        "api_key_env",
        "model_id",
        "mode",
        "endpoint",
    }
)


class BackendError(RuntimeError):
    """Backend loading, availability, or protocol failure."""


def normalize_backend_config(value: Any) -> tuple[str, dict[str, Any], bool]:
    """Return adapter/plugin name, its config, and whether it is custom."""
    if isinstance(value, str):
        if ":" in value:
            return value, {}, True
        if value not in SHIPPED_CAPABILITIES:
            valid = ", ".join(sorted(SHIPPED_CAPABILITIES))
            raise BackendError(
                f"Unknown decision backend {value!r}; expected one of {valid}, "
                "or a plugin path like 'package.module:Class'."
            )
        return value, {}, False
    if not isinstance(value, dict):
        raise BackendError("Decision 'backend' must be a string or mapping.")
    adapter = value.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        raise BackendError("Decision backend mapping requires a non-empty 'adapter'.")
    custom = ":" in adapter
    if not custom and adapter not in SHIPPED_CAPABILITIES:
        valid = ", ".join(sorted(SHIPPED_CAPABILITIES))
        raise BackendError(
            f"Unknown decision backend {adapter!r}; expected one of {valid}, "
            "or a plugin path like 'package.module:Class'."
        )
    return adapter, deepcopy(value), custom


def shipped_config_errors(adapter: str, config: dict[str, Any]) -> list[str]:
    """Validate shipped adapter settings without constructing an adapter."""
    errors: list[str] = []
    unknown = sorted(str(key) for key in config if key not in _SHIPPED_CONFIG_KEYS)
    if unknown:
        errors.append(f"backend has unsupported key(s): {', '.join(unknown)}")
    timeout = config.get("timeout", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        errors.append("backend 'timeout' must be a positive integer")
    if adapter == "jev":
        fields = {
            "base_url": "https://api.typesafe.ai",
            "api_key_env": "TYPESAFE_API_KEY",
            "model_id": "jev-latest",
        }
    else:
        fields = {"endpoint": "http://localhost:8000"}
        mode = config.get("mode", "http")
        if mode not in ("http", "inprocess"):
            errors.append("laya backend 'mode' must be 'http' or 'inprocess'")
    for field, default in fields.items():
        if not isinstance(config.get(field, default), str) or not config.get(field, default):
            errors.append(f"{adapter} backend {field!r} must be a non-empty string")
    return errors


def validate_capabilities(value: Any, adapter: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BackendError(f"Decision backend {adapter!r} capabilities() must return a dict.")
    capabilities = {key: value.get(key) for key in _CAPABILITY_KEYS}
    for key in (
        "max_state_bytes",
        "max_state_tokens",
        "max_questions",
        "max_choice_options",
        "max_score_levels",
    ):
        limit = capabilities[key]
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
            raise BackendError(
                f"Decision backend {adapter!r} capability {key!r} must be a "
                "positive integer or null."
            )
    for key in ("supports_probabilities", "supports_confidence"):
        supported = capabilities[key]
        if supported is not None and not isinstance(supported, bool):
            raise BackendError(
                f"Decision backend {adapter!r} capability {key!r} must be boolean or null."
            )
    return capabilities


def _load_plugin(path: str, config: dict[str, Any]) -> Any:
    try:
        module_name, class_name = path.rsplit(":", 1)
        if not module_name or not class_name:
            raise ValueError
    except ValueError:
        raise BackendError(
            f"Invalid decision backend plugin {path!r}; expected 'package.module:Class'."
        ) from None
    try:
        backend_class = getattr(importlib.import_module(module_name), class_name)
        backend = backend_class(config)
    except Exception as exc:
        raise BackendError(f"Could not load decision backend plugin {path!r}: {exc}") from exc
    missing = [name for name in ("capabilities", "system_one") if not callable(getattr(backend, name, None))]
    if missing:
        raise BackendError(
            f"Decision backend plugin {path!r} is missing required method(s): "
            f"{', '.join(missing)}."
        )
    return backend


def create_backend(value: Any) -> tuple[Any, str, bool]:
    adapter, config, custom = normalize_backend_config(value)
    if custom:
        return _load_plugin(adapter, config), adapter, True
    if adapter == "jev":
        return JevBackend(config), adapter, False
    mode = config.get("mode", "http")
    if mode == "http":
        return LayaHttpBackend(config), adapter, False
    if mode == "inprocess":
        return LayaInProcessBackend(config), adapter, False
    raise BackendError("Laya backend 'mode' must be 'http' or 'inprocess'.")


def _wire_questions(questions: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(questions)
    for question in result.values():
        if question.get("type") == "choice" and isinstance(question.get("criteria"), list):
            question["criteria"] = {option: None for option in question["criteria"]}
        elif question.get("type") == "noul" and isinstance(question.get("criteria"), dict):
            question["criteria"] = {
                str(key).lower() if isinstance(key, bool) else key: value
                for key, value in question["criteria"].items()
            }
    return result


def _strict_laya_response(value: dict[str, Any]) -> dict[str, Any]:
    """Project Laya's extended answers onto the strict System One contract."""
    answers = value.get("answers")
    if not isinstance(answers, dict):
        return value
    fields = {
        "choice": ("type", "choice", "probabilities", "confidence"),
        "score": ("type", "score", "legend", "probabilities", "confidence"),
        "noul": ("type", "noul"),
    }
    projected: dict[str, Any] = {}
    for question_id, answer in answers.items():
        if not isinstance(answer, dict) or answer.get("type") not in fields:
            projected[question_id] = answer
            continue
        projected[question_id] = {
            key: answer[key] for key in fields[answer["type"]] if key in answer
        }
    result = dict(value)
    result["answers"] = projected
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """System One POSTs fail rather than forwarding credentials or state."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _HttpBackend:
    adapter = "http"

    def __init__(self, endpoint: str, config: dict[str, Any]) -> None:
        self.endpoint = endpoint.rstrip("/") + "/v1/systemone"
        timeout = config.get("timeout", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise BackendError(f"{self.adapter} backend 'timeout' must be a positive integer.")
        self.timeout = timeout
        self.config = config

    def capabilities(self) -> dict[str, Any]:
        return deepcopy(SHIPPED_CAPABILITIES[self.adapter])

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json"}

    def _payload(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        return {"state": state, "questions": _wire_questions(questions)}

    def _open(self, request: urllib.request.Request):
        return urllib.request.build_opener(_NoRedirect).open(
            request, timeout=self.timeout
        )

    def system_one(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(
            self._payload(state, questions), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=body, headers=self._headers(), method="POST"
        )
        parsed_endpoint = urllib.parse.urlsplit(self.endpoint)
        host = parsed_endpoint.hostname or self.endpoint
        try:
            if parsed_endpoint.port is not None:
                host = f"{host}:{parsed_endpoint.port}"
        except ValueError:
            pass
        try:
            with self._open(request) as response:
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            excerpt = exc.read(500).decode("utf-8", errors="replace").replace("\n", " ")
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                excerpt = excerpt.replace(authorization[7:], "[REDACTED]")
            raise BackendError(
                f"{self.adapter} backend at {host} returned HTTP {exc.code}: {excerpt[:500]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BackendError(f"{self.adapter} backend at {host} is unavailable: {exc}") from exc
        try:
            value = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendError(
                f"{self.adapter} backend at {host} returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise BackendError(f"{self.adapter} backend at {host} returned a non-object response.")
        usage = value.get("usage")
        if isinstance(usage, dict) and usage.get("truncated"):
            print(f"Warning: {self.adapter} backend reported that it truncated decision input.")
        return value


class JevBackend(_HttpBackend):
    adapter = "jev"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config.get("base_url", "https://api.typesafe.ai"), config)

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        env_name = self.config.get("api_key_env", "TYPESAFE_API_KEY")
        if not isinstance(env_name, str) or not env_name:
            raise BackendError("jev backend 'api_key_env' must be a non-empty string.")
        api_key = os.environ.get(env_name)
        if not api_key:
            raise BackendError(
                f"jev backend requires an API key in environment variable {env_name!r}."
            )
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _payload(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        payload = super()._payload(state, questions)
        payload["model"] = self.config.get("model_id", "jev-latest")
        return payload


class LayaHttpBackend(_HttpBackend):
    adapter = "laya"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config.get("endpoint", "http://localhost:8000"), config)

    def system_one(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        return _strict_laya_response(super().system_one(state, questions))


class LayaInProcessBackend:
    adapter = "laya"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        try:
            laya = importlib.import_module("laya")
            self.router = laya.Router()
        except (ImportError, AttributeError) as exc:
            raise BackendError(
                "laya in-process backend is unavailable; install the optional "
                "'laya[onnx]' package."
            ) from exc

    def capabilities(self) -> dict[str, Any]:
        return deepcopy(SHIPPED_CAPABILITIES["laya"])

    def system_one(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        try:
            value = self.router.predict(state, _wire_questions(questions))
        except Exception as exc:
            raise BackendError(f"laya in-process backend failed: {exc}") from exc
        if not isinstance(value, dict):
            raise BackendError("laya in-process backend returned a non-object response.")
        usage = value.get("usage")
        if isinstance(usage, dict) and usage.get("truncated"):
            print("Warning: laya backend reported that it truncated decision input.")
        return _strict_laya_response(value)
