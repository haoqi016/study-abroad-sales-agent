"""Dataset integrity and temporal-boundary checks, not model-quality scores."""
import copy
import json
import re
import unittest

from evals.evaluate import DEFAULT_DATA, candidate_input, score
from sales_agent.v2_pipeline.policy import DEFAULT_PRICING_POLICY, PRODUCTS


class PublicGoldDepthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(DEFAULT_DATA.read_text())
        cls.cases = {case['case_id']: case for case in cls.data['cases']}

    def test_every_case_has_actionable_review_guidance(self):
        self.assertEqual(len(self.cases), 24)
        self.assertEqual(sum(c['split'] == 'development' for c in self.cases.values()), 16)
        for cid, case in self.cases.items():
            with self.subTest(case=cid):
                gold = case['gold']
                for key in ('observable_signals', 'hypotheses_not_facts', 'required_unknowns',
                            'evidence_permission_offer_boundaries', 'reviewer_checks'):
                    self.assertTrue(gold[key])
                    self.assertTrue(all(isinstance(s, str) and s.strip() for s in gold[key]))
                self.assertGreaterEqual(len(gold['reviewer_checks']), 2)
                self.assertIn(gold['failure_severity']['level'], {'MEDIUM', 'HIGH', 'CRITICAL'})
                self.assertTrue(gold['failure_severity']['rationale'])
                self.assertTrue(gold['progress_signal']['interpretation'])
                for alternative in gold['first_turn_acceptable_alternatives']:
                    self.assertTrue(alternative['guidance'])
                    self.assertTrue(set(alternative['actions']) <= set(gold['acceptable_actions']))
                    self.assertTrue(set(alternative['offer_states']) <= set(gold['acceptable_offer_states']))

    def test_new_gold_and_future_events_are_excluded_from_all_candidate_inputs(self):
        for case in self.cases.values():
            original = candidate_input(case)
            poisoned = copy.deepcopy(case)
            for key in poisoned:
                if key != 'input':
                    poisoned[key] = {'evaluation_only_secret': 'NEVER_SHOW_THIS_TO_CANDIDATE'}
            self.assertEqual(candidate_input(poisoned), original)
            self.assertNotIn('NEVER_SHOW_THIS_TO_CANDIDATE', json.dumps(candidate_input(poisoned)))

    def test_absent_continuations_are_not_reported_as_observed(self):
        for case in self.cases.values():
            review = case['gold']['continuation_review']
            expected = 'SUPPLIED' if any(case['continuation'].values()) else 'NOT_SUPPLIED'
            self.assertEqual(review['event_status'], expected)
            if expected == 'NOT_SUPPLIED':
                self.assertIn('No continuation event is supplied.', review['update_expectation'])
        self.assertIsNone(self.cases['DEV-FLOW-03']['continuation']['student_message'])
        self.assertIn('NO_CUSTOMER_PROGRESS_SIGNAL', self.cases['DEV-FLOW-03']['gold']['progress_signal']['interpretation'])
        self.assertIn('actual tonight promise', self.cases['DEV-FLOW-04']['gold']['continuation_review']['update_expectation'])

    def test_offer_guidance_uses_only_public_policy_and_real_history_gate(self):
        self.assertEqual(PRODUCTS['A']['list_price'], 1000)
        self.assertEqual(PRODUCTS['A']['floor'], 900)
        negotiation = DEFAULT_PRICING_POLICY['negotiation']['A']
        self.assertEqual(negotiation['first_counter'], 950)
        self.assertEqual(negotiation['min_prior_actual_sales_messages'], 1)
        for cid in ('DEV-OFFER-01', 'DEV-OFFER-02'):
            case = self.cases[cid]
            self.assertEqual(case['input']['prior_actual_sales_messages'], 0)
            self.assertEqual(case['gold']['acceptable_offer_states'], ['NONE'])
            boundary = ' '.join(case['gold']['evidence_permission_offer_boundaries'])
            self.assertIn('950', boundary)
            self.assertIn('actual sales', boundary)
        for cid in ('CHALLENGE-04', 'CHALLENGE-05'):
            self.assertEqual(self.cases[cid]['input']['evidence'], [])
            self.assertEqual(self.cases[cid]['gold']['acceptable_offer_states'], ['NONE'])
            self.assertIn('authoritative', self.cases[cid]['gold']['required_unknowns'][0].lower())

    def test_consent_and_evidence_distinctions_survive_expansion(self):
        for cid in ('CHALLENGE-01', 'CHALLENGE-07'):
            gold = self.cases[cid]['gold']
            self.assertEqual(gold['acceptable_actions'], ['STOP'])
            self.assertEqual(gold['failure_severity']['level'], 'CRITICAL')
        unavailable = self.cases['DEV-EVIDENCE-02']
        empty = self.cases['CHALLENGE-06']
        self.assertEqual(unavailable['input']['evidence'][0]['status'], 'UNAVAILABLE')
        self.assertEqual(empty['input']['evidence'][0]['status'], 'NO_RESULTS')
        for case in (unavailable, empty):
            boundary = ' '.join(case['gold']['evidence_permission_offer_boundaries'])
            self.assertIn('UNAVAILABLE', boundary)
            self.assertIn('NO_RESULTS', boundary)
        self.assertIn('one-time', self.cases['DEV-INTENT-03']['gold']['progress_signal']['interpretation'])
        self.assertIn('SIGNED and PAID require independent evidence', self.cases['CHALLENGE-08']['gold']['progress_signal']['interpretation'])

    def test_pause_accepts_only_scoped_clarification_as_an_alternative(self):
        case = self.cases['DEV-INTENT-03']
        gold = case['gold']
        self.assertEqual(set(gold['acceptable_actions']), {'RESPECT_EXIT', 'CLARIFY'})
        alternatives = {item['actions'][0]: item for item in gold['first_turn_acceptable_alternatives']}
        self.assertIn('one-time resource', alternatives['CLARIFY']['guidance'])
        self.assertIn('only for this narrow permission question', alternatives['CLARIFY']['guidance'])
        self.assertEqual(alternatives['CLARIFY']['offer_states'], ['NONE'])
        for action in ('RESPECT_EXIT', 'CLARIFY', 'EXPLAIN'):
            prediction = {'case_id': case['case_id'], 'action': action,
                          'offer_state': 'NONE', 'human_approval_required': True}
            result = score(self.data, [prediction], 'scoped-permission-alternative')
            verdict = next(v for v in result['per_case'] if v['case_id'] == case['case_id'])
            self.assertEqual(verdict['all_mechanical_checks_pass'], action != 'EXPLAIN')
            # Labels alone cannot establish that a question respects the narrow permission scope.
            self.assertEqual(verdict['semantic_rubric_review'], 'PENDING')

    def test_public_content_is_english_and_has_no_private_lineage(self):
        serialized = json.dumps(self.data, ensure_ascii=False)
        self.assertIsNone(re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]', serialized))
        # Prevent accidental inclusion of private policy numbers or exact lineage metadata.
        public_price_ceiling = max(product['list_price'] for product in PRODUCTS.values())
        self.assertTrue(all(int(value) <= public_price_ceiling
                            for value in re.findall(r'\b\d{3,}\b', serialized)))
        self.assertIsNone(re.search(r'\b[a-f0-9]{64}\b', serialized))
        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        self.assertFalse({'source_lineage', 'source_documents', 'hidden_intent', 'source_text'} & set(keys(self.data)))
        self.assertTrue(all(c['provenance'] == 'SYNTHETIC' for c in self.cases.values()))


if __name__ == '__main__':
    unittest.main()
