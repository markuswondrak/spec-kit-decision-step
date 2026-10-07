# Custom Backends

The built-in [Jev and Laya backends](../README.md#backends) cover the default
cases. For anything else, plug in your own backend class.

Use any Python class with `capabilities()` and `system_one(state, questions)`
methods:

```yaml
backend:
  adapter: mypackage.models:DecisionBackend
  endpoint: http://decision.internal
  model: company-triage-v2
```

The complete backend mapping is passed to the class constructor. A minimal
implementation looks like this:

```python
class DecisionBackend:
    def __init__(self, config):
        self.config = config

    def capabilities(self):
        return {
            "max_state_bytes": None,
            "max_state_tokens": 4096,
            "max_questions": 32,
            "max_choice_options": 50,
            "max_score_levels": 10,
            "supports_probabilities": True,
            "supports_confidence": True,
        }

    def system_one(self, state, questions):
        return {
            "model": "company-triage-v2",
            "answers": {
                # one protocol-shaped answer for every question id
            },
            "usage": {},
        }
```

Custom backend capabilities are checked at execution time so validation does
not trigger plugin I/O.
