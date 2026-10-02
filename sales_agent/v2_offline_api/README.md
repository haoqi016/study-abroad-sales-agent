# Offline V2 Sales Workspace adapter

`OfflineWorkspaceApi` is a local Python counterpart to the method names in
`web/api.js`. By default it uses an in-memory `V2RuntimeStore` and a
`V2SalesPipeline` with scripted `DeterministicOfflineProvider` responses.
The adapter itself opens no socket, calls no model endpoint, and has no
message sender.

Implemented method mappings: `listStudents`, `createStudent`, `updateStudent`,
`getWorkspace`, `recordInbound`, `discussInternal`, `requestDecision`,
`reviewDraft`, `approveDraft`, `approveCustomOffer`, `recordActualSent`,
`recordCommercialOutcome`, and `listReminders`. `queue_script` supplies one synthetic provider response per
decision or revision. `requestDecision` returns both the pipeline result and
the current workspace view. The shape is similar to the JS demo API but is not
a drop-in browser adapter.

All input objects used for records and scripts require `demo_only: true`.
Student names must visibly say `DEMO` (the code also accepts a legacy synthetic marker); contact and identity fields are
rejected. These checks cannot establish that arbitrary pasted message text is
synthetic, so do not use real student data. Nothing is persisted after the
Python object closes by default. There is no user authentication, CRM
integration, push service, or production authorization. The optional loopback
bridge in `web/bridge_server.py` is local only; it is not cloud authentication.

To retain synthetic exercise data across local server restarts, explicitly
choose a new SQLite file:

```sh
python web/bridge_server.py --sqlite-file /absolute/path/to/synthetic-workspace.sqlite3
```

The file's parent directory must exist. Reuse that same path on the next run.
The adapter marks new files as synthetic workspace databases and refuses
existing unmarked SQLite files. A malformed event history aborts startup;
it is never reset or silently skipped. The student list is rebuilt from the
immutable `STUDENT_SNAPSHOT` events, and drafts, reviews, sent records, and
commercial outcomes remain in the same V2 event store. Script queues are
ephemeral and must be supplied again after restart. The marker and the
`demo_only` check cannot prove that pasted free text is synthetic: only use
fabricated student details and messages. Protect and remove the local file
according to your own data handling needs.

Only the pipeline creates Gate results. A pending custom offer creates no
customer draft; a separate `approveCustomOffer` event is required, followed
by a new decision. Drafts require review and approval. `recordActualSent`
requires the manually supplied original external text plus
`confirmed_external_send: true`; it only records that assertion locally and
never performs a send.
`recordCommercialOutcome` records a synthetic operator assertion as
`SELF_REPORTED`; it never verifies a payment or changes any real transaction.

## How a synthetic turn moves through V2

1. `createStudent` creates a visibly marked demo record; `recordInbound` stores the student's raw message as an event. The adapter also records explicit reply signals and stop-contact state where applicable.
2. `requestDecision` builds context from the current event history. A scripted provider is used by default; an explicitly configured local model is an experiment. The pipeline may request read-only evidence, then runs the Decision stage and a pre-conversation Gate.
3. If permitted, the Conversation stage produces a draft and a post-conversation Gate checks it. `REVIEW_REQUIRED` means there is an unsent draft for a human to inspect. `APPROVAL_REQUIRED` means a custom offer needs separate human approval before a new decision. `HANDOFF`, `REVISE_REQUIRED`, and `STOPPED` are not successful sends.
4. `reviewDraft` records feedback; `approveDraft` approves a particular draft. `recordActualSent` records a human-supplied external message and compares it with the approved draft. Neither the adapter nor browser sends it.
5. On a later inbound, V2 can assess the previous objective and use the recorded customer-visible history. `recordCommercialOutcome` remains a synthetic self-report, not a verified sale.

The [English evaluation fixtures and protocol](../../evals/README.md) assess local development cases and identify what still needs blinded adviser review. The [cost appendix](../../analysis/COST_ANALYSIS.md) compares explicitly assumed B0/B1/B2 scenarios; no measured cost or business improvement is implied.

Run focused verification with:

```sh
python3 -m unittest web.tests.test_bridge_server -v
node --test web/tests/*.test.js
```
