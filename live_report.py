"""Offline diagnostics for projected journals; no credentials, network or training."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import stat

from policy import SafeError, instant, strict_json
from rooms import PROFILES

MAX_BYTES = 67108864
MAX_ROWS = 100000


def load_journals(paths):
    if not 1 <= len(paths) <= 4:
        raise SafeError('One to four journal files required.')
    rows, total = [], 0
    for path in paths:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise SafeError('Journal must be owner-only, service-owned and regular.')
            while True:
                line = handle.readline(16385)
                if not line:
                    break
                total += len(line)
                if len(line) > 16384 or total > MAX_BYTES or len(rows) >= MAX_ROWS:
                    raise SafeError('Offline journal input budget exceeded.')
                rows.append(strict_json(line))
    return rows


def summarize(rows, profiles):
    mapping = {entity: profile.name for profile in profiles for entity in profile.entities}
    if not rows:
        raise SafeError('No projected observations.')
    by_entity = {e: [] for e in mapping}
    previous = None
    for row in rows:
        if (not isinstance(row, dict) or row.get('entity_id') not in mapping
                or row.get('room') != mapping[row['entity_id']] or row.get('eligible_preference_label') is not False
                or row.get('record_kind') not in {'snapshot','state_update','gap','removed'}):
            raise SafeError('Journal is not an allowlisted observation-only projection.')
        t = instant(row.get('time'))
        if previous is not None and t < previous:
            raise SafeError('Journal files/rows must be supplied chronologically.')
        previous = t
        by_entity[row['entity_id']].append((t,row))
    start, end = instant(rows[0]['time']), instant(rows[-1]['time'])
    total_minutes = (end-start)/60e9
    summaries = {}
    for entity, entries in by_entity.items():
        durations, actors, kinds = Counter(), Counter(), Counter()
        known = 0
        for index, (t,row) in enumerate(entries):
            next_time = entries[index+1][0] if index+1 < len(entries) else end
            duration = (next_time-t)/60e9
            state = row.get('state')
            durations[state if state in {'on','off','playing','paused','idle','standby','buffering','numeric'} else 'unknown'] += duration
            if row.get('availability') == 'reported':
                known += duration
            kinds[row['record_kind']] += 1
            # A snapshot/heartbeat/gap is never an action, regardless of stored context.
            if entity.startswith('light.') and row['record_kind'] == 'state_update':
                actor = row.get('actor')
                if actor not in {'engine','automation','script','user_associated','unattributed'}:
                    raise SafeError('Unknown observation actor class.')
                actors[actor] += 1
        # Unobserved prefix is unknown; do not extend a row beyond observed EOF.
        if entries:
            durations['unknown'] += (entries[0][0]-start)/60e9
        else:
            durations['unknown'] = total_minutes
        summaries[entity] = {'room':mapping[entity], 'rows':len(entries), 'record_kinds':dict(kinds),
                             'reported_state_minutes':round(known,3),
                             'reported_state_coverage_percent':round(100*known/total_minutes,2) if total_minutes else None,
                             'state_minutes':{k:round(v,3) for k,v in durations.items()},
                             'light_update_actor_counts':dict(actors),
                             'terminal_gap_present':bool(entries and entries[-1][0]==end and entries[-1][1]['record_kind']=='gap')}
    return {'start':rows[0]['time'], 'end':rows[-1]['time'], 'rows':len(rows), 'entities':summaries,
            'all_entities_have_terminal_gap':all(s['terminal_gap_present'] for s in summaries.values()),
            'preference_labels':0, 'control_enabled':False,
            'scope':'Coverage means projected HA state evidence within observed capture time, not verified human occupancy or physical sensor uptime.',
            'limitations':['Snapshots are not actions; light state updates are not necessarily commands.',
                           'Unknown/gap intervals are never absence labels.',
                           'Unmarked journal EOF ends observation; it never proves ongoing state.',
                           'No user-associated event automatically establishes a corrective preference.']}


def main():
    parser = argparse.ArgumentParser(description='Summarize private projected journals offline')
    parser.add_argument('--input', nargs='+', required=True)
    parser.add_argument('--room', choices=[*PROFILES,'both'], default='both')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    profiles = list(PROFILES.values()) if args.room=='both' else [PROFILES[args.room]]
    try:
        report = summarize(load_journals(args.input), profiles)
        os.umask(0o077)
        with Path(args.output).open('x') as handle:
            json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write('\n')
        print(json.dumps({'saved':args.output,'rows':report['rows'],'terminal_gaps':report['all_entities_have_terminal_gap']}))
    except (SafeError,OSError,ValueError,TypeError):
        raise SystemExit('Offline report refused; check private input format/order and output path. No payload logged.') from None


if __name__=='__main__':
    main()
