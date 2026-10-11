# Home learning experience

The primary flow is Home → My rooms → Review changes. Setup and history import
are separate from technical diagnostics and the experimental context lab.

Each room distinguishes selected capabilities, insufficient evidence, experimental
patterns, behavior patterns that passed historical baseline checks, and explicit
preference candidates. Unavailable devices keep their historical data. Device
selection and exclusions remain in the Home Assistant integration options.

The review queue uses the newest forecast for each target and channel. It excludes
unchanged states, sensor forecasts, experimental models, and candidates with
safety or coverage blockers. Preference and execution blockers remain explicit:
reviewing a candidate never performs an action. Recorded signals are evidence
from forecast issuance, not a claim about human activity or intent.

Raw forecasts and agreement counts remain in closed technical details. A high
agreement rate can reflect devices remaining off and is not proof of useful
decisions. Download completion is not equivalent to adequate training coverage.

Current limitations remain visible: device execution is unavailable; context-map
hypotheses do not train models; behavior forecasts do not establish preferences;
activity attribution and causal explanations are not established. A better
interface does not change those engine limitations.
