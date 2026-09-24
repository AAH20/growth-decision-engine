# Contributing

Keep the measurement kernel dependency-light, deterministic and offline. A change to a metric definition, input validation rule, plan semantics or report structure must bump the protocol version and include a fixture and independent expected-value test. A change to an adapter must include both a permitted synthetic source export and rejection cases for missing IDs, duplicates, partial pagination and sampling.

Before opening a pull request:

```bash
python3 -m unittest discover -s tests -v
python3 -m growth_decision_engine demo --output outputs/demo-scorecard.json
python3 -m growth_decision_engine verify \
  --assignments growth_decision_engine/fixtures/assignments.synthetic.csv \
  --outcomes growth_decision_engine/fixtures/outcomes.synthetic.csv \
  --costs growth_decision_engine/fixtures/costs.synthetic.csv \
  --plan growth_decision_engine/fixtures/plan.synthetic.json \
  --scorecard outputs/demo-scorecard.json
```

Describe which claim the change supports, its failure modes, runtime and memory impact, and whether it changes a real decision. Never submit credentials, customer records, identifiers, or unredacted platform exports. Clearly mark synthetic examples and unverified proposals.
