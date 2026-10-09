"""Bounded, local multi-target state learning. No device execution or preference inference.

Chronological validation calibrates a categorical Bayes model; independent later
history compares it with persistence, prevalence and an hourly schedule baseline.
Historical automation states are outcomes, never preference or correction labels.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import math

from policy import SafeError, instant

STATES = {'on','off','open','closed','heat','cool','auto','dry','fan_only','heat_cool','playing','paused','idle','standby'}
TARGET_DOMAINS = {'light','binary_sensor','fan','climate','cover'}
MAX_ROWS = 6000
MAX_FEATURES = 24


def time_ms(value):
    return instant(value)//1000000


def finite(value):
    return type(value) in {int,float} and math.isfinite(value)


def channels(entity, row):
    if row.get('availability') != 'reported':
        return {}
    state = row.get('state')
    domain = entity.split('.')[0]
    if domain=='cover' and row.get('device_class') not in {'curtain','shade','blind','shutter'}:
        return {}
    result = {'state':state} if state in STATES and domain in TARGET_DOMAINS else {}
    attrs = dict(row.get('attributes',{}))
    if domain=='light' and not finite(attrs.get('color_temp_kelvin')) and finite(attrs.get('color_temp')) and attrs['color_temp']>0:
        attrs['color_temp_kelvin']=1000000/attrs['color_temp']
    for key,step in [('brightness',32),('color_temp_kelvin',500),('temperature',1),('current_position',10),('percentage',10)]:
        if domain not in {'light','climate','cover','fan'} or not finite(attrs.get(key)):
            continue
        # Off lights have no desired brightness/color, and absent setpoints stay absent.
        if domain == 'light' and state != 'on':
            continue
        value=int(round(attrs[key]/step)*step)
        if key=='brightness':value=min(255,max(0,value))
        result[key] = str(value)
    return result


def features(observations, room, timestamp, previous=None):
    stamp=datetime.fromtimestamp(timestamp/1000,timezone.utc)
    result={'@utc_hour':stamp.hour,'@weekday':stamp.weekday()}
    for entity,row in sorted(observations.items()):
        # Room-local evidence plus occupancy, power and water confounders across rooms.
        if row.get('room') != room and row.get('device_class') not in {'occupancy','presence','motion','power','volume_flow_rate'} and not entity.startswith('valve.'):
            continue
        if row.get('availability') != 'reported':
            result[entity+'|available']='missing'
            continue
        result[entity+'|available']='reported'
        if row.get('state') in STATES:
            result[entity+'|state']=row['state']
        if finite(row.get('value')):
            result[entity+'|value|'+str(row.get('unit',''))]=row['value']
            old=(previous or {}).get(entity,{})
            if (old.get('availability')=='reported' and old.get('unit')==row.get('unit') and finite(old.get('value'))
                    and row.get('segment') is not None and old.get('segment')==row.get('segment')):
                result[entity+'|delta5min|'+str(row.get('unit',''))]=row['value']-old['value']
        for key,value in sorted(row.get('attributes',{}).items()):
            if finite(value):
                result[entity+'|'+key+'|'+str(row.get('unit',''))]=value
            elif key=='hvac_action' and value in {'heating','cooling','idle','off','fan','drying'}:
                result[entity+'|hvac_action']=value
    priority=lambda item:(0 if item[0].startswith('@') else 1 if item[0].endswith('|state') else 2 if '|value|' in item[0] or '|delta5min|' in item[0] else 3,item[0])
    return dict(sorted(result.items(),key=priority)[:MAX_FEATURES])


def bucket(value, boundaries):
    if finite(value):
        return str(sum(value>bound for bound in boundaries))
    return value if isinstance(value,str) else 'missing'


def fit(examples):
    names=sorted({k for e in examples for k in e['x']})
    # Stable bounded selection; rank features by observed variation, not the test.
    names=sorted(names,key=lambda k:(-len({str(e['x'].get(k)) for e in examples}),k))[:MAX_FEATURES]
    bins={}
    for name in names:
        values=sorted(e['x'][name] for e in examples if finite(e['x'].get(name)))
        bins[name]=sorted(set(values[int((len(values)-1)*q)] for q in [.2,.4,.6,.8])) if values else []
    labels=sorted({e['y'] for e in examples})
    counts=Counter(e['y'] for e in examples)
    tables={label:{name:Counter() for name in names} for label in labels}
    vocab={name:set() for name in names}
    for e in examples:
        for name in names:
            value=bucket(e['x'].get(name),bins[name]);tables[e['y']][name][value]+=1;vocab[name].add(value)
    ranges={name:[min(v),max(v)] for name in names if (v:=[e['x'][name] for e in examples if finite(e['x'].get(name))])}
    return {'features':names,'bins':bins,'labels':labels,'counts':dict(counts),'tables':tables,
            'vocab':{k:len(v)+1 for k,v in vocab.items()},'ranges':ranges,'temperature':1,'examples':len(examples)}


def infer(model, x):
    scores={label:math.log((model['counts'][label]+1)/(model['examples']+len(model['labels']))) for label in model['labels']}
    terms={label:[] for label in model['labels']}
    unknown=0
    for name in model['features']:
        value=bucket(x.get(name),model['bins'][name]);unknown+=int(value=='missing')
        for label in scores:
            count=model['tables'][label][name].get(value,0)
            term=math.log((count+1)/(model['counts'][label]+model['vocab'][name]))
            scores[label]+=term;terms[label].append((name,term))
    peak=max(scores.values());exps={k:math.exp((v-peak)/model['temperature']) for k,v in scores.items()}
    total=sum(exps.values());probabilities={k:v/total for k,v in exps.items()}
    best=max(probabilities,key=probabilities.get)
    other=max((k for k in probabilities if k!=best),key=probabilities.get)
    evidence=sorted([{'feature':name,'log_support':round(term-dict(terms[other])[name],4),'value':x.get(name)}
                     for name,term in terms[best]],key=lambda v:abs(v['log_support']),reverse=True)[:6]
    outside=[]
    for name,(low,high) in model['ranges'].items():
        value=x.get(name);width=max(high-low,1)
        if finite(value) and (value<low-width or value>high+width):outside.append(name)
    return {'prediction':best,'probabilities':probabilities,'evidence':evidence,
            'abstained':unknown>len(model['features'])/3 or bool(outside),'out_of_distribution':outside}


def score(examples, predict, labels):
    loss=0;correct=Counter();totals=Counter()
    for e in examples:
        p=predict(e);guess=max(p,key=p.get);totals[e['y']]+=1;correct[e['y']]+=int(guess==e['y'])
        loss+=sum((p.get(label,0)-int(label==e['y']))**2 for label in labels)
    return {'examples':len(examples),'brier':round(loss/len(examples),6),
            'balanced_accuracy':round(sum(correct[k]/totals[k] for k in totals)/len(totals),6),
            'outcome_counts':dict(totals),'classes_observed':len(totals)}


def train(rows, move_date, allowed):
    if not isinstance(rows,list) or not 300<=len(rows)<=MAX_ROWS:
        raise SafeError('300 to 6000 chronological snapshots required.')
    move=time_ms(move_date);previous=None;groups=defaultdict(list)
    for row in rows:
        if not isinstance(row,dict) or row.keys()!={'time','observations'} or not isinstance(row['observations'],dict) or set(row['observations'])-allowed:
            raise SafeError('Historical snapshot outside reviewed scope.')
        t=time_ms(row['time'])
        if previous is not None and t<=previous:raise SafeError('Historical snapshots must be strictly chronological.')
        previous=t
    for index,(before,after) in enumerate(zip(rows,rows[1:])):
        t,end=time_ms(before['time']),time_ms(after['time'])
        if not 240000<=end-t<=360000 or t<move<=end:
            continue
        room_features={}
        for entity,current in before['observations'].items():
            future=after['observations'].get(entity,{})
            current_channels=channels(entity,current)
            for channel,target in channels(entity,future).items():
                if channel not in current_channels:continue
                # Unknown endpoints/gaps cannot establish continuity.
                if current.get('segment') is None or current.get('segment')!=future.get('segment'):continue
                identity=entity+'|'+channel
                if identity not in groups and len(groups)>=24:continue
                room=current['room']
                past=rows[index-1] if index and 240000<=t-time_ms(rows[index-1]['time'])<=360000 else None
                if room not in room_features:room_features[room]=features(before['observations'],room,t,past['observations'] if past else None)
                groups[identity].append({'time':t,'end':end,'x':room_features[room],
                    'y':target,'current':current_channels[channel],'actor':future.get('actor','unattributed'),
                    'unit':current.get('unit'),'current_home':t>=move})
    models={};reports={}
    for identity,examples in groups.items():
        if len(models)>=24:reports[identity]={'state':'model_capacity_waiting','examples':len(examples)};continue
        recent=[e for e in examples if e['current_home']]
        if len(recent)<300:reports[identity]={'state':'insufficient_current_home_history','examples':len(examples),'current_home_examples':len(recent)};continue
        cut1=recent[int(len(recent)*.6)]['time'];cut2=recent[int(len(recent)*.8)]['time']
        training=[e for e in examples if e['end']<cut1]
        validation=[e for e in examples if cut1<=e['time'] and e['end']<cut2]
        test=[e for e in examples if cut2<=e['time']]
        if identity.endswith('|temperature') and len({e['unit'] for e in examples})!=1:
            reports[identity]={'state':'incompatible_temperature_units','examples':len(examples)};continue
        labels=sorted({e['y'] for e in training})
        if len(labels)<2 or not validation or not test or min(Counter(e['y'] for e in training).values())<10:
            reports[identity]={'state':'insufficient_variation','examples':len(examples)};continue
        if any(e['y'] not in labels for e in validation+test):
            reports[identity]={'state':'unseen_outcome','examples':len(examples)};continue
        candidate_models={};candidate_scores={}
        for name,subset in [('all_history',training),('current_home',[e for e in training if e['current_home']]),
                            ('recent_14_days',[e for e in training if e['time']>=max(move,cut1-14*86400000)])]:
            if len(subset)<100 or len({e['y'] for e in subset})<2:continue
            model=fit(subset);candidates={}
            if any(e['y'] not in model['labels'] for e in validation):continue
            for temperature in [1,2,4,8,16]:
                model['temperature']=temperature
                candidates[temperature]=score(validation,lambda e:infer(model,e['x'])['probabilities'],labels)['brier']
            model['temperature']=min(candidates,key=candidates.get)
            candidate_models[name]=model;candidate_scores[name]=min(candidates.values())
        if not candidate_models:reports[identity]={'state':'insufficient_variation','examples':len(examples)};continue
        selected=min(candidate_scores,key=candidate_scores.get);model=candidate_models[selected]
        priors={label:sum(e['y']==label for e in training)/len(training) for label in labels}
        hours=defaultdict(Counter)
        for e in training:hours[e['x']['@utc_hour']][e['y']]+=1
        def hourly(e):
            counts=hours[e['x']['@utc_hour']];n=sum(counts.values())
            return {k:(counts[k]+1)/(n+len(labels)) for k in labels}
        results={'learned':score(test,lambda e:infer(model,e['x'])['probabilities'],labels),
                 'persistence':score(test,lambda e:{e['current']:1},labels),
                 'prevalence':score(test,lambda e:priors,labels),'hourly':score(test,hourly,labels)}
        best=min(results[k]['brier'] for k in ['persistence','prevalence','hourly'])
        useful=(results['learned']['brier']<best*.95 and results['learned']['balanced_accuracy']>=results['persistence']['balanced_accuracy']
                and results['learned']['classes_observed']>=2 and examples[-1]['end']-examples[0]['time']>=7*86400000
                and examples[-1]['end']-cut2>=86400000)
        entity,channel=identity.split('|')
        models[identity]={'model':model,'entity_id':entity,'channel':channel,'room':rows[-1]['observations'].get(entity,{}).get('room'),
                          'evaluation_state':'beats_baselines' if useful else 'experimental','lineage':sorted({name.split('|')[0] for name in model['features'] if not name.startswith('@')})}
        reports[identity]={'state':models[identity]['evaluation_state'],'task':'five_minute_reported_state_forecast',
            'examples':len(examples),'current_home_examples':len(recent),'old_home_examples':len(examples)-len(recent),'selected_on_validation':selected,
            'validation_candidates':candidate_scores,'train_end':cut1,'test_start':cut2,'calibration_temperature':model['temperature'],
            'held_out':results,'preference_labels':0,'actor_counts':dict(Counter(e['actor'] for e in examples)),
            'limitations':['Reported sensor occupancy is not verified human occupancy; pets and multiple occupants remain ambiguous.',
                          'Observed automation/device behavior is not a desired action or preference.',
                          'Archive continuity is limited to thirty minutes and is not verified physical availability.',
                          'Probability calibration is preliminary and correlations are not causal.']}
    return {'schema':1,'models':models,'reports':reports,'move_date':move_date,'source_rows':len(rows),'control_enabled':False}


def propose(identity, model, result, current, observations, policy, now_ms):
    """Generate an inert candidate with every guard reason, never an executable call."""
    entity=model['entity_id'];channel=model['channel'];reasons=[]
    if result['abstained']:reasons.append('missing_or_out_of_distribution_inputs')
    if model['evaluation_state']!='beats_baselines':reasons.append('model_has_not_beaten_baselines')
    confidence=max(result['probabilities'].values())
    if confidence<.75:reasons.append('low_forecast_probability')
    if any(e not in observations for e in model['lineage']+[entity]):reasons.append('excluded_or_missing_lineage')
    row=observations.get(entity,{})
    if row.get('availability')!='reported':reasons.append('target_unavailable')
    if entity.startswith('light.') and channel=='state' and result['prediction']=='on' and policy['night']:
        brightness=row.get('attributes',{}).get('brightness')
        if not finite(brightness) or brightness>policy['night_brightness_max']:
            reasons.append('nighttime_brightness_unknown_or_above_limit')
    if row.get('actor')=='user_associated' and row.get('record_kind')=='state_update' and now_ms-time_ms(row['time'])<policy['manual_hold_seconds']*1000:
        reasons.append('manual_priority_hold')
    predicted=result['prediction']
    value=None
    if channel!='state':
        value=float(predicted)
        if channel=='brightness' and policy['night'] and value>policy['night_brightness_max']:
            reasons.append('nighttime_brightness_limit')
        if channel=='temperature' and not policy['climate_min']<=value<=policy['climate_max']:
            reasons.append('climate_setpoint_limit')
        if channel=='temperature' and row.get('unit') not in {'°C','°F'}:
            reasons.append('temperature_unit_not_verified')
    if entity.startswith('binary_sensor.'):
        reasons.append('sensor_evidence_forecast_has_no_action')
    if entity.startswith('cover.') and row.get('assumed_state'):
        reasons.append('curtain_position_is_assumed')
    if entity.startswith('climate.'):
        target=row.get('attributes',{}).get('temperature')
        if row.get('unit') not in {'°C','°F'} or not finite(target) or not policy['climate_min']<=target<=policy['climate_max']:
            reasons.append('unsafe_or_unknown_current_climate_setpoint')
    # This mandatory gate cannot be removed by any model/policy/UI feedback.
    reasons.append('preference_not_established')
    reasons.append('execution_not_implemented')
    return {'target':entity,'channel':channel,'predicted':predicted,'current':current,'change_predicted':predicted!=current,
            'probability':round(confidence,4),'kind':'observed_behavior_candidate','desired_action_learned':False,
            'blocked_by':reasons,'would_change':predicted!=current,'executed':False,'evidence':result['evidence']}
