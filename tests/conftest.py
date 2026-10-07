from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_TESTS = ROOT / "tests"

# Integration modules import ``specify_cli`` at module scope and are only
# collected for a deliberate integration run.
_INTEGRATION_MODULES = ("test_decision_step.py", "test_smoke.py")

# Put the package root and tests dir on the path so host-free unit tests can
# import ``backends`` and ``fake_backend`` directly.
for entry in (_TESTS, ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))


def _configured_specify_cli_src() -> Path | None:
    """Return the ``SPEC_KIT_DIR`` checkout's ``src`` dir, when it exists."""
    configured = os.environ.get("SPEC_KIT_DIR")
    if not configured:
        return None
    checkout = Path(configured).expanduser()
    for src in (checkout / "src", checkout):
        if (src / "specify_cli").is_dir():
            return src
    return None


def _sibling_specify_cli_src() -> Path | None:
    """Return the sibling ``../spec-kit`` checkout's ``src`` dir, when present."""
    checkout = ROOT.parent / "spec-kit"
    for src in (checkout / "src", checkout):
        if (src / "specify_cli").is_dir():
            return src
    return None


def _integration_requested() -> bool:
    return os.environ.get("SPEC_KIT_INTEGRATION") == "1" or "--integration" in sys.argv


def _host_available() -> bool:
    try:
        return importlib.util.find_spec("specify_cli") is not None
    except (ImportError, ValueError):
        return False


# Make a configured or checked-out Spec Kit host importable for the opt-in
# integration run. The default suite is host-free and must not need it.
if _integration_requested():
    _host_src = _configured_specify_cli_src() or _sibling_specify_cli_src()
    if _host_src is not None and str(_host_src) not in sys.path:
        sys.path.insert(0, str(_host_src))

# Never import the integration modules unless the run is deliberate and a host
# is actually importable; this keeps the default suite free of ``specify_cli``.
if not (_integration_requested() and _host_available()):
    collect_ignore = list(_INTEGRATION_MODULES)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="run tests that require an installed Spec Kit host (specify_cli)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requires an installed Spec Kit host (specify_cli); "
        "run with --integration",
    )


@pytest.fixture(scope="session")
def decision_module():
    package_name = "_decision_step_under_test"
    spec = importlib.util.spec_from_file_location(
        package_name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module
