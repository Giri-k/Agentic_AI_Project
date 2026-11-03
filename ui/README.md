# OP-US support console

The local React console uses FastAPI and `RealAdapter` to run the project's agent and single-pass baseline. Model calls and embeddings use Portkey. Orders, refunds, approvals, and messages remain fictional records in the local working store; no money is moved and no email is delivered.

The application has one execution mode. `MockAdapter`, generated benchmark figures, forced mock scenarios, and the `OPUS_ADAPTER` switch have been removed. Unit tests still inject model and HTTP responses so they can exercise real execution code without an API key.

## Start locally

Requirements: Python 3.12+, `uv`, and Node.js 20.19+ or 22.12+ with npm.

From the repository root, install dependencies and configure `PORTKEY_API_KEY` in the root `.env` file. Keep the key server-side and never commit it. Reading tickets and saved results does not call the model; starting a run requires the key.

```bash
uv sync --dev
uv run uvicorn ui.api.main:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal:

```bash
cd ui/web
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Vite forwards `/api` to `http://127.0.0.1:8000`; API documentation is at **http://127.0.0.1:8000/docs**. An `ECONNREFUSED 127.0.0.1:8000` proxy error means the Python API is not running. Stop each server with Ctrl+C.

## Workspace behavior

- **Inbox:** search tickets/customers, filter by status/team/priority/channel/split, sort, and paginate.
- **Ticket detail:** review the conversation and owned order records, then run the agent or baseline. Foreign orders remain inaccessible. Results show saved decisions, citations, and actual tool receipts.
- **Approvals:** activate a staged staff approval for the exact order and amount, then resume when its requirement and remaining budget allow continuation. Activation itself does not issue a refund.
- **Trace:** inspect model steps, evidence, tool arguments/results, refusals, retries, receipts, and live lifetime counters. The expandable trace opens during a run; a full trace page is also available.
- **Demo lab:** links select real tickets with existing policy, security, or injected-failure cases. The model chooses its actions; the console applies no forced model trajectory.
- **Grader view:** optional expected-versus-recorded structural checks. Labels are fetched only after opting in and are never passed to the agent.
- **Evaluation:** read saved measured runs and retrieval measurements. Viewing results launches no new experiment.

Only one background run executes at a time. Competing runs, staff approval changes, and Reset are blocked while it is active. A ticket cannot be rerun from the same working snapshot. Customer-input pauses stay blocked because this console has no customer-reply editor.

**Reset deletes local execution history.** The top-bar confirmation restores the dataset's working store and clears local traces, checkpoints, outbox messages, and execution progress. Source data, policies, evaluation artifacts, and the embedding cache are retained. Restarting the API preserves the working store.

## Execution and recovery

The allowances are **8 model calls and 8 tool calls per ticket**, counted across resumed invocations. The dispatcher also enforces one temporary-error retry, one schema repair, one draft revision, and the third-identical-model-call stop rule. Framework-required ticket rereads still consume tool budget but do not count as repeated model requests.

Execution progress is saved in `runtime/store.json` together with action receipts: system, run/mode, cumulative budget, interruption state, and the agent checkpoint where applicable. The separate checkpoint file is a compatibility copy; the atomic store record is authoritative. The UI reads those durable counters during work and after an API restart.

Resume re-reads records and preserves spent calls. A paused refund needs an active unused approval matching its exact owned order and known amount. An interrupted run whose refund already committed can recover from that ticket's saved refund receipt when enough budget remains to finish. Unknown amounts or ambiguous orders require staff review. Older baseline runs without persisted lifetime budgets are blocked instead of receiving a fresh allowance.

The baseline makes one retrieval and one model call per invocation, without an adaptive loop or reply evaluator. It uses the same Python permissions and lifetime budget. A resumed baseline may therefore stop safely if the remaining allowance cannot cover its retrieval and required actions.

## Measured evaluation

The console reads `results/eval/*.json` on each request. It lists only runs marked `measured: true` and displays no invented substitute when results are absent. Retrieval measurements come from `retrieval.json`; absent measurements are shown as not measured.

Generate or finish measurements from the repository root when intentionally running an experiment:

```bash
uv run python -m agent.eval_run --split dev --tag v2
uv run python -m agent.eval_run --split test --tag v2
uv run python -m agent.eval_retrieval --split test --save
```

These commands use the course API. Evaluation code owns artifacts, fingerprints, scoring, and continuation rules; see the root README for current options.

The primary comparison requires matching split, benchmark, policy snapshot, model, and recorded reasoning effort. Matching run versions are preferred when several counterparts are available. Both run IDs and configurations are displayed. Runs whose settings were not recorded remain visibly unrecorded and are not automatically paired with recorded configurations. The all-runs table retains each column's settings for examining experiments.

Metric meanings remain distinct:

- **Structural success** is the deterministic status/category/team/action/citation and safety check against labels and saved execution evidence.
- **Structural unsupported flags** identify tickets failing deterministic citation/card/receipt/amount checks.
- **Semantic unsupported replies** are flagged by the separate LLM judge. The headline shows the semantic count and successful-judgment denominator, plus contradictions, structural flags, and judge errors. Missing judge measurements remain missing.
- Judge counts concern ticket replies; multiple saved messages for one ticket are reviewed together. Judge errors and incomplete coverage matter when interpreting results.
- Unauthorized refund attempts and executions, unsupported replies, and correct pauses remain separate metrics. Tokens and latency display only supplied measurements.

Per-ticket links open the current interactive workspace and its trace. Historical benchmark traces remain with the evaluation files; the links do not claim to open those executions.

## Adapter and API contract

```text
React console -> /api routes -> ui/api/adapter.py -> RealAdapter
                                                    |-- agent and baseline
                                                    |-- store and staff approvals
                                                    |-- JSONL traces
                                                    `-- measured evaluation artifacts
```

`ui/api/adapter.py` defines the interface. `/api/health` supplies real mode, simulation date, capabilities, global running state, active ticket, and limits. Ticket detail includes counters and server-owned reasons why Run or Resume is unavailable. Currency crosses the API as integer cents; an unknown refund amount is null.

Main endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Mode, capabilities, active run, and limits |
| GET | `/api/tickets` and `/api/tickets/{id}` | Inbox summaries and owned records |
| POST | `/api/tickets/{id}/run` | Start `{system: "agent"\|"baseline"}`; return a run ID |
| POST | `/api/tickets/{id}/resume` | Continue an eligible saved run |
| GET | `/api/tickets/{id}/trace?since=0` | Events after an event-count cursor |
| GET | `/api/tickets/{id}/expected` | Opt-in labels and structural grading |
| GET | `/api/approvals` | Waiting requirements and staged/active approvals |
| POST | `/api/approvals/{id}/approve` | Idempotent staff activation |
| GET | `/api/policies` and `/api/policies/{id}` | Policy metadata and sections |
| GET | `/api/eval/runs` and `/api/eval/runs/{id}` | Saved measured evaluation runs |
| GET | `/api/eval/retrieval` | Saved retrieval measurement |
| POST | `/api/admin/reset` | Confirmed restoration of the working store |

Unknown IDs return 404; invalid filters or bodies return 422; competing runs and reruns return 409; unmet resume requirements return 400. Local Vite origins on port 5173 are allowed by CORS.

## Verify

These checks use isolated stores and injected model responses; no course API key is needed:

```bash
.venv/bin/python -m pytest tests ui/api_tests -q
cd ui/web
npm test
npm run build
```

Coverage includes customer isolation, exact approval matching, interrupted-refund recovery, cumulative counters, run locking, receipt preservation, optional grading, compatible evaluation selection, semantic-versus-structural metrics, mobile keyboard navigation, and pending-dialog focus. Fonts and icons are bundled locally.
