# V2 English evaluation package

This is a public, **synthetic** coursework evaluation package. It includes 16 English development scenarios based on the authors' original scenario concepts and 8 newly invented challenge scenarios. All dialogue, amounts, case evidence and review events here are fictional public fixtures. The original development concepts cover pricing, intent, evidence and conversation flow. They have been rewritten rather than released as private transcripts. No independent annotation, owner approval or real customer outcome is claimed.

The challenge set is public and was written by the same implementation team. It is **not an independent holdout**. Both sets can diagnose errors but cannot support an unbiased generalization estimate. Publishing Gold also makes future contamination likely. For a future benchmark, a separate reviewer should author and seal fresh cases before freezing the candidate system.

## Files and reproduction

- `goldens/public_v2_english.json`: 24 cases, English reference behavior, forbidden behavior and second-turn expectations.
- `evaluate.py`: dataset validation, allowlisted candidate input export and mechanical label evaluation.
- `results/scripted_baseline.json`: actual locally executed always-stop baseline, with all 24 individual verdicts.
- `RUBRIC.md`: human review instructions, severity, baselines and reporting protocol.
- `../tests/test_public_eval.py`: regression tests for leakage exclusion, missing cases, permission checks and malformed predictions.

Run from the repository root with Python 3.10 or newer; no API key or external package is needed:

```bash
python -m evals.evaluate
python -m unittest discover -s tests -p 'test_public_eval.py'
python -m evals.evaluate --export-inputs /tmp/public_v2_inputs.json
```

The default command evaluates a scripted candidate that always returns STOP, NONE and a human-review flag. This is a deliberately unhelpful abstention baseline. It does not call the V2 runtime or a language model. It agrees with the expected action on **2/24** cases (development **0/16**, challenge **2/8**); all mechanical checks pass on **2/24**. Its permission labels pass on 24/24, illustrating why a single safety-looking score cannot measure usefulness. These results are repeatable label checks, not a measured model-quality result.

For a candidate, provide a JSON array containing one object per case:

```json
[
  {"case_id": "DEV-OFFER-01", "action": "CLARIFY", "offer_state": "NONE", "human_approval_required": true}
]
```

```bash
python -m evals.evaluate --predictions /tmp/predictions.json --candidate candidate-v1 --output /tmp/candidate-report.json
```

Omitted cases remain in the denominator and fail. Duplicate or unknown IDs abort scoring. Action and offer labels must come from the declared vocabularies in the evaluator. These are evaluation labels, not a promise that the runtime uses this exact action schema. A runtime adapter must map its decision into these labels and retain the original trace for human review; an adapter has not been implemented in this package.

## Candidate input and Gold separation

`candidate_input` uses an explicit field allowlist. It excludes Gold, scenario titles, future messages, target failure tags, inferred hidden intent and private lineage. The export contains a case ID only for joining results; use only its `input` value as candidate scenario content. Load public pricing policy separately as a permitted system fixture. Never send the original Gold file or the evaluator's verdicts to the candidate during generation. The candidate must not have file access to this directory in a real model experiment: allowlist projection prevents accidental prompt inclusion, not filesystem access or training contamination.

The second student message is intentionally withheld until the first response is saved. The current script scores **first-turn labels only**. Continuations and synthetic review events support a subsequent manual multi-turn exercise; they are not executed or scored automatically. Reviewer instructions in Gold must never be presented as student statements. For the feedback-routing and actual-send cases, the first-turn Gold asks only for scope explanation or clarification justified by the visible customer message. Their review/send events and routing/reconciliation requirements apply only in the continuation. A full exercise must supply those events at the correct stage; first-turn labels cannot verify that later behavior. One-off resource consent is likewise required only after the student grants it in the next turn; the first response must not assume it.

## Fixture policy

Package A uses synthetic units: list 1000, floor 900, first counter 950 after at least one actual sales message. Further counters require human review. Package C uses list 800 and floor 700. Products B, D, E and F are not autonomously sellable. These are fictional demonstration figures and are not actual service prices or a pricing recommendation. All custom offers, payment variations and commercial commitments require the relevant human review. Simulated approval events are test data only.

## What has and has not been measured

| Item | Status |
| --- | --- |
| Dataset consistency and English CJK scan | Executed locally |
| Mechanical always-stop baseline | Executed locally; 2/24 combined passes |
| Real V2 model outputs on 24 cases | PENDING |
| Human semantic rubric review | PENDING |
| Second-turn behavior | PENDING |
| Independent reviewer agreement | PENDING |
| Live customer satisfaction, conversion or revenue | NOT MEASURED |

The CJK scan is a useful regression check, not linguistic proof of fluent English. Action-label agreement is deliberately coarse; multiple reasonable actions may exist beyond the current reference labels. Revise disputed Gold only through a documented review, never silently to fit model outputs. Passing all label checks cannot certify the response text, evidence binding, actual runtime tool behavior, correct persistence or safe sending. Runtime policy tests and human semantic review remain separate requirements.
