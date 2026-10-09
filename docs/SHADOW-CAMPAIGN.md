# Shadow learning campaign

DwellMind can run a bounded 1–30 day campaign for the rooms and entities reviewed
in Home Assistant. Start/stop it in Evidence Studio or with the native HA actions.
It never executes device commands. The worker has no HA control credentials.

## Capabilities and honest enrollment

The native pickers support lights, occupancy/contact sensors, environmental,
power/energy/water/air-quality sensors, fans, climate entities, covers, switches,
valves and media inputs. Registry area membership and explicit exclusions still
apply. Newly supported entities are not silently added to an existing selection.
Valves/switches/media are context inputs, never water-valve action targets.
Covers need a curtain/shade/blind/shutter class to train curtain targets; garage,
gate, door, window and unclassified covers remain context inputs. Verify the
physical identity of selected covers/climates before any future control policy.

Each selected supported target learns a five-minute reported-state forecast:
occupancy/contact evidence; lighting on/off, brightness and Kelvin;
climate modes/setpoints; curtain states/positions; and fan states. The model uses
room-local states, environmental values and five-minute changes, cross-room
occupancy/power/flow and valve confounders, time and availability categories.
There are at most 24 targets and 24 features per target. No GPU or LLM is needed.

An observed occupancy state is not proof of a human, room entry, identity or the
absence of cats. A forecast of a fan or light action is not proof of desired
ventilation or a preferred light setting. Showers, bodily activities and learned
preferences are not claimed without appropriate labels and independent evidence.
Human hypothesis-map drafts are not automatically ground-truth labels.

## Historical bootstrap and evaluation

The dashboard accepts a private-IP InfluxDB 1.x connection on port 8086 and a
read-only account. It uses generated SELECTs for reviewed entities/fields only,
no arbitrary SQL, redirects, proxies, database writes, raw HA context identities,
media titles or direct internal database-file access. Credentials stay in the
running import's memory; they are not saved or included in responses/journals.
Use local HTTPS when available; HTTP credentials travel on the trusted LAN.
An interrupted import requires reconnecting its read-only account; existing
sources remain intact. Mappings use the standard HA Influx domain/entity tags
and unit measurements. Ambiguous series fail rather than silently merge.

Recent 21-day history is queried first. Older requested years are then retained
as immutable private source pages. The import has a 4,096-query, approximately
512 MiB source-byte, 100,000 sampled-event-per-phase cap and one-second pacing.
Reaching a cap produces a visible partial result, never a false complete archive.
All raw pages read are preserved. Training uses at most 6,000 snapshots, retaining
bounded old/current windows, not every raw archive row. Sparse readings expire
after thirty minutes; gaps are not interpolated and availability remains
uncertain. Full preserved source coverage and model sample size are different.

The worker also replays selected existing capture journals. The companion
refreshes unchanged selected HA states every five minutes without treating them
as actions or dropping queued real transitions. Gaps invalidate continuity.
Snapshot endpoints across a move or gap cannot become training examples.

The classifier learns categorical conditional distributions with training-only
numeric quantiles and smoothed counts. Chronological validation selects both the
calibration temperature and all-history/current-home/recent candidates. A later
untouched current-home period compares Brier loss and balanced accuracy against
persistence, prevalence and an hourly baseline. A target needs variation and
adequate history; constant targets stay untrained. Less than seven days of
history or a one-day holdout stays experimental even if a score looks good.
All probabilities remain preliminary, not assurances of action appropriateness.

## Continuous shadow testing

Real predictions are journaled before their five-minute outcomes are checked.
The feed shows matching/different reported outcomes and unknown coverage.
Snapshots and automatic actions never become preference labels. Every six hours,
new data can train a new private model version; test outcomes cannot select or
alter its fitted preprocessing/calibration. A failed fit retains prior models.
Independent outcomes measure behavior agreement, not preference satisfaction.

Action candidates show the predicted change and every blocking reason, including
manual/unattributed-change holds (30 minutes), 22:00–07:00 brightness max 51/255,
climate setpoint bounds 18–24 °C or 65–75 °F, unknown temperature units,
assumed curtain positions, excluded lineage, missing/out-of-distribution inputs,
low probability, baseline failure, unestablished preference and absent execution.
These shadow limits do not modify existing HA automation or device settings.
Future execution still requires separate target/equipment policy approval.

The companion renews passive captures up to 24 hours at a time only while a
shadow campaign remains active. The campaign preserves its original deadline
through worker restart and writes a fresh private journal segment; stop markers
prevent a user-stopped campaign from resuming. No unbounded restart policy.
Existing independent HA observation automations remain separate.

Private retention caps: 32 campaign segments, four 16 MiB decision journals per
segment, 128 model versions per segment, bounded capture/source archives. No old
sources, models, entities or data are deleted. Storage failure stops shadow
processing while passive capture remains independent. Archive and training jobs
run in bounded background threads; Unraid defaults use 1 GiB/no extra swap,
2 CPUs, 32 PIDs, minimal capabilities and read-only container root.

## Browser access

A separately signed workspace credential can be remembered for 30 days. It reads
context and manages only shadow start/stop/archive jobs on the same origin. It
cannot change entity scope, access the master key, forward fake observations,
run commands, train arbitrary targets or control devices. Existing read-only
credentials keep their original permissions; pair once to authorize new job
controls. Forget clears local access; copied credentials expire or become invalid
when the worker master key rotates. Browser storage can be read by someone with
access to that browser/profile. Cross-origin job requests are refused.

## Explicit preference feedback

Each proposal asks whether that state would be appropriate in the context shown.
An authenticated **Yes** becomes an explicit desired-state review, not a label
inferred from automation or an unchanged device. **No** trains rejection/acceptance
judgment but never invents an opposite desired setting. **Unsure** remains an
uncertain review and is not a training label. Reviews are immutable private files,
bounded to 1,024 records per campaign segment. Identical retries cannot inflate
counts. A revised or undone review writes a new revision and replaces only its
effective training label; previous records remain preserved.
Excluded input lineage prevents new labels or preference inference.

With 100 varied explicit reviews for a target, the worker can fit separate desired
state and appropriateness models with chronological validation/calibration and
held-out comparisons against current-state/prevalence baselines. Short/single-class
history stays experimental or untrained. Observed-state predictions remain
separate from preference predictions. The mandatory execution gate is never
removed by a review. This is a working supervised feedback path, not a claim of
already learned household preferences or a semantic shower/activity classifier.
