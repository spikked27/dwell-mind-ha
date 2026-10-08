"""Offline cross-room comparison; confidence/coverage before model scores."""
import argparse
import json
import os
from pathlib import Path

from office_report import analyze
from policy import instant, strict_json
from rooms import LIVING_ROOM, OFFICE


def fraction(value, denominator):
    return round(100 * value / denominator, 2) if denominator else None


def compare(reports):
    if len(reports) != 2 or {r['room'] for r in reports} != {'Office', 'Living Room'}:
        raise ValueError("Office and Living Room reports required")
    reference = reports[0]
    for report in reports[1:]:
        if (instant(report['start']) != instant(reference['start'])
                or instant(report['end']) != instant(reference['end'])
                or report['timezone'] != reference['timezone']):
            raise ValueError("Comparison requires the same time window and timezone")
    summaries = []
    for report in reports:
        totals = report['totals']
        collection = report['collection']
        model = report['routine_baseline']
        hourly, constant = model['evaluation']['hourly_brier'], model['evaluation']['constant_brier']
        summaries.append({
            'room': report['room'], 'rows': collection['rows'], 'queries': collection['queries'],
            'incomplete_entity_days': sum(e['incomplete_days'] for e in collection['entities'].values()),
            'inferred_known_coverage_percent': fraction(totals.get('presence_evidence_known_minutes', 0), totals.get('duration_minutes', 0)),
            'sensor_disagreement_percent_of_both_known': fraction(totals.get('sensor_disagreement_minutes', 0), totals.get('both_sensors_known_minutes', 0)),
            'derived_off_raw_active_minutes': totals.get('derived_off_raw_active_minutes', 0),
            'primary_off_other_light_on_minutes': totals.get('primary_off_extra_light_on_minutes', 0),
            'sensor_active_media_active_minutes': totals.get('sensor_active_media_active_minutes', 0),
            'review_candidates': len(report['review_candidates']),
            'test_bins': model['evaluation']['test_bins'], 'hourly_brier': hourly, 'constant_brier': constant,
            'hourly_better_in_this_sample': hourly < constant if hourly is not None and constant is not None else None,
            'control_enabled': False, 'preference_labels': 0,
        })
    return {'start': reference['start'], 'end': reference['end'], 'timezone': reference['timezone'], 'rooms': summaries,
            'scope': 'Same archive week and 120-minute state hold. Coverage is reconstructed-known time, not proven recorder uptime.',
            'decision': 'No model promoted for control; short historical comparisons do not establish human preference or safety.',
            'next_milestone': 'Connect passive provenance/availability projection to a read-only event transport on the separate host, then evaluate multiple weeks.'}


def markdown(result):
    lines = ['# Office / Living Room pilot comparison', '', f"Window: {result['start']} to {result['end']}", '',
             result['scope'], '', '| Metric | Office | Living Room |', '| --- | ---: | ---: |']
    by_room = {r['room']: r for r in result['rooms']}
    for key in ['rows', 'queries', 'incomplete_entity_days', 'inferred_known_coverage_percent',
                'sensor_disagreement_percent_of_both_known', 'derived_off_raw_active_minutes',
                'primary_off_other_light_on_minutes', 'sensor_active_media_active_minutes', 'review_candidates',
                'test_bins', 'hourly_brier', 'constant_brier']:
        lines.append(f"| {key.replace('_', ' ')} | {by_room['Office'][key]} | {by_room['Living Room'][key]} |")
    lines += ['', 'Unknown coverage remains unknown, not an absence label. Media and lighting state are context only.', '',
              result['decision'], '', 'Next milestone: ' + result['next_milestone'], '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Compare bounded Office and Living Room datasets offline')
    parser.add_argument('--office-input', required=True)
    parser.add_argument('--living-room-input', required=True)
    parser.add_argument('--output-prefix', required=True)
    args = parser.parse_args()
    reports = []
    for path, profile in [(args.office_input, OFFICE), (args.living_room_input, LIVING_ROOM)]:
        with Path(path).open('rb') as handle:
            raw = handle.read(8388609)
        if len(raw) > 8388608:
            raise SystemExit('Input exceeds byte budget.')
        reports.append(analyze(strict_json(raw), profile=profile))
    result = compare(reports)
    os.umask(0o077)
    prefix = args.output_prefix
    outputs = [(Path(prefix + '.comparison.json'), json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + '\n'),
               (Path(prefix + '.comparison.md'), markdown(result))]
    if any(p.exists() for p, _ in outputs):
        raise SystemExit('Refusing to overwrite an existing comparison.')
    for path, text in outputs:
        with path.open('x') as handle:
            handle.write(text)
    print(json.dumps({'saved_prefix': prefix, 'rooms': result['rooms']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
