# Evidence studio

The Unraid worker serves its local web dashboard at **/ui** on the existing API
port. The Unraid template WebUI link opens it; existing containers can open the
same worker URL with /ui appended. No additional port, Docker socket, GPU,
cloud telemetry, JavaScript CDN or HA token is required.

Enter the worker pairing key privately. **Remember access** stores a separately
signed workspace credential for 30 days. It reads context and manages only
same-origin shadow jobs, not entity configuration or device control. The master
key is never stored. Older read-only credentials retain read-only permissions;
pair once to enable new campaign controls. **Forget access** clears local access;
copied credentials expire or become invalid after master-key rotation.

See [the shadow campaign guide](SHADOW-CAMPAIGN.md) for multi-target prediction,
historical bootstrap, outcome testing, policy limits and current limitations.

The forecast panel displays a real prediction from the existing fitted model.
The companion reads at most 27 completed Recorder hourly means for its already
reviewed source once per hour, retrying delayed statistics every five minutes.
Missing hours, wrong units, old-home windows, unavailable sources, or excluded
entities prevent a fresh forecast. The plot distinguishes observed hourly means
from the next hourly mean forecast. Feature contributions are additive model
terms, not a decision tree, causal evidence, or calibrated confidence intervals.
Expired forecasts are visibly stale. Forecasts stay in memory, are recomputed
after restart, and neither retrain the model nor control devices.

The evidence map displays the current reviewed scope, latest projected state,
availability, actor category, live capture counts and actual temperature-model
evaluation results. Pulses indicate updated observations, not an invented stream
of thoughts. Stale connection status is visible. Missing data is never shown as
zero activity. A historical model outside the current allowed scope is marked
inactive, not presented as a model ready to control devices.

Create an idea with its first supporting or competing signal in one form.
Select a node to inspect, rename or delete ideas and remove their connections.
**Connect nodes** starts manual source/target linking; click it again or press
Escape to cancel. Undo/Redo (Ctrl/Cmd+Z and Shift+Z outside text fields) restores
up to 50 workspace edits, including deleted ideas/connections and node moves.
The history is for this tab; reload starts a fresh undo history.

**Save this context workspace** optionally stores drafts locally on this browser.
They can include private household entity names. Unchecking removes the stored
copy, keeping the current draft. Export retains a portable JSON copy. Hints do
not alter worker scope, train models, create automation rules or control devices.
The engine does not consume these hints yet.

## Context learning to build next

The human graph should supply role/relationship priors and reviewable labels,
not replace learning with hardcoded wiring. A contextual learner needs aligned
time windows, units, baselines, device response delays, causal event metadata and
multi-label episodes. Concurrent activities must remain possible:

- A humidity rise, sustained flow and bathroom evidence may support a shower.
- Irrigation is a competing explanation for main-meter flow, not proof that no
  shower can be happening simultaneously. Learn expected irrigation flow before
  attempting any residual-flow attribution; rate alone is ambiguous.
- Bidet power may represent a heater, dryer, cleaning cycle or use. A manually
  requested fan may show ventilation intent; neither proves a specific bodily
  event or occupant identity.

Attribution is partly available now: matching HA automation/script context
chains are marked as such, user-associated context is distinct from unknown,
and snapshots/gaps never become preference labels. A physical remote or external
controller can remain unattributed. Absence of automation attribution never
proves a human action. Engine provenance must be configured before actuation.

The current 11-entity pilot is not silently expanded by this dashboard. Power,
flow, fan, valve and climate context need reviewed observation support and explicit
input/target roles in a subsequent release. Off-limits rooms/entities and model
input lineage must be enforced before collecting, training or acting. Activity
classifiers require held-out evaluation and calibrated uncertainty before the UI
can show learned activity confidence. No shower/bidet/irrigation classifier exists
in this release. The only evaluated model so far is hourly temperature forecasting.
