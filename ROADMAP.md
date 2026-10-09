# Roadmap

## Goal and current foundation

The goal is automatic local home control learned from history and ongoing
behavior: lighting, curtains and climate within enforced, user-reviewable bounds.
See [Learning engine direction](docs/LEARNING-ENGINE.md) and its The Silly Home reference.

- Working Unraid worker, native HA room/entity selection and bounded observation.
- Private journals, gaps, attribution and read-only report/result entities.
- Historical hourly temperature training with move-aware validation and held-out
  comparison against persistence and previous-day baselines.
- Published amd64/arm64 images with CI and container smoke tests.
- Explicit room/entity exclusion controls in 0.3.1a1; live installation requires
  updating both worker and companion. No device control exists yet.

## Next: validated behavior learning
- Bounded read-only raw Influx import for approved targets and input lineage.
- Journal-driven chronological shadow evaluation with calibrated uncertainty.
- Explicit correction feedback separating unwanted lighting, brightness and timing.
- Evidence fusion that respects two occupants, pets and environmental context.
- Models compared against simple baselines; no automatic promotion from one week.

## Later: opt-in constrained control
- Manual overrides take immediate precedence.
- Nighttime lighting and equipment operating limits are hard constraints.
- Engine actions excluded from preference training.
- Explainable decisions, bounded exploration and rollback.
- Thermal modeling only after physical zones/actuators and temperature sources
  are verified. No heating control exists in the current alpha.

Temperature forecasting alone is not a climate controller. Device response,
desired actions, uncertainty and hard policies must be evaluated together before
enabling automatic control of each approved target.
