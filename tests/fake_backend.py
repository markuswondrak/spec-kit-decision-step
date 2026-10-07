from __future__ import annotations

from copy import deepcopy
from threading import Lock
from typing import Any


class FakeBackend:
    calls: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    response: dict[str, Any] = {"answers": {}}
    capability_values: dict[str, Any] = {
        "max_state_bytes": None,
        "max_state_tokens": None,
        "max_questions": None,
        "max_choice_options": None,
        "max_score_levels": None,
        "supports_probabilities": True,
        "supports_confidence": True,
    }
    lock = Lock()

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.response = {"answers": {}}
        cls.capability_values = {
            "max_state_bytes": None,
            "max_state_tokens": None,
            "max_questions": None,
            "max_choice_options": None,
            "max_score_levels": None,
            "supports_probabilities": True,
            "supports_confidence": True,
        }

    def capabilities(self) -> dict[str, Any]:
        return deepcopy(self.capability_values)

    def system_one(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.calls.append((deepcopy(state), deepcopy(questions), deepcopy(self.config)))
        return deepcopy(self.response)


class MissingMethods:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


class ExplodingBackend(FakeBackend):
    def system_one(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("backend offline")
