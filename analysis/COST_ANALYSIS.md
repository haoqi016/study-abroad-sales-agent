# V2 cost and business trade-off analysis

**Evidence status: SCENARIO ONLY.** This appendix models one synthetic price-objection enquiry. Every price, token count, call path, volume, labour second, hourly rate, fixed cost, build hour, and accepted-action rate below is an **ASSUMED** coursework input. The repository has no provider invoices, adviser time study, deployment bill, or real customer outcome data. The numbers illustrate a decision method; they do not establish savings or return on investment.

## Decision to make

The proposed user is an adviser who must decide what to do after a student raises price or service-scope concerns. A reply that sounds natural is insufficient when the assistant could offer an unapproved discount, claim unsupported programme facts, continue after a stop request, or treat a draft as sent. The V2 design uses a Decision stage, optional read-only evidence tools, a Conversation stage, deterministic Gates, and human approval before any external message. The public offline workspace records a manually asserted sent message but has no outbound sender. See the [root architecture](../README.md) and [offline adapter](../sales_agent/v2_offline_api/README.md).

| Option | What the scenario assumes | Main trade-off |
| --- | --- | --- |
| **B0** | Rule/template baseline, zero model calls, same deterministic policy and human-review boundary | Lowest inference cost; brittle when intent is ambiguous. This is an **analytical comparator**, not a selectable public runtime mode. |
| **B1** | One structured model call for decision and wording, then the same Gates and human review | Simpler and cheaper than multi-step V2; may have less opportunity to inspect evidence separately. This is also an **analytical comparator**. |
| **B2** | One nominal V2 path: tool-plan call, decision call, conversation call, Gates, human review | More inspectable stages and potential evidence use; extra latency, call cost, failure paths, and operating work. A turn can stop early or require additional calls. |

The build choice is to own the decision contracts, evidence adapters, permission and price Gates, event lineage, and review flow. Model inference is an optional replaceable provider. The default public demo uses a deterministic script; the local Ollama option is an experiment, not a measured service. The read-only programme and case tools need approved sources before their content can support real claims. There is no production deployment estimate here.

The B2 scenario's **three model calls are a nominal input, not a maximum**. The tool plan can select up to three read-only *evidence tools*, which is a different count. Early handoff may use fewer model calls. Human-requested revisions and provider retries can use more. On a strict live-model path, when the first Decision is incomplete, one bounded internal repair can add one Decision call. The token and dollar totals below do **not** price additional calls or their latency. Measure every attempted call, including failures, before replacing this scenario with a real cost estimate.

## Reproducible cost model

The [versioned input](cost_scenarios.json) lists each assumption and its provenance label. The [calculator](cost_model.py) validates the labels and computes the tables. Run from the repository root:

```sh
python3 analysis/cost_model.py
python3 -m unittest analysis.test_cost_model -v
```

For each arm, let `I` and `O` be assumed input/output tokens per enquiry, and `pI` and `pO` be assumed USD per million tokens. Let `t` be routine review + correction + escalation seconds, `q` the chance of extra rework, `r` its seconds, `w` loaded human USD/hour, `N` monthly enquiries, `F` monthly fixed cost, `H` build hours, and `M` amortization months:

```text
model cost/enquiry       = (I × pI + O × pO) / 1,000,000
expected human seconds  = t + q × r
monthly model cost      = N × model cost/enquiry
monthly human cost      = N × expected human seconds × w / 3,600
monthly scenario total  = monthly model + monthly human + F + H × w / M
cost/accepted action    = monthly scenario total / (N × assumed acceptance rate)
```

The acceptance rate means a hypothetical future adviser judgment that the **selected next action** was acceptable. It is not a lead conversion or payment rate. The calculator applies the same loaded hourly rate to build hours as a simplifying assumption; actual development labour could differ. Fixed costs are arm-specific placeholders for hosting, maintenance and monitoring, not vendor quotes. Excluded items include data licensing, privacy/security work, CRM integration, tool fees, incident handling, taxes, retries beyond the assumed rework term, customer-channel cost, and any revenue effects.

### Assumed per-enquiry inputs

| Input | B0 | B1 | B2 |
| --- | ---: | ---: | ---: |
| Model calls on nominal modeled path | 0 | 1 | 3 |
| Input / output tokens on that path | 0 / 0 | 1,200 / 350 | 4,000 / 1,170 |
| Model cost at **assumed** $0.50 input and $2.00 output per million | $0 | $0.00130 | $0.00434 |
| Routine review + correction + escalation seconds | 450 | 375 | 345 |
| Extra rework probability × 240 seconds | 25% | 22% | 20% |
| Expected total human seconds | 510 | 427.8 | 393 |
| Arm fixed USD/month | $20 | $40 | $90 |
| Build hours, amortized over 12 months | 8 | 16 | 40 |
| **Assumed** acceptable-action probability | 65% | 70% | 76% |

The assumed token rates are calculation inputs, **not current OpenAI, Ollama, or OpenRouter prices**. A live costing exercise must record provider, model/version, billing date, charged input/output/cache/tool tokens, retries, and currency conversion against an actual invoice. The public provider adapter currently returns a JSON object but does not provide a billing ledger or measured usage in this repository.

The bounded recent-history projection may change actual input-token use by turn: it keeps at most 16 recent customer-visible messages within an approximate 3,000-character budget, while keeping the latest message exact and adding a small source-linked index for older history. Characters are not model tokens, and the commercial decision view and optional wording cue add their own input. The assumed 4,000 B2 input tokens are therefore a scenario value, not a measurement or guaranteed prompt limit.

### Volume and labour sensitivity

All figures are USD/month and combine the *assumed* model, human, fixed, and amortized build components. They are scenario outputs, not actual spend.

| Scenario (enquiries/month; loaded USD/hour) | B0 | B1 | B2 | B2 − B1 | B2 extra seconds/enquiry to save to match B1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Low (50; $10) | $97.50 | $112.82 | $178.13 | +$65.32 | 470.29 |
| Base (200; $20) | $600.00 | $542.26 | $594.20 | +$51.94 | 46.75 |
| High (1,000; $40) | $5,713.33 | $4,847.97 | $4,594.34 | −$253.63 | 0 |

At the **base assumptions**, B2's extra fixed and amortized build cost is $90/month versus B1, while its modeled variable cost is about $0.19029 lower per enquiry. Algebra gives a break-even volume of about **473 enquiries/month** at $20/hour, holding every other assumption fixed. At 200 enquiries, B2 would have to save another **46.75 human seconds per enquiry** to tie B1 on total scenario cost. No such saving has been observed. Changing only the assumed B2 rework probability from 20% to 30% raises the base B2–B1 gap from $51.94 to **$78.61/month**; adding one B2 review minute per enquiry raises it to **$118.61/month**. Doubling both arms' assumed model rates moves the base gap to **$52.55/month**, which illustrates that human and fixed costs dominate this particular assumed input set. These stress results are generated by the calculator.

The modeled base cost per assumed acceptable action is $4.62 (B0), $3.87 (B1), and $3.91 (B2). Because all acceptance rates are invented, this comparison cannot rank real strategy quality. It shows which human labels must be collected before a cost-effectiveness claim is possible.

## Evidence plan and decision gate

1. Freeze a comparable, synthetic evaluation set and the B0/B1/B2 candidate outputs; have reviewers score the next action blind to arm. Report severe policy escapes separately from style and report uncertainty by case, not just a single aggregate. The public [evaluation set](../evals/README.md) is a development fixture, not an independent business holdout.
2. Time human review, correction, approval, handoff, and actual sending separately. Count API use on every attempted turn, including Gate failures and handoffs; log provider and token charges without personal data.
3. Replace assumed volume, labour, fixed, build and acceptance inputs with approved measurements. Recalculate the sensitivity table. A higher model call count is justified only if reviewer-accepted actions and safety improve enough to offset cost and delay.
4. Keep V2 experimental until an independent reviewer, approved factual sources, real workflow timings and privacy/security controls support a deployment decision. Synthetic tests verify contracts and reproducibility, not sales conversion.

## Source and provenance map

- **Implemented public path:** [`orchestrator.py`](../sales_agent/v2_pipeline/orchestrator.py) contains the `tool_plan`, `decision`, and `conversation` provider boundaries and the Gate flow; [`context_projection.py`](../sales_agent/v2_pipeline/context_projection.py) separates bounded customer-visible history, recorded commercial events and unverified prior judgment; [`providers.py`](../sales_agent/v2_pipeline/providers.py) defines scripted and opt-in model providers; [`adapter.py`](../sales_agent/v2_offline_api/adapter.py) and [`bridge_server.py`](../web/bridge_server.py) show local workspace behavior. These source files support architecture claims, **not the assumed numerical inputs**.
- **Scenario provenance:** [`cost_scenarios.json`](cost_scenarios.json) is the sole source for all numeric inputs. [`cost_model.py`](cost_model.py) implements the printed equations; [`test_cost_model.py`](test_cost_model.py) checks reconciliation, status labeling, and sensitivity direction. No private cost ledger, real price policy, customer dataset, or business metric is included.
- **For later replacement:** use the selected provider's dated billing record and official pricing page, adviser time observations, hosting invoices, and blinded review results. Avoid treating any one current price page as evidence of a charge in this offline demo.
