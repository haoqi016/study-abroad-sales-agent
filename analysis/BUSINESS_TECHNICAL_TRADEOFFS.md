# Business and Technical Trade-offs of the V2 Sales Agent

**Scope.** This paper evaluates a coursework prototype for study-abroad advisers. It helps a human choose and draft a next response; it does not send messages or establish sales conversion. The public browser demonstration uses a fixed synthetic script. Optional model providers exist, but live model quality and real customer outcomes have not been validated. The six-step Gold-assisted flow in the presentation is a design extension; the public runtime's nominal path has tool planning, Decision, and Conversation calls. See the [architecture](../README.md) and [browser guide](../web/README.md).

## The business choice

The product's potential value is to reduce the time an adviser spends interpreting an ambiguous student message while making the proposed next action inspectable. A fluent sales message has little value if it ignores the student's actual obstacle, invents a programme fact, promises an unapproved discount, or contacts someone who has withdrawn permission. V2 therefore separates **what to do** (Decision Agent) from **how to say it** (Conversation Agent), then applies policy checks and human review. This creates accountability but adds process and cost.

The [cost model](COST_ANALYSIS.md) compares three *analytical scenarios*, not three proven products:

| Option | Business advantage | Main cost or risk |
| --- | --- | --- |
| **B0: rules and templates** | No model charge; simple to operate. | Rigid responses can miss combined or unusual student concerns. |
| **B1: one model call** | Lower build and operating burden than V2. | Decision, evidence use, and wording are harder to inspect separately. |
| **B2: staged V2** | Explicit objective, evidence lineage, bounded wording, and review. | More calls, latency, maintenance, and possible handoffs. |

All dollar and time figures are **author-defined assumptions**, not invoices or adviser measurements. At 200 enquiries per month and a loaded labour rate of $20/hour, the model estimates monthly totals of **$600 for B0, $542.26 for B1, and $594.20 for B2**. B2 assumes 393 human seconds per enquiry versus 427.8 for B1, yet costs **$51.94 more per month** at this volume because of higher fixed and amortized build costs. Under unchanged assumptions, B2 breaks even with B1 at roughly **473 enquiries per month**. At 50 enquiries and $10/hour, B2 is much more expensive; at 1,000 and $40/hour, its assumed labour saving outweighs its overhead. The model therefore does not justify “more agents” by itself. It asks whether real review time, error reduction, and acceptable actions compensate for the added stages. The nominal B2 estimate prices three model calls, not extra Gold-selection calls, human-requested revisions, or a possible Decision repair.

## Technical trade-offs

**Separation versus latency.** The Decision Agent writes an objective, next action, evidence IDs, and a content contract. The Conversation Agent turns that contract into a draft without changing material commitments. This division makes a bad strategy easier to distinguish from bad wording, but each provider call can add delay or fail. A bounded repair can add another Decision call. The default offline walkthrough proves the [event and Gate wiring](../sales_agent/v2_pipeline/orchestrator.py), not live inference speed or judgment quality.

**Retrieval versus source governance.** Four read-only tool interfaces cover sales methods, approved advantages, anonymized cases, and programme facts. Retrieval can help answer a specific question, but an unavailable, stale, or unapproved result must not become a customer claim. The public bridge has no approved live Cases or programme source by default, so the recorded browser walkthrough makes zero tool calls. Supplying reliable sources would add approval, freshness checks, provenance work, and possible licensing cost. The [tool and bridge code](../sales_agent/v2_tools/) documents these boundaries.

**Context versus privacy and omission.** V2 projects recent customer-visible messages, selected source-linked older quotes, verified profile fields, and working memory. It keeps student statements distinct from agent hypotheses and unsent drafts. A bounded context reduces token use and accidental disclosure, but a relevant older detail can be omitted or misunderstood; the source record must remain available for review. The [context projection](../sales_agent/v2_pipeline/context_projection.py) is a design control, not proof that every decision has enough context.

**Controls versus friction.** Deterministic Gates, offer approval, contact permission, and separate actual-send records reduce the chance of unauthorized commitments. They also require human time and can stop a potentially useful response. This is appropriate for the current assistant role: the model proposes; an adviser decides what, if anything, to send. Real deployment would additionally need authentication, protected storage, approved data sources, and operational monitoring.

## Evidence and recommendation

The public [evaluation package](../evals/README.md) has 24 English synthetic cases: 16 development scenarios and 8 invented challenges. Its executed scripted first-turn probe created 22 unsent drafts, with one unknown-permission handoff and one do-not-contact block. Four cases lack equivalent runtime evidence fixtures; among the remaining 18, the canned action label matches 5. No case has independent human semantic review, and the two-case local model diagnostic produced two provider handoffs before a decision. These results test wiring and reveal gaps; they cannot establish model quality or revenue impact.

I would keep V2 as a **human-reviewed prototype**. Next, compare B1 and B2 on a fresh sealed case set with the same inputs and budget. Have independent advisers score action usefulness, evidence support, forbidden claims, and editing time; log attempted-call tokens, failures, p50/p95 latency, and review minutes. Replace the assumed cost inputs with invoices and observed work. Expand the staged design only if it improves reviewer-accepted actions and safety enough to justify its measured cost and delay. Any claim about conversion requires later, consented real-workflow evidence rather than synthetic Gold or a polished video.
