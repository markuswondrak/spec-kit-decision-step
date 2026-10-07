# Contributing

The tests use fake in-process backends and make no live Jev or Laya calls.

## Live Backend Tests

The repository includes disposable end-to-end tests that install this step with
`--dev`, run a workflow with all three question types, verify the persisted
typed output, and exercise downstream `if` expressions. They create a temporary
project and remove it afterwards. Set `KEEP_LIVE_TEST_PROJECT=1` to retain it.

Run Jev with the API key provided through the process environment. The key is
not written to the temporary project or passed on the command line:

```bash
TYPESAFE_API_KEY="your-key" ./scripts/test-jev-live.sh
```

Run Laya locally. This uses the in-process adapter and `uv` installs the
optional `laya[onnx]` package for the command, so no server or API key is
required:

```bash
./scripts/test-laya-live.sh
```

Set `SPEC_KIT_DIR` when the Spec Kit checkout is not the sibling directory
`../spec-kit`:

```bash
SPEC_KIT_DIR=/path/to/spec-kit ./scripts/test-laya-live.sh
```

The default suite is host-free: it runs the backend and configuration unit
tests without any Spec Kit installation.

```bash
uv run --with pytest pytest tests -q
uv run ruff check .
```

Run it as `pytest tests` (not a bare `pytest`): `tests/pytest.ini` anchors
pytest's rootdir, otherwise the repository root's `__init__.py` makes pytest
import the whole repo as a package.

Tests that import `specify_cli` — the `DecisionStep` behavior suite and the
end-to-end `--dev` CLI smoke test — are collected only for a deliberate
integration run. Provide the host (PyPI `specify-cli`, or a local Spec Kit
checkout via `SPEC_KIT_DIR`) and pass `--integration`:

```bash
uv run --with specify-cli --with pytest pytest tests -q --integration
SPEC_KIT_DIR=/path/to/spec-kit uv run --with specify-cli --with pytest pytest tests -q --integration
```

The integration suite covers configuration validation, all question types,
response validation, safe file handling, truncation, configuration layering,
plugin loading, concurrency, resume, and an end-to-end `--dev` installation
smoke test.
