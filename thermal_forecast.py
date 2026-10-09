"""Local learned hourly-mean forecast with chronological, move-aware evaluation.

Not a heating controller or a causal heat-response model. No network or credentials.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from policy import SafeError, instant

HOUR = 3600000
DAY = 24 * HOUR
FEATURES = ['current_mean', 'one_hour_change', 'change_from_24_hours_ago', 'utc_hour_sin', 'utc_hour_cos']


def iso(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def samples(dataset):
    if not isinstance(dataset, dict) or dataset.get('unit') not in {'°C', '°F'}:
        raise SafeError('Explicit temperature unit required.')
    rows = dataset.get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 45000:
        raise SafeError('One to 45000 hourly records required.')
    move = instant(dataset['move_date']) // 1000000
    start, end = [instant(dataset[k]) // 1000000 for k in ('start', 'end')]
    if not start < move < end or end - start > 5 * 366 * DAY:
        raise SafeError('Bounded history and a move boundary within it required.')
    values = {}
    seen = set()
    missing = 0
    for row in rows:
        if not isinstance(row, dict) or row.keys() - {'start', 'end', 'mean', 'min', 'max'}:
            raise SafeError('Unexpected historical field.')
        t, value = row.get('start'), row.get('mean')
        if type(t) not in {int, float} or not math.isfinite(t) or t % HOUR or not start <= t < end:
            raise SafeError('Invalid hourly record timestamp.')
        if t in seen:
            raise SafeError('Duplicate history must be reconciled before training.')
        seen.add(t)
        bounds = (-40, 60) if dataset['unit'] == '°C' else (-40, 140)
        if type(value) not in {int, float} or not math.isfinite(value) or not bounds[0] <= value <= bounds[1]:
            missing += 1
            continue
        values[t] = float(value)
    result = []
    for t in sorted(values):
        # Require an uninterrupted 26-hour observed window. No filling downtime,
        # stitching houses, extrapolating past EOF or taking future feature values.
        if not all(t + offset * HOUR in values for offset in range(-24, 2)):
            continue
        if t - 24 * HOUR < move <= t + HOUR:
            continue
        phase = 2 * math.pi * ((t // HOUR) % 24) / 24
        current = values[t]
        x = [current, current - values[t-HOUR], current-values[t-24*HOUR], math.sin(phase), math.cos(phase)]
        result.append({'time': t, 'target_end': t + 2 * HOUR, 'x': x, 'current': current,
                       'seasonal': values[t-23*HOUR], 'target': values[t+HOUR]})
    return result, {'source_rows': len(rows), 'usable_hourly_means': len(values),
                    'invalid_or_missing_means': missing,
                    'missing_hour_slots': int((end-start)//HOUR)-len(values),
                    'supervised_examples': len(result), 'move_boundary': iso(move)}, start, end, move


def solve(matrix, vector):
    a = [list(row) + [value] for row, value in zip(matrix, vector)]
    n = len(vector)
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(a[row][column]))
        if abs(a[pivot][column]) < 1e-12:
            raise SafeError('Degenerate training matrix.')
        a[column], a[pivot] = a[pivot], a[column]
        scale = a[column][column]
        a[column] = [v / scale for v in a[column]]
        for row in range(n):
            if row != column:
                factor = a[row][column]
                a[row] = [x-factor*y for x, y in zip(a[row], a[column])]
    return [row[-1] for row in a]


def fit(examples):
    if len(examples) < 500:
        raise SafeError('At least 500 training examples required.')
    means = [sum(e['x'][i] for e in examples)/len(examples) for i in range(len(FEATURES))]
    scales = [max(1e-6, math.sqrt(sum((e['x'][i]-means[i])**2 for e in examples)/len(examples)))
              for i in range(len(FEATURES))]
    n = len(FEATURES) + 1
    matrix, vector = [[0.0]*n for _ in range(n)], [0.0]*n
    for e in examples:
        x = [1.0] + [(value-mean)/scale for value, mean, scale in zip(e['x'], means, scales)]
        y = e['target'] - e['current']
        for i in range(n):
            vector[i] += x[i]*y
            for j in range(n):
                matrix[i][j] += x[i]*x[j]
    # Fixed ridge penalty, not selected against the test set. Intercept unpenalized.
    for i in range(1, n):
        matrix[i][i] += 1.0
    return {'means': means, 'scales': scales, 'coefficients': solve(matrix, vector),
            'training_examples': len(examples), 'training_start': iso(examples[0]['time']),
            'training_last_target_end': iso(examples[-1]['target_end']), 'ridge_penalty': 1.0}


def predict(model, example):
    x = [(value-mean)/scale for value, mean, scale in zip(example['x'], model['means'], model['scales'])]
    # Return an explicit abstention for inputs far outside the fitted distribution.
    if any(abs(value) > 8 for value in x):
        return example['current'], True
    return example['current'] + model['coefficients'][0] + sum(c*v for c, v in zip(model['coefficients'][1:], x)), False


def metrics(examples, forecast):
    errors, abstentions = [], 0
    for e in examples:
        predicted, abstained = forecast(e)
        errors.append(predicted-e['target'])
        abstentions += int(abstained)
    return {'examples': len(examples), 'mae': round(sum(abs(v) for v in errors)/len(errors), 6),
            'rmse': round(math.sqrt(sum(v*v for v in errors)/len(errors)), 6),
            'abstentions_with_persistence_fallback': abstentions}


def forecast_window(result, rows, now_ms):
    """Forecast from 25 completed Recorder hours, never from sparse live events."""
    if not isinstance(rows, list) or not 25 <= len(rows) <= 27:
        raise SafeError('25 to 27 completed hourly means required.')
    values = {}
    bounds = (-40, 60) if result['unit'] == '°C' else (-40, 140)
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {'start','mean'}:
            raise SafeError('Invalid forecast record.')
        t, value = row['start'], row['mean']
        if (type(t) not in {int,float} or not math.isfinite(t) or t % HOUR or t in values
                or type(value) not in {int,float} or not math.isfinite(value) or not bounds[0] <= value <= bounds[1]):
            raise SafeError('Missing or invalid hourly means; no interpolation.')
        values[t] = value
    t = max(values)
    if not t+HOUR <= now_ms < t+2*HOUR:
        raise SafeError('Recent completed hourly statistics required.')
    if not all(t-i*HOUR in values for i in range(25)) or t-24*HOUR < instant(result['coverage']['move_boundary'])//1000000:
        raise SafeError('Uninterrupted current-home window required.')
    current = values[t]
    phase = 2*math.pi*((t//HOUR)%24)/24
    example = {'current':current,'x':[current,current-values[t-HOUR],current-values[t-24*HOUR],math.sin(phase),math.cos(phase)]}
    predicted, abstained = predict(result['model'],example)
    scaled = [(v-m)/s for v,m,s in zip(example['x'],result['model']['means'],result['model']['scales'])]
    return {'state':'abstained' if abstained else 'forecast', 'model_id':result['model_id'],
            'entity_id':result['entity_id'],'unit':result['unit'],'issued_at':iso(now_ms),
            'input_hour':iso(t),'target_start':iso(t+HOUR),'target_end':iso(t+2*HOUR),
            'predicted_mean':None if abstained else round(predicted,4),'persistence_mean':current,
            'observed_hours':[{'start':iso(v),'mean':values[v]} for v in sorted(values) if v>=t-24*HOUR],
            'contributions':[{'feature':name,'change':round(c*v,4)} for name,c,v in zip(FEATURES,result['model']['coefficients'][1:],scaled)],
            'intercept_change':result['model']['coefficients'][0],
            'held_out_mae':result['held_out_test']['learned']['mae'],
            'uncertainty':'Historical MAE is an average error, not a calibrated confidence interval.'}


def train(dataset):
    examples, coverage, start, end, move = samples(dataset)
    test_start, validation_start = end-30*DAY, end-60*DAY
    training = [e for e in examples if e['target_end'] <= validation_start]
    validation = [e for e in examples if validation_start <= e['time'] and e['target_end'] <= test_start]
    test = [e for e in examples if test_start <= e['time'] and e['target_end'] <= end]
    if validation_start <= move or len(validation) < 200 or len(test) < 200:
        raise SafeError('Need two recent nonoverlapping 30-day windows with 200 observed examples each.')
    groups = {'all_history': training, 'current_home': [e for e in training if e['time'] >= move],
              'recent_90_days': [e for e in training if e['time'] >= max(move, validation_start-90*DAY)]}
    models, comparison = {}, {}
    for name, group in groups.items():
        if len(group) >= 500:
            models[name] = fit(group)
            comparison[name] = metrics(validation, lambda e, m=models[name]: predict(m, e))
    if not models:
        raise SafeError('Insufficient observed training data.')
    selected = min(comparison, key=lambda name: comparison[name]['mae'])
    baselines = {'persistence': lambda e: (e['current'], False), 'previous_day': lambda e: (e['seasonal'], False)}
    validation_baselines = {name: metrics(validation, fn) for name, fn in baselines.items()}
    results = {name: metrics(test, fn) for name, fn in baselines.items()}
    results['learned'] = metrics(test, lambda e: predict(models[selected], e))
    best_baseline = min(results['persistence']['mae'], results['previous_day']['mae'])
    gain = 100*(best_baseline-results['learned']['mae'])/best_baseline if best_baseline else 0.0
    # Evaluate independent UTC days, exposing variability instead of one headline average.
    by_day = {}
    for e in test:
        by_day.setdefault(int(e['time']//DAY), []).append(e)
    daily = [{'day': iso(day*DAY)[:10], 'learned_mae': metrics(es, lambda e: predict(models[selected], e))['mae'],
              'persistence_mae': metrics(es, baselines['persistence'])['mae']} for day, es in by_day.items()]
    return {'schema': 1, 'task': 'next_hour_mean_temperature_forecast', 'entity_id': dataset['entity_id'],
            'unit': dataset['unit'], 'control_enabled': False, 'preference_labels': 0,
            'coverage': coverage, 'source_start': iso(start), 'source_end': iso(end),
            'validation_start': iso(validation_start), 'test_start': iso(test_start),
            'selected_on_validation': selected, 'validation_candidates': comparison,
            'validation_baselines': validation_baselines, 'held_out_test': results,
            'mae_improvement_over_best_baseline_percent': round(gain, 2),
            'evaluation_status': 'improves_baselines' if gain >= 5 else 'no_demonstrated_useful_improvement',
            'features': FEATURES, 'model': models[selected], 'test_daily_errors': daily,
            'limitations': ['Predicts hourly averaged reported temperature, not instantaneous physical temperature.',
                            'Not a causal HVAC model; no heating controls or preference labels.',
                            'Same entity ID does not prove the aggregate sensor composition stayed unchanged.',
                            'No gap interpolation. Move-crossing windows omitted. Historical units must be consistent.',
                            'Single chronological holdout is preliminary; repeated seasonal evaluation is required.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = Path(args.input).read_bytes()
    if len(raw) > 8388608:
        raise SystemExit('Historical input exceeds private byte budget.')
    result = train(json.loads(raw))
    with Path(args.output).open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    print(json.dumps({key: result[key] for key in ('evaluation_status', 'selected_on_validation', 'held_out_test',
                                                  'mae_improvement_over_best_baseline_percent')}))


if __name__ == '__main__':
    main()
