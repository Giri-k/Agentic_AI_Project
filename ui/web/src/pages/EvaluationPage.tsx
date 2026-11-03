import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Activity,
  ArrowDownUp,
  ArrowUpRight,
  BookOpenText,
  ChartNoAxesCombined,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  FlaskConical,
  Info,
  LoaderCircle,
  Search,
  ShieldCheck,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEvalRun, useEvalRuns, useRetrievalMetrics } from "../api";
import { EmptyState, StatusBadge } from "../components";
import { date, humanize } from "../format";
import type { EvalRun, EvalRunSummary } from "../types";

// Missing measurements remain visibly missing rather than becoming zeros or fabricated cost estimates.
function numeric(value: number | null | undefined, digits = 0): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      })
    : "Not measured";
}

// A ratio's display and bar width use the supplied metric, never a new model-quality estimate.
function percentage(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : "Not measured";
}

// Every illustrative section carries its own label, including below the fold.
function DataBadge({ mock }: { mock: boolean }) {
  return mock ? (
    <span className="eval-data-badge">
      <FlaskConical size={11} />
      MOCK DATA
    </span>
  ) : (
    <span className="eval-real-badge">
      <Activity size={11} />
      SUPPLIED RESULTS
    </span>
  );
}

// Ticket membership and policy metadata must agree before presenting a fair side-by-side comparison.
function comparisonIssue(selected: EvalRun, other: EvalRun): string | null {
  if (selected.split !== other.split)
    return "The selected runs use different dataset splits.";
  if (!selected.benchmark_id || !other.benchmark_id)
    return "Benchmark identifiers are missing, so equivalent inputs cannot be confirmed.";
  if (selected.benchmark_id !== other.benchmark_id)
    return "The selected runs use different benchmark snapshots.";
  if (!selected.policy_snapshot || !other.policy_snapshot)
    return "Policy snapshot metadata is missing from one or both runs.";
  if (selected.policy_snapshot !== other.policy_snapshot)
    return "The selected runs use different policy snapshots.";
  const selectedIds = selected.tickets
    .map((ticket) => ticket.ticket_id)
    .sort()
    .join("|");
  const otherIds = other.tickets
    .map((ticket) => ticket.ticket_id)
    .sort()
    .join("|");
  if (selectedIds !== otherIds)
    return "The selected runs do not contain the same tickets.";
  if (selected.mock !== other.mock)
    return "Mock and real results cannot be treated as a measured system comparison.";
  return null;
}

// Comparison tables retain distinct safety counts and nullable execution-cost measurements.
type Measured = Pick<EvalRun, "metrics">;
type MetricRow = {
  label: string;
  value: (run: Measured) => string;
  safety?: boolean;
};

// One list of metrics for both tables, so the agent-versus-baseline view and the all-runs view always agree.
const METRIC_ROWS: MetricRow[] = [
  {
    label: "Overall success",
    value: (run: Measured) => percentage(run.metrics.success_rate),
  },
  {
    label: "Final-status accuracy",
    value: (run: Measured) => percentage(run.metrics.status_accuracy),
  },
  {
    label: "Category accuracy",
    value: (run: Measured) => percentage(run.metrics.category_accuracy),
  },
  {
    label: "Category macro-F1",
    value: (run: Measured) => numeric(run.metrics.category_macro_f1, 3),
  },
  {
    label: "Routing accuracy",
    value: (run: Measured) => percentage(run.metrics.routing_accuracy),
  },
  {
    label: "Expected actions and arguments",
    value: (run: Measured) => percentage(run.metrics.action_accuracy),
  },
  {
    label: "Tool-call precision / recall",
    value: (run: Measured) =>
      `${percentage(run.metrics.tool_call_precision)} / ${percentage(run.metrics.tool_call_recall)}`,
  },
  {
    label: "Tool argument accuracy",
    value: (run: Measured) => percentage(run.metrics.tool_argument_accuracy),
  },
  {
    label: "Tool error rate",
    value: (run: Measured) => percentage(run.metrics.tool_error_rate),
  },
  {
    label: "Judge: expected facts stated",
    value: (run: Measured) => percentage(run.metrics.judge_fact_coverage),
  },
  {
    label: "Judge: replies contradicting a fact",
    value: (run: Measured) => numeric(run.metrics.judge_contradicted_replies),
  },
  {
    label: "Judge: replies with unsupported claims",
    value: (run: Measured) => numeric(run.metrics.judge_unsupported_replies),
  },
  {
    label: "Unauthorized refunds attempted",
    value: (run: Measured) =>
      numeric(run.metrics.unauthorized_refunds_attempted),
  },
  {
    label: "Unauthorized refunds executed",
    value: (run: Measured) =>
      numeric(run.metrics.unauthorized_refunds_executed),
    safety: true,
  },
  {
    label: "Unsupported replies",
    value: (run: Measured) => numeric(run.metrics.unsupported_replies),
  },
  {
    label: "Correct pauses / expected pauses",
    value: (run: Measured) =>
      `${numeric(run.metrics.correct_pauses)} / ${numeric(run.metrics.expected_pauses)}`,
  },
  {
    label: "Citation precision",
    value: (run: Measured) => percentage(run.metrics.citation_precision),
  },
  {
    label: "Citation recall",
    value: (run: Measured) => percentage(run.metrics.citation_recall),
  },
  {
    label: "Average model calls",
    value: (run: Measured) => numeric(run.metrics.avg_model_calls, 1),
  },
  {
    label: "Average tool calls",
    value: (run: Measured) => numeric(run.metrics.avg_tool_calls, 1),
  },
  {
    label: "Average tokens",
    value: (run: Measured) => numeric(run.metrics.avg_tokens),
  },
  {
    label: "Average input / output tokens",
    value: (run: Measured) =>
      `${numeric(run.metrics.avg_tokens_in)} / ${numeric(run.metrics.avg_tokens_out)}`,
  },
  {
    label: "Average seconds per ticket",
    value: (run: Measured) =>
      run.metrics.avg_seconds === null ||
      run.metrics.avg_seconds === undefined
        ? "Not measured"
        : `${numeric(run.metrics.avg_seconds, 1)} s`,
  },
  {
    label: "95th-percentile seconds per ticket",
    value: (run: Measured) =>
      typeof run.metrics.p95_seconds === "number"
        ? `${numeric(run.metrics.p95_seconds, 1)} s`
        : "Not measured",
  },
  {
    label: "Model-call latency (average / 95th percentile)",
    value: (run: Measured) =>
      typeof run.metrics.avg_model_latency_ms === "number"
        ? `${numeric(run.metrics.avg_model_latency_ms / 1000, 1)} s / ${numeric((run.metrics.p95_model_latency_ms ?? 0) / 1000, 1)} s`
        : "Not measured",
  },
];

function ComparisonTable({
  selected,
  counterpart,
}: {
  selected: EvalRun;
  counterpart: EvalRun;
}) {
  const problem = comparisonIssue(selected, counterpart);
  if (problem)
    return (
      <div className="eval-comparison-unavailable">
        <TriangleAlert size={20} />
        <strong>These runs are not comparable yet.</strong>
        <p>{problem}</p>
      </div>
    );
  const agent = selected.system === "agent" ? selected : counterpart;
  const baseline = selected.system === "baseline" ? selected : counterpart;
  const rows = METRIC_ROWS;
  const table = (metrics: typeof rows) => (
    <div className="eval-table-scroll">
      <table className="eval-table eval-comparison-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>
              Adaptive agent
              <DataBadge mock={agent.mock} />
            </th>
            <th>
              Single-pass baseline
              <DataBadge mock={baseline.mock} />
            </th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((row) => (
            <tr key={row.label} className={row.safety ? "eval-safety-row" : ""}>
              <th scope="row">{row.label}</th>
              <td>{row.value(agent)}</td>
              <td>{row.value(baseline)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  return (
    <>
      <div className="eval-comparison-context">
        <ShieldCheck size={13} />
        Same {selected.split} split · {selected.tickets.length} tickets ·
        matching benchmark and policy snapshots
      </div>
      {table(rows.slice(0, 5))}
      <details className="eval-secondary-metrics">
        <summary>Citations and execution costs</summary>
        {table(rows.slice(5))}
      </details>
    </>
  );
}

// Headline cards show raw safety numbers and their denominators without claiming mock proof of safety.
function HeadlineMetrics({ run }: { run: EvalRun }) {
  const metrics = run.metrics;
  return (
    <section
      className="eval-headline-section"
      aria-label="Headline evaluation metrics"
    >
      <div className="eval-section-heading">
        <h2>Outcomes and safety</h2>
        <DataBadge mock={run.mock} />
      </div>
      <div className="eval-headline-grid">
        <div className="eval-metric-card">
          <span className="eval-metric-icon">
            <ChartNoAxesCombined size={17} />
          </span>
          <h3>Final-status accuracy</h3>
          <strong>{percentage(metrics.status_accuracy)}</strong>
          <p>Expected versus recorded status</p>
        </div>
        <div className="eval-metric-card eval-refund-card">
          <span className="eval-metric-icon">
            <ShieldCheck size={17} />
          </span>
          <h3>Unauthorized refunds</h3>
          <div className="eval-refund-numbers">
            <div>
              <strong>{numeric(metrics.unauthorized_refunds_attempted)}</strong>
              <span>Attempted</span>
            </div>
            <div
              className={
                metrics.unauthorized_refunds_executed > 0
                  ? "eval-executed-warning"
                  : ""
              }
            >
              <strong>{numeric(metrics.unauthorized_refunds_executed)}</strong>
              <span>Executed</span>
            </div>
          </div>
          <p>
            Across {numeric(metrics.refund_risk_tickets)} refund-risk tickets
          </p>
        </div>
        <div className="eval-metric-card">
          <span className="eval-metric-icon">
            <TriangleAlert size={17} />
          </span>
          <h3>Unsupported replies</h3>
          <strong>{numeric(metrics.unsupported_replies)}</strong>
          <p>Reported reply count</p>
        </div>
        <div className="eval-metric-card">
          <span className="eval-metric-icon">
            <CircleDashed size={17} />
          </span>
          <h3>Correct pauses</h3>
          <strong>
            {numeric(metrics.correct_pauses)}
            <small> / {numeric(metrics.expected_pauses)}</small>
          </strong>
          <p>Of the pauses expected</p>
        </div>
        <div className="eval-metric-card">
          <span className="eval-metric-icon">
            <BookOpenText size={17} />
          </span>
          <h3>Policy citations</h3>
          <strong>{percentage(metrics.citation_precision)}</strong>
          <p>Precision · Recall {percentage(metrics.citation_recall)}</p>
        </div>
      </div>
    </section>
  );
}

// A compact accuracy bar is supplementary; the table retains the exact numeric value.
function Accuracy({ value }: { value: number }) {
  const width = Number.isFinite(value)
    ? Math.min(100, Math.max(0, value * 100))
    : 0;
  return (
    <div className="eval-accuracy">
      <span>{percentage(value)}</span>
      <i aria-hidden="true">
        <span style={{ width: `${width}%` }} />
      </i>
    </div>
  );
}

// Breakdowns preserve each evaluation group's own denominator and expected final state.
function Breakdown({ run }: { run: EvalRun }) {
  return (
    <div className="eval-breakdowns">
      <section className="panel eval-breakdown-panel">
        <div className="eval-section-heading">
          <h2>By scenario group</h2>
          <DataBadge mock={run.mock} />
        </div>
        {run.by_group.length ? (
          <div className="eval-table-scroll">
            <table className="eval-table">
              <thead>
                <tr>
                  <th>Scenario group</th>
                  <th>Tickets</th>
                  <th>Status accuracy</th>
                </tr>
              </thead>
              <tbody>
                {run.by_group.map((row) => (
                  <tr key={row.group}>
                    <th scope="row">{row.group}</th>
                    <td>{row.tickets}</td>
                    <td>
                      <Accuracy value={row.status_accuracy} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="eval-empty-section">
            No scenario-group breakdown was supplied.
          </p>
        )}
      </section>
      <section className="panel eval-breakdown-panel">
        <div className="eval-section-heading">
          <h2>By expected end state</h2>
          <DataBadge mock={run.mock} />
        </div>
        {run.by_end_state.length ? (
          <div className="eval-table-scroll">
            <table className="eval-table">
              <thead>
                <tr>
                  <th>Expected state</th>
                  <th>Tickets</th>
                  <th>Status accuracy</th>
                </tr>
              </thead>
              <tbody>
                {run.by_end_state.map((row) => (
                  <tr key={row.status}>
                    <th scope="row">
                      <StatusBadge status={row.status} />
                    </th>
                    <td>{row.tickets}</td>
                    <td>
                      <Accuracy value={row.status_accuracy} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="eval-empty-section">
            No final-state breakdown was supplied.
          </p>
        )}
      </section>
    </div>
  );
}

// The outcome remains Not run when no recorded actual state exists, regardless of a default false flag.
function ticketOutcome(
  ticket: EvalRun["tickets"][number],
): "pass" | "fail" | "not_run" {
  return ticket.actual_status === null || ticket.passed === null
    ? "not_run"
    : ticket.passed
      ? "pass"
      : "fail";
}

// Per-ticket links intentionally open current interactive state rather than pretending a mock benchmark has live traces.
function TicketResults({ run }: { run: EvalRun }) {
  const [search, setSearch] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [page, setPage] = useState(0);
  useEffect(() => {
    setPage(0);
  }, [search, outcome, run.run_id]);
  const filtered = run.tickets.filter(
    (ticket) =>
      (outcome === "all" || ticketOutcome(ticket) === outcome) &&
      `${ticket.ticket_id} ${ticket.scenario_type} ${ticket.expected_status} ${ticket.actual_status ?? ""} ${ticket.notes.join(" ")}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  );
  const size = 15;
  const pageCount = Math.max(1, Math.ceil(filtered.length / size));
  const selectedPage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(
    selectedPage * size,
    (selectedPage + 1) * size,
  );
  return (
    <section className="panel eval-ticket-panel">
      <div className="eval-section-heading">
        <div>
          <h2>
            Per-ticket outcomes<span>{run.tickets.length}</span>
          </h2>
          <p>Links open current ticket history, not a benchmark trace.</p>
        </div>
        <DataBadge mock={run.mock} />
      </div>
      <div className="eval-ticket-toolbar">
        <label className="search-field">
          <Search size={16} />
          <input
            aria-label="Search evaluation tickets"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search ticket, scenario, or notes…"
          />
        </label>
        <label className="eval-outcome-filter">
          Outcome
          <select
            aria-label="Filter ticket outcomes"
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
          >
            <option value="all">All outcomes</option>
            <option value="pass">Pass</option>
            <option value="fail">Fail</option>
            <option value="not_run">Not run</option>
          </select>
        </label>
      </div>
      {visible.length ? (
        <div className="eval-table-scroll">
          <table className="eval-table eval-ticket-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Scenario</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Outcome</th>
                <th>Workspace</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((ticket) => {
                const result = ticketOutcome(ticket);
                return (
                  <tr key={ticket.ticket_id}>
                    <td>
                      <Link
                        className="eval-ticket-id"
                        to={`/tickets/${ticket.ticket_id}`}
                      >
                        {ticket.ticket_id}
                      </Link>
                      {ticket.notes.length > 0 && (
                        <details className="eval-ticket-notes">
                          <summary>Evaluation notes</summary>
                          <ul>
                            {ticket.notes.map((note, index) => (
                              <li key={index}>{note}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </td>
                    <td>{ticket.scenario_type}</td>
                    <td>
                      <StatusBadge status={ticket.expected_status} />
                    </td>
                    <td>
                      {ticket.actual_status ? (
                        <StatusBadge status={ticket.actual_status} />
                      ) : (
                        <span className="eval-not-run-value">Not run</span>
                      )}
                    </td>
                    <td>
                      <span
                        className={`eval-outcome-badge eval-outcome-${result}`}
                      >
                        {result === "pass" ? (
                          <Check size={11} />
                        ) : result === "fail" ? (
                          <X size={11} />
                        ) : (
                          <CircleDashed size={11} />
                        )}
                        {result === "not_run" ? "Not run" : humanize(result)}
                      </span>
                    </td>
                    <td>
                      <div className="eval-ticket-links">
                        <Link to={`/tickets/${ticket.ticket_id}`}>
                          Open ticket
                          <ArrowUpRight size={11} />
                        </Link>
                        <Link
                          to={`/tickets/${ticket.ticket_id}/trace`}
                          title="Current interactive trace; not the illustrative evaluation run’s trace"
                        >
                          Current trace
                          <ArrowUpRight size={11} />
                        </Link>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No evaluation tickets match">
          Try a different ticket ID or outcome filter.
        </EmptyState>
      )}
      <div className="eval-ticket-pagination">
        <span>
          {filtered.length
            ? `${selectedPage * size + 1}–${Math.min((selectedPage + 1) * size, filtered.length)} of ${filtered.length} tickets`
            : "0 matching tickets"}
        </span>
        <div>
          <button
            className="icon-button"
            aria-label="Previous evaluation page"
            disabled={selectedPage === 0}
            onClick={() => setPage(selectedPage - 1)}
          >
            <ChevronLeft size={15} />
          </button>
          <span>
            Page {selectedPage + 1} of {pageCount}
          </span>
          <button
            className="icon-button"
            aria-label="Next evaluation page"
            disabled={selectedPage + 1 >= pageCount}
            onClick={() => setPage(selectedPage + 1)}
          >
            <ChevronRight size={15} />
          </button>
        </div>
      </div>
    </section>
  );
}

// "agent-test-v2" -> "v2"; runs saved before tags existed (agent-test, baseline-test) are the first version.
function runVersion(summary: EvalRunSummary): string {
  const tag = summary.run_id.split(`-${summary.split}`)[1]?.replace(/^-/, "");
  return (tag || "v1").split("-")[0];
}

// Runs saved before the effort was configurable ran at the model's default, which is medium.
const effortOf = (run: EvalRunSummary) =>
  (run.settings?.reasoning_effort ?? "medium").replace("default (medium)", "medium");

// Every measured run side by side: one column per run, one row per metric, test and dev split apart.
function AllRunsTable({ runs }: { runs: EvalRunSummary[] }) {
  const measured = runs.filter((run) => run.metrics);
  if (!measured.length) return null;
  const order = (run: EvalRunSummary) =>
    `${run.system === "agent" ? 0 : 1}-${runVersion(run)}-${["low", "medium", "high"].indexOf(effortOf(run))}`;
  const splits = (["test", "dev"] as const)
    .map((split) => ({
      split,
      runs: measured
        .filter((run) => run.split === split)
        .sort((a, b) => order(a).localeCompare(order(b))),
    }))
    .filter((group) => group.runs.length);
  return (
    <section className="panel eval-comparison-panel" aria-label="All runs compared">
      <div className="eval-section-heading">
        <div>
          <h2>All runs compared</h2>
          <p>Every evaluation run side by side, by split.</p>
        </div>
        <DataBadge mock={measured.some((run) => run.mock)} />
      </div>
      {splits.map(({ split, runs: group }) => (
        <div key={split} className="eval-all-runs-group">
          <h3>
            {split === "test" ? "Test" : "Development"} split ·{" "}
            {typeof group[0].tickets === "number" ? group[0].tickets : ""} tickets
          </h3>
          <div className="eval-table-scroll">
            <table className="eval-table eval-all-runs-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  {group.map((run) => (
                    <th key={run.run_id} title={run.run_id}>
                      {run.system === "agent" ? "Agent" : "Baseline"} {runVersion(run)}
                      <small>{effortOf(run)} effort</small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {METRIC_ROWS.map((row) => (
                  <tr key={row.label} className={row.safety ? "eval-safety-row" : ""}>
                    <th scope="row">{row.label}</th>
                    {group.map((run) => (
                      <td key={run.run_id}>{row.value(run as Measured)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </section>
  );
}

// Retrieval figures are supplied results; viewing this page does not perform a new retrieval experiment.
function RetrievalCard() {
  const query = useRetrievalMetrics();
  return (
    <details className="panel eval-retrieval-panel">
      <summary className="eval-section-heading">
        <strong>Policy retrieval details</strong>{" "}
        {query.data && <DataBadge mock={query.data.mock} />}
      </summary>
      {query.isPending ? (
        <div className="eval-loading" role="status">
          <LoaderCircle size={17} className="trace-spin" />
          Loading retrieval results…
        </div>
      ) : query.isError ? (
        <EmptyState
          error
          title="Retrieval results couldn’t load"
          retry={() => {
            void query.refetch();
          }}
        >
          {query.error.message}
        </EmptyState>
      ) : (
        <>
          <div className="eval-retrieval-note">
            <Info size={14} />
            <p>
              {query.data.mock || query.data.measured === false
                ? "Illustrative figures; no new retrieval experiment was run."
                : "Results supplied by the evaluation backend."}
              <span>{query.data.provenance}</span>
            </p>
          </div>
          <div className="eval-retrieval-context">
            {query.data.queries} queries · Relevant evidence in the top three
            passages
          </div>
          {query.data.methods.length ? (
            <div className="eval-table-scroll">
              <table className="eval-table eval-retrieval-table">
                <thead>
                  <tr>
                    <th>Method</th>
                    <th>Hit@3</th>
                    <th>Recall@3</th>
                    <th>Precision@3</th>
                    <th>MRR</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.methods.map((method) => (
                    <tr key={method.method}>
                      <th scope="row">
                        {method.method === "embeddings"
                          ? "Embeddings · cosine"
                          : method.method === "bm25"
                            ? "BM25"
                            : humanize(method.method)}
                      </th>
                      <td>{percentage(method.hit_at_3)}</td>
                      <td>{percentage(method.recall_at_3)}</td>
                      <td>{percentage(method.precision_at_3)}</td>
                      <td>{numeric(method.mrr, 3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="eval-empty-section">
              No retrieval methods were supplied.
            </p>
          )}
        </>
      )}
    </details>
  );
}

// This dashboard reads immutable evaluation artifacts; it never launches model calls or grades tickets in the browser.
export function EvaluationPage() {
  const runs = useEvalRuns();
  const [params, setParams] = useSearchParams();
  const rawSystem = params.get("system"),
    rawSplit = params.get("split");
  const system = rawSystem === "baseline" ? "baseline" : "agent";
  const split = rawSplit === "dev" ? "dev" : "test";
  const invalidFilters = Boolean(
    (rawSystem && !["agent", "baseline"].includes(rawSystem)) ||
    (rawSplit && !["dev", "test"].includes(rawSplit)),
  );
  const summaries = runs.data ?? [];
  const available = useMemo(
    () =>
      summaries
        .filter((run) => run.system === system && run.split === split)
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [summaries, system, split],
  );
  const requestedId = params.get("run");
  const summary = requestedId
    ? available.find((run) => run.run_id === requestedId)
    : available[0];
  const invalidRun = Boolean(requestedId && !summary && runs.isSuccess);
  const selected = useEvalRun(invalidFilters ? "" : (summary?.run_id ?? ""));
  const otherSummary = summaries.find(
    (run) =>
      run.system !== system &&
      run.split === split &&
      (!summary?.benchmark_id || run.benchmark_id === summary.benchmark_id) &&
      (!summary?.policy_snapshot ||
        run.policy_snapshot === summary.policy_snapshot),
  );
  const other = useEvalRun(
    invalidFilters || invalidRun ? "" : (otherSummary?.run_id ?? ""),
  );
  useEffect(() => {
    document.title = "Evaluation · OP-US";
  }, []);
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    if (key !== "run") next.delete("run");
    setParams(next, { replace: true });
  };
  const run = selected.data;
  const mocked = Boolean(run?.mock ?? summary?.mock);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">RESULTS WITH CONTEXT</div>
          <h1>
            Evaluation<span className="heading-dot">.</span>
          </h1>
          <p>Review outcomes and safety on matching inputs.</p>
        </div>
        <span className="eval-page-mark">
          <ChartNoAxesCombined size={19} />
          Agent vs. single pass
        </span>
      </div>
      <div className="eval-selection-bar">
        <label>
          System
          <select
            aria-label="Evaluation system"
            value={system}
            onChange={(event) => update("system", event.target.value)}
          >
            <option value="agent">Adaptive agent</option>
            <option value="baseline">Single-pass baseline</option>
          </select>
        </label>
        <label>
          Dataset split
          <select
            aria-label="Evaluation split"
            value={split}
            onChange={(event) => update("split", event.target.value)}
          >
            <option value="test">Test</option>
            <option value="dev">Development</option>
          </select>
        </label>
        <label className="eval-run-select">
          Evaluation run
          <select
            aria-label="Evaluation run"
            value={summary?.run_id ?? ""}
            onChange={(event) => update("run", event.target.value)}
            disabled={!available.length || invalidFilters}
          >
            <option value="" disabled>
              {runs.isPending
                ? "Loading runs…"
                : available.length
                  ? "Choose a run"
                  : "No runs available"}
            </option>
            {available.map((item) => (
              <option key={item.run_id} value={item.run_id}>
                {item.run_id}
              </option>
            ))}
          </select>
        </label>
        <span className="eval-read-only">
          <ArrowDownUp size={13} />
          Prepared results · read-only
        </span>
      </div>
      {runs.isPending ? (
        <div className="panel eval-loading" role="status">
          <LoaderCircle size={18} className="trace-spin" />
          Loading evaluation runs…
        </div>
      ) : runs.isError ? (
        <div className="panel">
          <EmptyState
            error
            title="Evaluation runs couldn’t load"
            retry={() => {
              void runs.refetch();
            }}
          >
            {runs.error.message}
          </EmptyState>
        </div>
      ) : invalidFilters || invalidRun ? (
        <div className="panel">
          <EmptyState error title="This evaluation selection isn’t available">
            {invalidFilters
              ? "Choose a supported system and dataset split."
              : "The requested run is not available for this system and split."}
            <br />
            <button className="text-link" onClick={() => setParams({})}>
              Show the default evaluation view
            </button>
          </EmptyState>
        </div>
      ) : !summary ? (
        <div className="panel">
          <EmptyState title="No evaluation runs for this view">
            Choose another system or split. Prepared results will appear here
            when supplied by the backend.
          </EmptyState>
        </div>
      ) : selected.isPending ? (
        <div className="panel eval-loading" role="status">
          <LoaderCircle size={18} className="trace-spin" />
          Loading the selected evaluation…
        </div>
      ) : selected.isError ? (
        <div className="panel">
          <EmptyState
            error
            title="This evaluation run couldn’t load"
            retry={() => {
              void selected.refetch();
            }}
          >
            {selected.error.message}
          </EmptyState>
        </div>
      ) : (
        run && (
          <>
            <div
              className={`eval-provenance-banner ${mocked ? "eval-provenance-mock" : ""}`}
            >
              <FlaskConical size={21} />
              <div>
                <strong>
                  {mocked
                    ? "MOCK DATA · Illustrative benchmark results"
                    : "Supplied evaluation results"}
                </strong>
                <p>
                  {mocked
                    ? "Scripted figures for this dashboard; no live model performance was measured."
                    : "Results supplied by the evaluation backend."}
                </p>
                {run.provenance && (
                  <details className="eval-provenance-details">
                    <summary>Data source and limitations</summary>
                    <p>{run.provenance}</p>
                  </details>
                )}
              </div>
            </div>
            <div className="eval-run-context">
              <span>
                {run.system === "agent"
                  ? "Adaptive agent"
                  : "Single-pass baseline"}
              </span>
              <span>
                {run.split === "dev" ? "Development" : "Test"} ·{" "}
                {run.tickets.length} tickets
              </span>
              <span>Created {date(run.created_at)}</span>
            </div>
            <HeadlineMetrics run={run} />
            <section className="panel eval-comparison-panel">
              <div className="eval-section-heading">
                <h2>Agent versus baseline</h2>
                <DataBadge mock={mocked} />
              </div>
              {!otherSummary ? (
                <div className="eval-comparison-unavailable">
                  <Info size={21} />
                  <strong>No matching comparison run is available.</strong>
                  <p>
                    A counterpart must use the same split, benchmark, and policy
                    snapshot.
                  </p>
                </div>
              ) : other.isPending ? (
                <div className="eval-loading" role="status">
                  <LoaderCircle size={17} className="trace-spin" />
                  Loading the comparison run…
                </div>
              ) : other.isError ? (
                <EmptyState
                  error
                  title="The comparison run couldn’t load"
                  retry={() => {
                    void other.refetch();
                  }}
                >
                  {other.error.message}
                </EmptyState>
              ) : (
                other.data && (
                  <ComparisonTable selected={run} counterpart={other.data} />
                )
              )}
            </section>
            <TicketResults run={run} />
            <details className="panel eval-comparison-panel">
              <summary className="eval-section-heading">
                <strong>Breakdowns by scenario and end state</strong>{" "}
                <DataBadge mock={mocked} />
              </summary>
              <div className="eval-breakdown-content">
                <Breakdown run={run} />
              </div>
            </details>
          </>
        )
      )}
      {runs.data && <AllRunsTable runs={runs.data} />}
      <RetrievalCard />
    </>
  );
}
