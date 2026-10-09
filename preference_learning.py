"""Explicit human-review labels only; automation/state history cannot enter here."""
from collections import Counter

from policy import SafeError
from shadow_learning import fit, infer, score


def train_preferences(examples):
    if not isinstance(examples,list) or not 100<=len(examples)<=1024:
        raise SafeError('100 to 1024 explicit preference reviews required.')
    if any(e.get('source')!='explicit_human_review' or not isinstance(e.get('decision_id'),str) for e in examples):
        raise SafeError('Preference source must be explicit human review.')
    if len({e['decision_id'] for e in examples})!=len(examples):raise SafeError('Duplicate review labels refused.')
    if len({e.get('unit') for e in examples})>1:raise SafeError('Preference labels with different units cannot be merged.')
    examples=sorted(examples,key=lambda e:e['time']);cut1=examples[int(len(examples)*.6)]['time'];cut2=examples[int(len(examples)*.8)]['time']
    training=[e for e in examples if e['time']<cut1];validation=[e for e in examples if cut1<=e['time']<cut2];test=[e for e in examples if e['time']>=cut2]
    counts=Counter(e['y'] for e in training)
    if len(counts)<2 or min(counts.values())<10 or not validation or not test:
        raise SafeError('Need varied preferences across independent review windows.')
    model=fit(training);labels=model['labels']
    if any(e['y'] not in labels for e in validation+test):raise SafeError('New preference outcome needs more observed reviews.')
    candidates={}
    for temperature in [1,2,4,8,16]:
        model['temperature']=temperature;candidates[temperature]=score(validation,lambda e:infer(model,e['x'])['probabilities'],labels)['brier']
    model['temperature']=min(candidates,key=candidates.get)
    prevalence={k:counts[k]/len(training) for k in counts}
    metrics={'learned':score(test,lambda e:infer(model,e['x'])['probabilities'],labels),
             'current_state':score(test,lambda e:{e['current']:1},labels),'prevalence':score(test,lambda e:prevalence,labels)}
    useful=(metrics['learned']['brier']<min(metrics[k]['brier'] for k in ['current_state','prevalence'])*.95
            and metrics['learned']['classes_observed']>=2 and examples[-1]['time']-examples[0]['time']>=7*86400000)
    return {'model':model,'state':'validated_preference_candidate' if useful else 'experimental_preference',
            'held_out':metrics,'labels':len(examples),'source':'explicit_human_review','control_enabled':False}
