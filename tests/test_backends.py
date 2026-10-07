from __future__ import annotations

import json
import urllib.error

import pytest

from backends import BackendError, JevBackend, LayaHttpBackend, _NoRedirect


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_jev_http_payload_and_authorization(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response({"answers": {}})

    monkeypatch.setenv("MY_JEV_KEY", "top-secret")
    backend = JevBackend(
        {
            "base_url": "https://example.test",
            "model_id": "jev-pinned",
            "api_key_env": "MY_JEV_KEY",
            "timeout": 7,
        }
    )
    monkeypatch.setattr(backend, "_open", lambda request: urlopen(request, 7))

    backend.system_one(
        {"state": True},
        {
            "category": {
                "type": "choice",
                "instructions": "Pick",
                "criteria": ["a", "b"],
            }
        },
    )

    request = captured["request"]
    payload = json.loads(request.data)
    assert request.full_url == "https://example.test/v1/systemone"
    assert request.headers["Authorization"] == "Bearer top-secret"
    assert captured["timeout"] == 7
    assert payload["model"] == "jev-pinned"
    assert payload["questions"]["category"]["criteria"] == {"a": None, "b": None}


def test_jev_missing_key_names_env_without_leaking_secret(monkeypatch):
    monkeypatch.delenv("ABSENT_KEY", raising=False)
    backend = JevBackend({"api_key_env": "ABSENT_KEY"})

    with pytest.raises(BackendError) as error:
        backend.system_one({}, {})

    assert "ABSENT_KEY" in str(error.value)


def test_http_error_is_actionable_and_does_not_leak_key(monkeypatch):
    monkeypatch.setenv("JEV_TEST_KEY", "never-print-this")

    def urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "unavailable",
            {},
            _ResponseBody(b"maintenance never-print-this"),
        )

    backend = JevBackend(
        {"base_url": "https://api.example.test", "api_key_env": "JEV_TEST_KEY"}
    )
    monkeypatch.setattr(backend, "_open", lambda request: urlopen(request, 30))

    with pytest.raises(BackendError) as error:
        backend.system_one({}, {})

    message = str(error.value)
    assert "jev" in message
    assert "api.example.test" in message
    assert "HTTP 503" in message
    assert "maintenance" in message
    assert "never-print-this" not in message


def test_endpoint_userinfo_is_not_in_error_message(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    backend = LayaHttpBackend({"endpoint": "http://user:hidden@localhost:8123"})
    monkeypatch.setattr(backend, "_open", lambda request: urlopen(request, 30))

    with pytest.raises(BackendError) as error:
        backend.system_one({}, {})

    message = str(error.value)
    assert "localhost:8123" in message
    assert "hidden" not in message


class _ResponseBody:
    def __init__(self, value: bytes):
        self.value = value

    def read(self, amount: int = -1) -> bytes:
        return self.value[:amount]

    def close(self) -> None:
        pass


def test_laya_http_uses_no_auth_by_default(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured["request"] = request
        return _Response({"answers": {}})

    backend = LayaHttpBackend({"endpoint": "http://localhost:8123"})
    monkeypatch.setattr(backend, "_open", lambda request: urlopen(request, 30))
    backend.system_one("state", {})

    assert captured["request"].full_url == "http://localhost:8123/v1/systemone"
    assert "Authorization" not in captured["request"].headers


def test_http_redirects_are_disabled():
    assert (
        _NoRedirect().redirect_request(
            None, None, 302, "Found", {}, "https://attacker.test/capture"
        )
        is None
    )


def test_laya_projects_extended_answers_to_strict_contract(monkeypatch):
    backend = LayaHttpBackend({})
    response = {
        "answers": {
            "risk": {
                "type": "noul",
                "noul": 0.4,
                "confidence": 0.9,
                "answer_confidence": 0.8,
                "action": {"act_probability": 1.0},
            }
        },
        "routing": {"model": "extended"},
    }
    monkeypatch.setattr(backend, "_open", lambda request: _Response(response))

    result = backend.system_one("state", {})

    assert result["answers"]["risk"] == {"type": "noul", "noul": 0.4}
