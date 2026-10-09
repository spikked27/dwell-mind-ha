# Evidence studio

The Unraid worker serves its local web dashboard at **/ui** on the existing API
port. The Unraid template WebUI link opens it; existing containers can open the
same worker URL with /ui appended. No additional port, Docker socket, GPU,
cloud telemetry, JavaScript CDN or HA token is required.

Enter the existing worker pairing key privately in the dashboard. The key is
kept in that tab's memory only, never the URL, exported maps, cookies or browser
storage. The dashboard makes authenticated GET requests only. The key remains a
worker pairing credential; it is not a separately scoped read-only token.
Browser POST requests to the worker remain refused.

The evidence map displays the current reviewed scope, latest projected state,
availability, actor category, live capture counts and actual temperature-model
evaluation results. Pulses indicate updated observations, not an invented stream
of thoughts. Stale connection status is visible. Missing data is never shown as
zero activity. A historical model outside the current allowed scope is marked
inactive, not presented as a model ready to control devices.

Drag nodes or click **Connect nodes**, then choose a source and target. Add named
human hypotheses and annotate supporting, contradicting, confounding or possible
response relationships. **Export hypothesis map** saves a bounded JSON document.
These workspace hints are in-memory drafts: they do not alter the worker's scope,
train models, create automation rules or control devices. Export before closing
the tab. The engine does not consume these hints yet.

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
