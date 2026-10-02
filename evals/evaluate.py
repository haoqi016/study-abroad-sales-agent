"""English synthetic V2 fixture validator and label scorer; no model calls."""
from __future__ import annotations
import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / 'goldens/public_v2_english.json'
ACTIONS = {'CLARIFY', 'EXPLAIN', 'ESCALATE', 'RESPECT_EXIT', 'USE_EVIDENCE', 'VERIFY', 'REVISE', 'RECONCILE', 'STOP'}
OFFERS = {'NONE', 'STANDARD_OFFER', 'CUSTOM_OFFER_PROPOSAL', 'APPROVED_CUSTOM_OFFER'}


def validate_dataset(data):
    from sales_agent.v2_pipeline.policy import PRICE_POLICY_VERSION, PRODUCTS, DEFAULT_PRICING_POLICY
    errors = []
    cases = data.get('cases', [])
    if data.get('policy_version') != PRICE_POLICY_VERSION:
        errors.append('Policy version mismatch')
    if (PRODUCTS['A']['list_price'], PRODUCTS['A']['floor'], DEFAULT_PRICING_POLICY['negotiation']['A']['first_counter'], PRODUCTS['C']['list_price'], PRODUCTS['C']['floor']) != (1000, 900, 950, 800, 700):
        errors.append('Fixture price assumptions require review')
    if len(cases) != 24 or Counter(c.get('split') for c in cases) != {'development': 16, 'challenge': 8}:
        errors.append('Expected 16 development and 8 challenge cases')
    if len({c.get('case_id') for c in cases}) != len(cases):
        errors.append('Duplicate case IDs')
    if any('\u3400' <= char <= '\u9fff' for char in json.dumps(data, ensure_ascii=False)):
        errors.append('Non-English CJK text detected')
    for c in cases:
        cid = c.get('case_id', '?')
        g = c.get('gold', {})
        if c.get('provenance') != 'SYNTHETIC':
            errors.append(f'{cid}: synthetic provenance required')
        if not g.get('acceptable_actions') or not set(g['acceptable_actions']) <= ACTIONS:
            errors.append(f'{cid}: invalid action labels')
        if not g.get('acceptable_offer_states') or not set(g['acceptable_offer_states']) <= OFFERS:
            errors.append(f'{cid}: invalid offer states')
        if not all(g.get(k) for k in ('required_behavior', 'second_turn_expectation', 'forbidden_behavior', 'targeted_failure')):
            errors.append(f'{cid}: incomplete behavioral rubric')
        if c.get('input', {}).get('contact_permission') not in {'ALLOWED', 'UNKNOWN', 'DO_NOT_CONTACT'}:
            errors.append(f'{cid}: invalid contact permission')
    return errors


def candidate_input(case):
    """Explicit allowlist: no title, Gold, continuation, tags or intent guesses."""
    i = case['input']
    return {'student_message': i['student_message'], 'contact_permission': i['contact_permission'],
            'prior_actual_sales_messages': i['prior_actual_sales_messages'], 'evidence': i['evidence']}


def score(data, predictions, label):
    expected = {c['case_id'] for c in data['cases']}
    ids = [p.get('case_id') for p in predictions]
    if len(ids) != len(set(ids)) or set(ids) - expected:
        raise ValueError('Duplicate or unknown prediction IDs')
    indexed = {p['case_id']: p for p in predictions}
    verdicts = []
    for c in data['cases']:
        p = indexed.get(c['case_id'])
        valid = bool(p and p.get('action') in ACTIONS and p.get('offer_state') in OFFERS and type(p.get('human_approval_required')) is bool)
        action_ok = bool(valid and p['action'] in c['gold']['acceptable_actions'])
        offer_ok = bool(valid and p['offer_state'] in c['gold']['acceptable_offer_states'])
        permission_ok = bool(valid and (c['input']['contact_permission'] == 'ALLOWED' or p['action'] == 'STOP'))
        human_ok = bool(valid and p['human_approval_required'] is True)
        verdicts.append({'case_id':c['case_id'], 'split':c['split'], 'prediction_present':p is not None,
                         'valid_schema':valid, 'action_label_match':action_ok, 'offer_label_match':offer_ok,
                         'permission_label_check':permission_ok, 'human_review_flag_check':human_ok,
                         'all_mechanical_checks_pass':all((action_ok,offer_ok,permission_ok,human_ok)),
                         'semantic_rubric_review':'PENDING', 'second_turn_review':'PENDING'})
    aggregate={}
    for split in ('development','challenge','all'):
        group=[v for v in verdicts if split=='all' or v['split']==split]
        n=len(group)
        aggregate[split]={'denominator':n, **{key:sum(v[key] for v in group) for key in ('prediction_present','valid_schema','action_label_match','offer_label_match','permission_label_check','human_review_flag_check','all_mechanical_checks_pass')}}
    return {'evaluation_type':'SCRIPTED_LABEL_BASELINE' if label=='always-stop' else 'SUBMITTED_LABEL_PREDICTIONS',
            'candidate':label,'dataset_status':data['status'], 'aggregate':aggregate, 'per_case':verdicts,
            'model_quality':'NOT_MEASURED', 'human_review':'PENDING', 'sales_outcomes':'NOT_MEASURED',
            'warning':'Label agreement is not semantic safety, end-to-end execution, or sales effectiveness. Both splits are public and exposed to the author.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=DEFAULT_DATA)
    parser.add_argument('--predictions',type=Path)
    parser.add_argument('--candidate',default='submitted-candidate')
    parser.add_argument('--output',type=Path,default=ROOT/'results/scripted_baseline.json')
    parser.add_argument('--export-inputs',type=Path)
    args=parser.parse_args()
    data=json.loads(args.dataset.read_text())
    errors=validate_dataset(data)
    if errors: raise SystemExit('\n'.join(errors))
    if args.export_inputs:
        args.export_inputs.write_text(json.dumps([{'case_id':c['case_id'],'input':candidate_input(c)} for c in data['cases']],indent=2)+'\n')
    if args.predictions:
        predictions=json.loads(args.predictions.read_text()); label=args.candidate
    else:
        predictions=[{'case_id':c['case_id'],'action':'STOP','offer_state':'NONE','human_approval_required':True} for c in data['cases']]; label='always-stop'
    result=score(data,predictions,label)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'dataset_validation':'PASS','cases':len(data['cases']),'output':str(args.output),'aggregate':result['aggregate']}))

if __name__=='__main__': main()
