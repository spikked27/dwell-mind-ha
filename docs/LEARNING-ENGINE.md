# Learning engine direction

DwellMind's goal is local automatic control of lighting, curtains and climate,
learned from historical data and continuing behavior. Unraid hosts models and
decision making; the HA integration handles selection, constraints, feedback and
verified execution. The first temperature forecaster is one component, not the
complete controller.

## Reference: The Silly Home

[The Silly Home](https://github.com/lcmchris/thesillyhome-container) separates
sensor inputs from predicted actuators, prepares historical context for learning,
uses a decision-tree lighting model with recency weighting and prior-state
features, and evaluates predictions on live sensor changes. This is a useful
architectural reference. DwellMind does not copy its implementation or adopt its
database connection/deployment configuration.

For DwellMind, predicting an observed device state is distinct from learning a
desired action. Automation actions, engine actions, snapshots and unknown periods
must not become preference labels. Corrections and quick reversals need uncertain
attribution, occupant/pet context and explicit feedback. Retained data must have
source, availability, move boundary, room mapping and lineage information.

## Explicit scope

The HA room form provides **Off-limits rooms** and **Off-limits entities**.
Exclusions override reviewed inclusion, persist in supported config-entry options
and never delete HA entities or historical files. They block new collection and
historical training through the integration. Declared helper dependencies inherit
exclusions. Unknown membership is refused when room exclusions are configured.
Dependency traversal is bounded and cycles fail closed.

HA sends only the remaining approved scope to the worker. An empty scope pauses
collection and makes the worker refuse captures and training. Saving scope
changes ends a running capture and preserves its report. Registry changes
invalidate queued observations; membership is reviewed before another batch is
sent. A model excluded by scope is retained historically but not shown as usable.
Removing an exclusion does not automatically enable newly discovered entities.
The existing allowlist remains in effect.

The current engine has no home-device actuation. The future execution layer must
check the latest exclusions, input lineage, manual hold and hard limits again
immediately before every action. A model, scene, group or previously queued action
must not override these checks. Hidden dependencies require explicit lineage;
an undeclared helper relationship cannot be assumed safe.

## Next components

1. Read-only bounded Influx import for the reviewed scope, retaining raw-event
   gaps and historical names without merging the old and current homes blindly.
2. Occupancy evidence and lighting action/brightness models with chronological
   holdouts, calibrated uncertainty and correction feedback. Raw historical light
   states alone are not desired brightness labels.
3. Shadow decisions, explanations, outcomes and model comparison in HA.
4. A constrained HA execution broker, emergency stop, immediate manual priority,
   nighttime brightness and equipment-specific climate/curtain bounds.
5. Climate response learning from temperatures, weather and verified equipment
   activity, plus curtain intent models that preserve assumed-position uncertainty.
6. Scheduled local retraining, drift checks, model versioning and rollback.

Promotion to control is per target and requires evaluated usefulness plus an
approved device scope and policy. Excluded rooms and entities remain off limits
regardless of model accuracy. No GPU or LLM is required for these stages.
