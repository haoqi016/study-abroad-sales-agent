# V2 English evaluation package

This is a public, **synthetic** coursework evaluation package. It includes 16 English development scenarios based on the authors' original scenario concepts and 8 newly invented challenge scenarios. All dialogue, amounts, case evidence and review events here are fictional public fixtures. The original development concepts cover pricing, intent, evidence and conversation flow. They have been rewritten rather than released as private transcripts. No independent annotation, owner approval or real customer outcome is claimed.

The challenge set is public and was written by the same implementation team. It is **not an independent holdout**. Both sets can diagnose errors but cannot support an unbiased generalization estimate. Publishing Gold also makes future contamination likely. For a future benchmark, a separate reviewer should author and seal fresh cases before freezing the candidate system.

## Files and reproduction

- `goldens/public_v2_english.json`: 24 cases, English reference behavior, forbidden behavior and second-turn expectations.
- `evaluate.py`: dataset validation, allowlisted candidate input export and mechanical label evaluation.
- `run_v2_first_turn.py`: an event-backed first-turn run through the actual offline workspace and V2 policy gates, using a fixed scripted provider.
- `results/scripted_baseline.json`: actual locally executed always-stop baseline, with all 24 individual verdicts.
- `results/v2_first_turn_scripted.json`: per-case runtime statuses, event lineage, gate decisions and fixture gaps. Generated event IDs and timings differ each run.
- `run_v2_local_diagnostic.py` and `results/v2_local_two_case_diagnostic.json`: a fixed, two-case local Ollama diagnostic and its executed failure-inclusive report.
- `RUBRIC.md`: human review instructions, severity, baselines and reporting protocol.
- `../tests/test_public_eval.py`: regression tests for leakage exclusion, missing cases, permission checks and malformed predictions.

Run from the repository root with Python 3.10 or newer; no API key or external package is needed:

```bash
python -m evals.evaluate
python -m unittest discover -s tests -p 'test_public_eval.py'
python -m evals.evaluate --export-inputs /tmp/public_v2_inputs.json
python -m evals.run_v2_first_turn
python -m unittest tests.test_runtime_eval
```

The default command evaluates a scripted candidate that always returns STOP, NONE and a human-review flag. This is a deliberately unhelpful abstention baseline. It does not call the V2 runtime or a language model. It agrees with the expected action on **2/24** cases (development **0/16**, challenge **2/8**); all mechanical checks pass on **2/24**. Its permission labels pass on 24/24, illustrating why a single safety-looking score cannot measure usefulness. These results are repeatable label checks, not a measured model-quality result.

## Executed V2 first-turn wiring probe

`python -m evals.run_v2_first_turn` runs all 24 cases as independent synthetic students. It records an inbound message, calls `OfflineWorkspaceApi.requestDecision`, and reads the resulting V2 decision, pre- and post-conversation gates, draft and intervention events. The `DeterministicOfflineProvider` is given the same fixed one-question script for every allowed case. It does not read Gold or later events when generating. This probes event persistence, policy gates and permission boundaries, **not model quality**.

The current report contains 24 attempts: 22 `REVIEW_REQUIRED` turns with unsent drafts, one `CONTACT_PERMISSION_UNKNOWN` handoff before a decision, and one `DO_NOT_CONTACT` adapter block before a decision. All 22 recorded decisions have pre- and post-conversation gate results. No human approval or external send is simulated. The permission outcomes are reported separately; neither is silently counted as a candidate action label.

Four cases include public evidence that this runner does not inject into `ToolPorts`. They remain in the 24-attempt denominator and carry `PUBLIC_EVIDENCE_NOT_INJECTED_INTO_RUNTIME_TOOLS`; their action-label comparison is pending because the input is not equivalent. The remaining 18 have a faithful fixed `CLARIFY` label from the canned strategy, and 5/18 match the current public Gold action labels. Human semantic review has been completed for **0/24** cases. This weak canned strategy is a diagnostic contrast, not the V2 model's score. Gate passes do not imply the question is helpful or that Gold requirements are met. All 24 continuations remain unexecuted.

Each row contains a stable hash of its allowlisted candidate input, the generated inbound/context/decision/draft/gate event IDs when present, gate subjects and codes, draft status, and actual-send count. The report also records dataset, harness and pricing-policy versions. The event IDs and measured local wall times are newly generated on each run; the case statuses, fixture gaps and aggregate counts are the repeatable result. A current checkout with edits may produce a different harness hash from this committed report.

Only the known do-not-contact adapter refusal is classified as `ADAPTER_BLOCKED`. An unexpected `requestDecision` exception is `EXECUTION_ERROR` and remains in the attempted denominator. The V2 pipeline may instead return `HANDOFF` with a reason such as `MODEL_PROVIDER_UNAVAILABLE_OR_INVALID`; this is a failed model attempt, never a permission success or Gold match.

## Bounded local model diagnostic

`python -m evals.run_v2_local_diagnostic` fixes the two-case sample to `DEV-OFFER-01` and `DEV-INTENT-01`, the local `qwen3.5:9b` model digest, and a 90-second limit per provider call. These cases have allowed contact and no supplied evidence fixture. The runner reads only `candidate_input` and the normal V2 policy; Gold and continuations are not supplied to the provider. It makes one pipeline attempt per case and preserves both failures in the denominator.

In the recorded run, **2/2 attempts ended `HANDOFF` with `MODEL_PROVIDER_UNAVAILABLE_OR_INVALID` before a decision**. The first took 124.9 seconds across its provider calls and the second 90.0 seconds. Both persisted inbound and context events; neither produced a decision, gate or draft. There was no selective retry. The report records model digest, prompt version, timeout, case input hashes, event lineage and per-case latency. This establishes only the observed local failure mode, not a V2 quality score. The 24-case model run, semantic review and business outcomes remain pending.

The local diagnostic retains the harness and whole-dataset hashes from its original attempt. The scripted harness was subsequently tightened to distinguish unexpected request errors from permission blocks, and Gold annotations were revised, so those whole-file hashes differ from the current files. The allowlisted input hashes for both selected cases still match the current dataset. The two recorded local `HANDOFF` statuses came from pipeline results and remain compatible with that classification change; no Ollama attempt was repeated.

For a candidate, provide a JSON array containing one object per case:

```json
[
  {"case_id": "DEV-OFFER-01", "action": "CLARIFY", "offer_state": "NONE", "human_approval_required": true}
]
```

```bash
python -m evals.evaluate --predictions /tmp/predictions.json --candidate candidate-v1 --output /tmp/candidate-report.json
```

Omitted cases remain in the denominator and fail. Duplicate or unknown IDs abort scoring. Action and offer labels must come from the declared vocabularies in the evaluator. These are evaluation labels, not a promise that the runtime uses this exact action schema. The runtime probe maps only its own fixed `CLARIFY` strategy; it does not claim a general mapping for model-generated V2 strategies.

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
| Scripted V2 first-turn wiring | Executed locally; 24 attempts, 22 drafts, 2 permission stops/handoffs, 4 evidence fixture gaps |
| Local model diagnostic | Executed on 2 selected cases; 0/2 decisions due provider failure |
| Real V2 model outputs on 24 cases | PENDING |
| Human semantic rubric review | PENDING; 0/24 reviewed |
| Second-turn behavior | PENDING |
| Independent reviewer agreement | PENDING |
| Live customer satisfaction, conversion or revenue | NOT MEASURED |

The CJK scan is a useful regression check, not linguistic proof of fluent English. Action-label agreement is deliberately coarse; multiple reasonable actions may exist beyond the current reference labels. Revise disputed Gold only through a documented review, never silently to fit model outputs. Passing all label checks cannot certify the response text, evidence binding, actual runtime tool behavior, correct persistence or safe sending. Runtime policy tests and human semantic review remain separate requirements.
