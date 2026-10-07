"""Configuration loading for the decision step."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """An actionable decision-step configuration error."""


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge mappings recursively; every non-mapping override replaces its leaf."""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _read_mapping(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ConfigError(f"Decision configuration file not found: {path}")
        return {}
    if path.is_symlink():
        raise ConfigError(f"Refusing symlinked decision configuration file: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read decision configuration {path}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Decision configuration {path} must be a YAML mapping.")
    unknown = sorted(str(key) for key in value if key != "backend")
    if unknown:
        raise ConfigError(
            f"Decision configuration {path} has unsupported key(s): "
            f"{', '.join(unknown)}; only 'backend' is configurable."
        )
    return value


def _instance_settings(config: dict[str, Any]) -> dict[str, Any]:
    nested = config.get("config", {})
    if nested is None:
        nested = {}
    if not isinstance(nested, dict):
        raise ConfigError("Decision step 'config' must be a mapping.")
    unknown = sorted(str(key) for key in nested if key != "backend")
    if unknown:
        raise ConfigError(
            "Decision step 'config' has unsupported key(s): "
            f"{', '.join(unknown)}; only 'backend' is configurable."
        )
    settings = deepcopy(nested)
    if "backend" in config:
        settings["backend"] = deepcopy(config["backend"])
    return settings


def _merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge a settings layer, resetting backend options when its adapter changes."""
    backend = override.get("backend")
    inherited_backend = base.get("backend")
    if (
        isinstance(backend, dict)
        and isinstance(backend.get("adapter"), str)
        and isinstance(inherited_backend, dict)
        and backend["adapter"] != inherited_backend.get("adapter")
    ):
        base = deepcopy(base)
        base["backend"] = {}
    return deep_merge(base, override)


def load_settings(config: dict[str, Any], project_root: Path | None) -> dict[str, Any]:
    """Load defaults, project, local, and instance settings in precedence order."""
    result = _read_mapping(Path(__file__).with_name("defaults.yml"), required=True)
    if project_root is not None:
        directory = project_root / ".specify" / "decision"
        result = _merge_settings(result, _read_mapping(directory / "config.yml"))
        result = _merge_settings(result, _read_mapping(directory / "local.yml"))
    return _merge_settings(result, _instance_settings(config))
