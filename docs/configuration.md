# Configuration and Operations

## Configuration Layers

Backend settings are merged from lowest to highest precedence:

1. Package defaults in `defaults.yml`
2. Project settings in `.specify/decision/config.yml`
3. Machine-local settings in `.specify/decision/local.yml`
4. The workflow step's `config:` or top-level `backend:` value

Example committed project configuration:

```yaml
# .specify/decision/config.yml
backend:
  adapter: jev
  model_id: jev-latest
  timeout: 30
```

Example local override:

```yaml
# .specify/decision/local.yml
backend:
  api_key_env: MY_TYPESAFE_API_KEY
```

`.specify/decision/local.yml` is ignored by this repository's `.gitignore`, but
consumer projects should add the same rule to their own ignore file. Store the
secret value in the environment, not in either YAML file.

Mappings are deep-merged when they retain the same adapter. Changing the
adapter, whether with a string such as `backend: laya` or a mapping with a new
`adapter`, replaces the inherited backend mapping and starts from that adapter's
built-in defaults.

## Operational Behavior

- Calls are fail-fast with a default 30-second timeout.
- There are no automatic retries or silent backend fallbacks.
- Resume is the recovery mechanism for transient failures.
- The step is stateless and safe for concurrent `fan-out` execution.
- HTTP requests do not follow redirects, avoiding accidental credential or
  state forwarding.
- Backend errors include the adapter, host, status, and a short response
  excerpt without exposing configured bearer credentials.

For uncertain or consequential decisions, compose the existing Spec Kit
`gate` step after `decision`. The model supplies a probability; the workflow
still owns the policy.
