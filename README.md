# Decision Step for Spec Kit

Turn workflow context into typed, probabilistic decisions that later steps can
branch on.

Decision models are having a moment. Instead of asking a general-purpose chat
model to generate prose and hoping a JSON parser can recover the answer, they
solve a narrower problem directly: evaluate bounded questions against a state
and return choices, scores, and probabilities.

This community workflow step brings that pattern to
[Spec Kit](https://github.com/github/spec-kit):

- **Typed outputs** for `choice`, `score`, and probability-of-true (`noul`)
  questions
- **Native probabilities** and optional confidence values from the backend
- **Direct branching** through `{{ steps.<id>.output.<question>.* }}`
- **Structured context** assembled from workflow expressions and project files
- **Pluggable backends**, with Jev and Laya adapters included
- **No additional dependency** for the default HTTP path

It is a good fit for issue triage, risk classification, release routing,
prioritization, policy checks, and other places where a workflow needs a
decision rather than another document.

```yaml
steps:
  - id: triage
    type: decision
    state: "{{ inputs.issue }}"
    questions:
      route:
        type: choice
        instructions: Choose where this issue should go.
        criteria:
          bug: Existing behavior is broken.
          feature: New functionality is requested.

  - id: dispatch
    type: switch
    expression: "{{ steps.triage.output.route.choice }}"
    cases:
      bug:
        - type: shell
          run: ./scripts/bug-intake.sh
      feature:
        - type: shell
          run: ./scripts/feature-intake.sh
```

## Installation

Install the released package archive:

```bash
specify workflow step add decision \
  --from https://github.com/markuswondrak/spec-kit-decision-step/releases/download/v0.9.0/decision-0.9.0.zip
```

Install from a local checkout while developing the step itself:

```bash
specify workflow step add decision --dev /path/to/spec-kit-decision-step
```

Verify the installation:

```bash
specify workflow step list
```

The `--dev`, `--from`, and `--force` install flags require Spec Kit `>=1.1.0`.
On older hosts, install by step type ID from a configured, install-allowed
catalog. The community step catalog is discovery-only: it supports `search` and
`info`, but not installation.

The package version is currently `0.9.0`.

## Quick Start

Jev is the default backend. Set its API key:

```bash
export TYPESAFE_API_KEY="your-key"
```

Then add a decision step to a workflow:

```yaml
schema_version: "1.0"

workflow:
  id: issue-triage
  name: Issue Triage
  version: "1.0.0"

inputs:
  issue_title:
    type: string
  issue_body_path:
    type: string

steps:
  - id: triage
    type: decision
    state:
      issue:
        title: "{{ inputs.issue_title }}"
      repository: "{{ inputs.repository | default('unknown') }}"
    files:
      - "{{ inputs.issue_body_path }}"
    questions:
      category:
        type: choice
        instructions: Classify this issue into exactly one category.
        criteria:
          bug: Reported behavior is broken or incorrect
          feature: Request for new functionality
          enhancement: Improvement to existing behavior
          docs: Documentation-only change
          question: Usage question rather than a change request

      severity:
        type: score
        instructions: How severe is the impact if left unaddressed?
        criteria: [trivial, minor, major, critical]

      has_repro:
        type: noul
        instructions: Does the issue contain clear reproduction steps?

  - id: route
    type: switch
    expression: "{{ steps.triage.output.category.choice }}"
    cases:
      bug:
        - id: bug-intake
          type: shell
          run: ./scripts/bug-intake.sh
      feature:
        - id: feature-intake
          type: shell
          run: ./scripts/feature-intake.sh
      enhancement:
        - id: small-change
          type: shell
          run: ./scripts/small-change.sh

  - id: escalate
    type: if
    condition: "{{ steps.triage.output.severity.score }} >= 2.5"
    then:
      - id: notify-maintainers
        type: shell
        run: ./scripts/notify-maintainers.sh
```

## Question Types

### Choice

Selects exactly one configured option. A mapping is recommended because option
descriptions give the model more useful decision criteria than names alone.

```yaml
priority:
  type: choice
  instructions: Select the appropriate delivery priority.
  criteria:
    now: Blocks production or active customers
    next: Important, but a workaround exists
    later: Valuable without immediate urgency
```

Typical output:

```json
{
  "type": "choice",
  "choice": "next",
  "probabilities": {"now": 0.12, "next": 0.75, "later": 0.13},
  "confidence": 0.75
}
```

### Score

Evaluates an ordered rubric. Branch on the numeric `score`; `legend` explains
the scale returned by the backend.

```yaml
impact:
  type: score
  instructions: Score the expected user impact.
  criteria: [negligible, low, moderate, high, critical]
```

Typical output:

```json
{
  "type": "score",
  "score": 2.7,
  "legend": {
    "0": "negligible",
    "1": "low",
    "2": "moderate",
    "3": "high",
    "4": "critical"
  },
  "probabilities": {"0": 0.02, "1": 0.08, "2": 0.32, "3": 0.48, "4": 0.1},
  "confidence": 0.58
}
```

### Noul

Returns a number from `0` to `1` representing `P(true)`. The name comes from the
System One protocol. It does not include a separate confidence field.

```yaml
security_risk:
  type: noul
  instructions: Could this issue plausibly involve a security or data-loss risk?
```

Typical output:

```json
{"type": "noul", "noul": 0.18}
```

For example:

```yaml
- id: security-review
  type: if
  condition: "{{ steps.triage.output.security_risk.noul }} >= 0.5"
```

## State and Files

`state` can be a mapping, list, string, number, or boolean. Expressions are
resolved recursively at every string leaf. A value containing exactly one
expression keeps its original type:

```yaml
state:
  labels: "{{ steps.intake.output.labels }}" # remains a list
  attempts: "{{ inputs.attempts }}"           # remains a number
  summary: "Attempt {{ inputs.attempts }}"    # interpolated string
```

Use `files` to include text from the project:

```yaml
files:
  - docs/design.md
  - "{{ inputs.report_path }}"
```

Files are embedded as `{path, content}` entries under a reserved `files` key.
Paths must stay inside the project root. Missing files, traversal, and symlinks
fail the step; binary and non-UTF-8 files are skipped with a visible warning.
Control and ANSI characters are stripped.

If state exceeds a backend's declared limit, it is serialized and truncated to
text. The step prints a warning rather than silently dropping context.

## Backends

### Jev

Jev is the default:

```yaml
- id: decide
  type: decision
  backend: jev
  # state and questions...
```

Full configuration:

```yaml
backend:
  adapter: jev
  base_url: https://api.typesafe.ai
  api_key_env: TYPESAFE_API_KEY
  model_id: jev-latest
  timeout: 30
```

`api_key_env` is the name of an environment variable, not the secret itself.
The key is sent as a bearer token and is never included in step output.

### Laya

Run a Jev-compatible Laya server locally, then use the shorthand:

```yaml
backend: laya
```

The default endpoint is `http://localhost:8000`. Full HTTP configuration:

```yaml
backend:
  adapter: laya
  mode: http
  endpoint: http://localhost:8000
  timeout: 30
```

An optional in-process mode is also available when Laya is installed:

```yaml
backend:
  adapter: laya
  mode: inprocess
```

Laya-specific response extensions are projected onto the strict output shape,
so downstream expressions remain portable between the included backends.

### Custom Backend

Any Python class with `capabilities()` and `system_one(state, questions)`
methods can be plugged in. See [docs/backends.md](docs/backends.md) for the
adapter reference and a minimal implementation.

The plugin module must be importable by the Spec Kit process, which does not add
your project root to `sys.path`. Put the module on `PYTHONPATH` (or install it)
before running the workflow:

```bash
PYTHONPATH=/path/to/plugins specify workflow run workflow.yml
```

## Output Contract

The step output is the backend's validated `answers` mapping, keyed by question
id. There is no metadata wrapper:

```text
steps.<step-id>.output.<question-id>.<field>
```

Examples:

```yaml
"{{ steps.triage.output.category.choice }}"
"{{ steps.triage.output.category.confidence }}"
"{{ steps.triage.output.severity.score }}"
"{{ steps.triage.output.security_risk.noul }}"
```

Model identity and usage data are intentionally omitted. Probabilities and
confidence are passed through when supplied by the backend; confidence is not
used automatically. Workflow authors decide what thresholds require a human
gate or a different route.

Malformed responses fail at the decision step with all detected answer errors,
rather than becoming an unrelated expression failure later in the workflow.

## Configuration and Operations

Backend settings are layered from package defaults through project, local, and
step-level configuration. Calls are fail-fast, stateless, and make no automatic
retries. Credentials come from the environment, requests do not follow
redirects, and error text never includes the bearer token. See
[docs/configuration.md](docs/configuration.md) for the precedence rules,
override examples, and full operational behavior.

## Troubleshooting

- **`jev backend requires an API key in environment variable ...`** — the Jev
  backend reads the key named by `api_key_env` from the environment. Export it,
  or point `api_key_env` at a different variable. The key is never read from
  YAML.
- **`unknown option '--from'` / `'--dev'`** — the host is older than Spec Kit
  `1.1.0`. Upgrade Spec Kit, or install by step type ID from an install-allowed
  catalog.
- **`ModuleNotFoundError` for a custom backend** — the plugin module is not
  importable. Add its directory to `PYTHONPATH` (or install it); Spec Kit does
  not add the project root to `sys.path`.
- **A file is skipped or state is truncated** — binary and non-UTF-8 files are
  skipped, and oversized state is truncated to text. Both print a visible
  warning and are expected behavior.
- **A backend error** — messages name the adapter, host, and status with a short
  response excerpt, and never include the bearer credential. Calls are
  fail-fast with no retries; use `specify workflow resume` after a transient
  failure.
- **A malformed response** — the decision step fails with every detected answer
  error instead of letting a later expression fail.

## License

MIT. See [LICENSE](LICENSE).

## Development

Tests use fake in-process backends and make no live Jev or Laya calls. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the default and live test runs, the
`--integration` suite, and `SPEC_KIT_DIR` setup.
