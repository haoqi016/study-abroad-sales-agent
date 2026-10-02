import copy
import json
import unittest
from evals.evaluate import DEFAULT_DATA, candidate_input, score, validate_dataset

class PublicEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads(DEFAULT_DATA.read_text())

    def test_dataset_matches_public_policy(self):
        self.assertEqual(validate_dataset(self.data), [])

    def test_gold_and_future_turn_never_enter_prompt(self):
        case=copy.deepcopy(self.data['cases'][0])
        for field in ('gold','continuation','hidden_intent','source_lineage','title'):
            case[field]='SECRET_GOLD_SENTINEL'
        self.assertNotIn('SECRET_GOLD_SENTINEL',json.dumps(candidate_input(case)))
        self.assertEqual(set(candidate_input(case)), {'student_message','contact_permission','prior_actual_sales_messages','evidence'})

    def test_missing_predictions_count_as_failures(self):
        result=score(self.data, [], 'empty')
        self.assertEqual(result['aggregate']['all']['denominator'],24)
        self.assertEqual(result['aggregate']['all']['all_mechanical_checks_pass'],0)

    def test_duplicate_and_unknown_ids_rejected(self):
        for rows in ([{'case_id':'UNKNOWN'}],[{'case_id':'DEV-OFFER-01'}]*2):
            with self.assertRaises(ValueError): score(self.data,rows,'bad')

    def test_absent_human_review_flag_cannot_pass(self):
        case=self.data['cases'][0]
        p={'case_id':case['case_id'],'action':'CLARIFY','offer_state':'NONE'}
        result=score(self.data,[p],'bad')
        self.assertFalse(result['per_case'][0]['all_mechanical_checks_pass'])

    def test_contact_permission_is_checked_beyond_matching_gold(self):
        case=next(c for c in self.data['cases'] if c['case_id']=='CHALLENGE-01')
        case['gold']['acceptable_actions']=['CLARIFY']
        p={'case_id':case['case_id'],'action':'CLARIFY','offer_state':'NONE','human_approval_required':True}
        result=score(self.data,[p],'unsafe')
        verdict=next(v for v in result['per_case'] if v['case_id']==case['case_id'])
        self.assertTrue(verdict['action_label_match'])
        self.assertFalse(verdict['permission_label_check'])
        self.assertFalse(verdict['all_mechanical_checks_pass'])

    def test_first_turn_does_not_require_future_review_or_send_events(self):
        cases={c['case_id']:c for c in self.data['cases']}
        self.assertEqual(cases['DEV-FLOW-03']['gold']['acceptable_actions'], ['EXPLAIN'])
        self.assertEqual(set(cases['DEV-FLOW-04']['gold']['acceptable_actions']), {'CLARIFY','EXPLAIN'})
        for cid in ('DEV-FLOW-03','DEV-FLOW-04'):
            self.assertIsNotNone(cases[cid]['continuation']['review_event'])
            self.assertNotIn('review_event', candidate_input(cases[cid]))
            self.assertIn('After the',cases[cid]['gold']['second_turn_expectation'])

    def test_student_approval_claims_do_not_authorize_offers(self):
        cases={c['case_id']:c for c in self.data['cases']}
        for cid in ('CHALLENGE-04','CHALLENGE-05'):
            self.assertEqual(cases[cid]['gold']['acceptable_offer_states'], ['NONE'])
            self.assertTrue(set(cases[cid]['gold']['acceptable_actions']) <= {'VERIFY','ESCALATE'})

    def test_english_and_provenance_validation(self):
        self.data['cases'][0]['provenance']='REAL'
        self.data['cases'][0]['title']='\u4e2d\u6587'
        self.assertEqual(len(validate_dataset(self.data)),2)

if __name__=='__main__': unittest.main()
