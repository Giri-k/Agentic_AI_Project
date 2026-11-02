# OP-US support console

This is the interactive mock UI milestone: a local React console backed by a FastAPI mock adapter. It displays the existing tickets, customer-owned records and policies, then simulates execution without calling a model or changing source fixtures.

The inbox supports search and filters. Ticket details show the conversation, customer, payments, active approvals, refunds and remaining refundable balance. Foreign orders are marked inaccessible. Policy links open the policy text.

Run agent and Run baseline start background scripted runs. The live trace shows model decisions, policy evidence, tool results, errors and receipts. Staff can explicitly activate a staged approval and resume an eligible ticket. The Demo lab, optional grader and mock evaluation dashboard are implemented. UI cleanup is complete; real-agent integration and presentation rehearsal remain.

The sidebar has one entry per workflow. **Reset** is available in the top bar on every page, with confirmation and a running-ticket lock. Inbox status has one filter. Ticket details prioritize the result and customer/order records; the execution trace starts collapsed, opens during a run, and remains available afterward. Approvals initially show records matching waiting tickets, with an option to show every staged record. Evaluation keeps the safety comparison visible and places citation/cost metrics, breakdowns and retrieval details in expandable sections.

## Recovery and safety demonstrations

Open **Demo lab** (`/demos`), select a scenario, then click **Run agent** on its ticket. Links select a scenario without starting work or resetting history. A previously processed ticket needs a workspace reset before replay.

| Ticket | Scenario | What to inspect |
| --- | --- | --- |
| T-0052 | `timeout_retry` | The first message write succeeds but its reply times out; one retry returns the saved receipt. Only one outbox message exists. |
| T-0029 | `repeated_call` | Two identical reads execute; the third request is stopped before execution and the ticket becomes failed-safe. |
| T-0055 | `refund_gate` | A deliberately forced request with APR-9999 is rejected with NO_MATCHING_APPROVAL. No refund or balance change occurs. |
| T-0002 | `standard` | Scripted embedding outage with BM25 fallback and a visible reason. |

Repeated-call and refund-gate cases are explicitly **forced tests**, not ordinary correct-agent trajectories or accuracy results. Standard mode on T-0055 never attempts its forbidden refund. Forced scenarios are restricted to their named tickets and the mock agent; baseline uses standard mode.

## Optional grader

The **Grader view** checkbox on ticket detail is off by default. Expected answers are fetched only after opting in. The comparison shows draft expected status, category, team, policy IDs, actions, forbidden actions and required tool arguments against saved results and trace evidence.

“Checked fields match” describes structural fields only. Reply meaning and factual grounding are **Not verified** and require human/evaluator review. Mock trajectories use the same draft labels, so these comparisons are not independent evidence of AI accuracy. Forced tests are informational. After resume, the grader uses the after-resume annotation and the resumed invocation; it does not incorrectly forbid the approved refund using the initial pause's rules.

## Evaluation dashboard

Open **Evaluation** (`/eval`). Choose agent/baseline, dev/test and a prepared snapshot. The page includes final-status accuracy, unauthorized refunds attempted and executed separately, unsupported replies, correct pauses with denominators, citation precision/recall, matched agent/baseline comparisons, scenario/end-state breakdowns and searchable per-ticket outcomes. Retrieval tables display Hit@3, Recall@3, Precision@3 and MRR.

All current dashboard figures are marked **MOCK DATA**, with `measured: false` and explicit provenance. They are deterministic illustrations derived from synthetic observations, not measured model performance. Costs that are not measured remain null and display “Not measured.” Both systems use identical split membership and policy snapshots. Interactive forced tests do not alter these immutable benchmark examples. Ticket/trace links open the current interactive workspace, not a historical trace for an illustrative benchmark row.

## Try the approval demo

1. Use **Reset** in the top bar for a fresh in-memory snapshot.
2. Open **T-0039** and click **Run agent**.
3. Follow the live trace until the result is **awaiting approval**.
4. Open **Approvals**, locate **APR-8013** for **O-60048 / $1,149.00**, and confirm activation.
5. Click **Resume ticket** in the waiting card, or return to the ticket and click **Resume**.
6. After completion, inspect the result's policy citations and refund receipt. The ticket is **resolved**, the order is **refunded**, and remaining refundable is **$0.00**.

Approving twice is harmless. Repeated resume or run requests cannot issue a second refund. Only one run can execute at a time; approval and reset are blocked while it is active. Customer-input and staff-recovery resume paths show their outstanding requirement instead of pretending it is satisfied.

**Budget:** the scripted agent demo needs four tool calls before the pause and four after resume. The backend limits are **8 model calls and 8 tool calls per ticket**, counted across resume, and the mock uses the same limits. A mock configured with a smaller allowance shows a budget notice and stops safely, keeping any existing action receipts.

Run each system from a fresh reset when comparing agent and baseline. A ticket can start only once per snapshot. Baseline uses one retrieval before one scripted model call per invocation and no retries. Resuming its refund example adds a retrieval, so it needs nine lifetime tool calls and stops safely at the default eight-call mock allowance.

## Start locally

Requirements: Python 3.12 or newer, `uv`, and Node.js 20.19+ or 22.12+ with npm. Installation requires network access; the mock console runs offline after dependencies are installed. Model keys are not needed, and the UI does not read `.env`.

From the repository root, start the API:

```bash
uv sync --dev
uv run uvicorn ui.api.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend:

```bash
cd ui/web
npm ci
npm run dev
```

Open **http://localhost:5173**. Vite forwards `/api` requests to `http://127.0.0.1:8000`. API documentation is available at **http://127.0.0.1:8000/docs**. Stop each server with Ctrl+C.

## Adapter boundary

```text
ui/web/ React → /api HTTP routes → ui/api/adapter.py
                                  ├─ MockAdapter: current milestone
                                  └─ RealAdapter: supplied by the agent team later
```

`OPUS_ADAPTER=mock` is the default. `MockAdapter` reads `data/synthetic/` and `policies/` into memory; it never imports agent execution code or writes to `runtime/`. Reset restores its private snapshot only. Restarting the API also restores the snapshot.

The snapshot contains 160 tickets: **149 new, 4 awaiting customer, 4 resolved and 3 awaiting approval**. These saved fixture states are preserved; they are not generated by the UI. The simulation date is the store's `2026-10-20`, rather than the laptop's current date. Money remains integer cents on the API and is formatted as USD in the UI.

The mock adapter privately reads draft labels to script plausible fixture outcomes. Labels are never returned to the ordinary screens or passed to a real agent. Categories stay blank until a mock run saves a result. Tickets omit `failure_injection`, and inactive staged approvals are exposed only as staged staff records on the Approvals screen. Every screen identifies mock mode, and model events identify themselves as scripted.

The `Adapter` protocol in `ui/api/adapter.py` defines the integration boundary. These additions clarify the UI spec for this milestone:

- `metadata()` supplies `/api/health`: mode, simulation date, global running state and active ticket, capability flags, mock limits, backend reference limits and the budget disclosure. The frontend uses capability flags and current execution state to enable supported operations.
- `list_tickets()` accepts optional keyword filters: `status`, `team`, `priority`, `channel`, `split` and `q`. Query matching is case-insensitive across ticket ID, subject and customer name.
- Ticket detail includes `execution`: cumulative counters, limits, run IDs, system, `can_run`, `can_resume` and reasons an action is blocked. Approvals include a structured `pending_refund`; an unknown amount is `null`, never zero.
- Trace history is an append-only event array. `since` is a nonnegative event-count cursor; the current UI fetches complete history and polls once per second while running. Unknown events and error codes remain inspectable.
- `start_run(ticket_id, system, scenario="standard")` accepts an explicit scenario. Ordinary tickets and real-adapter integration can retain standard mode. `get_expected()` is separate from all ordinary ticket/tool inputs. Evaluation getters return read-only snapshot artifacts with provenance.

`create_app(adapter=...)` permits isolated adapter tests. Adapter failures use `AdapterError(status_code, detail)` and become `{"detail": "message"}` responses.

Selecting `OPUS_ADAPTER=real` returns HTTP **503** until `ui/api/real_adapter.py` provides a compatible `RealAdapter`. It never silently substitutes mock data for a requested real mode.

## Available endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Mode, date, capabilities and configured display limits |
| GET | `/api/tickets` | Inbox with optional filters |
| GET | `/api/tickets/{id}` | Public ticket detail with owned order records |
| POST | `/api/tickets/{id}/run` | Start `{system: "agent"\|"baseline", scenario?: "standard"\|"timeout_retry"\|"repeated_call"\|"refund_gate"}`; return 202 with `run_id` |
| POST | `/api/tickets/{id}/resume` | Continue an eligible approval pause; return 202 with `run_id` |
| GET | `/api/tickets/{id}/trace?since=0` | Append-only trace events |
| GET | `/api/tickets/{id}/expected` | Opt-in draft labels and structural comparisons, with phase and limitations |
| GET | `/api/approvals` | Waiting tickets and staged/active approvals |
| POST | `/api/approvals/{id}/approve` | Explicit, idempotent staff activation |
| GET | `/api/policies` | Policy IDs, titles and actual versions |
| GET | `/api/policies/{id}` | Metadata and sections S1–S4 |
| GET | `/api/eval/runs` | Four illustrative agent/baseline × dev/test snapshots |
| GET | `/api/eval/runs/{id}` | Metrics, provenance, breakdowns and per-ticket observations |
| GET | `/api/eval/retrieval` | Explicitly illustrative policy-ranking results |
| POST | `/api/admin/reset` | Restore mock memory and return order/approval counts |

Unknown IDs return 404. Invalid enumerated filters or run bodies return 422. Conflicting runs, reruns and reset/approval during execution return 409; an unsatisfied resume requirement returns 400. CORS accepts the local Vite origins on port 5173.

## Verify

From the repository root:

```bash
.venv/bin/python -m pytest ui/api_tests
.venv/bin/python -m pytest tests ui/api_tests
```

Build the frontend from `ui/web/`:

```bash
npm test
npm run build
```

Component tests cover inbox navigation, filters, pagination, customer isolation,
policy browsing, global reset confirmation, mobile navigation focus, run/approval/resume/result refresh, trace disclosure, matching approval filters, grader opt-in, demo routing, evaluation controls/disclosures, unknown amounts and the large-text preference. API tests cover global execution locking, strict budget stopping, timeout replay, forced gate isolation, phase-aware grading, consistent dashboard denominators, approval/refund idempotence, exact money and source/runtime preservation. Fonts and
icons are bundled locally; the browser does not contact an external asset service.

### Local acceptance check

1. Search and filter the inbox, open a ticket, and review its owned records and policy links.
2. Follow the approval demo above. Verify the pause, exact approval, resumed result and refund receipt.
3. Replay the Demo lab recovery/safety cases using Reset between runs. Expand the trace to inspect errors and receipts.
4. Turn Grader view on, then check Evaluation and expand its diagnostics. Current results must stay labeled mock.
5. On a narrow window, open/close navigation and use Escape; check that controls wrap and tables scroll inside their panels. Test Large text on desktop.

## Next integration work

The UI is implemented through the evaluation page and polished against `MockAdapter`. The next development step is `RealAdapter` integration, followed by testing against actual agent output and a presentation rehearsal. Limits and cumulative progress are supplied by adapter metadata/events rather than inferred from visible rows.

The agent team owns real execution, approvals, checkpoints and `RealAdapter`. UI work stays in `ui/`; coordinate changes to the shared `pyproject.toml` and `uv.lock`. Keep expected labels separate from real agent inputs and identify any future fabricated evaluation results as mock data.
