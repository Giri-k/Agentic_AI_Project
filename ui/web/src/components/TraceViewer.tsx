import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Bot,
  Check,
  CircleCheck,
  CircleX,
  Clock3,
  LoaderCircle,
  RotateCcw,
  ShieldX,
  TriangleAlert,
  UserRound,
  Wrench,
} from "lucide-react";
import { useHealth, useTicket, useTrace } from "../api";
import { EmptyState, PolicyChip, ReceiptChip } from "../components";
import { humanize, STATUS_LABELS } from "../format";

type TraceRecord = {
  ts: string;
  ticket_id: string;
  run_id: string;
  event: string;
  [key: string]: unknown;
};
type TraceRow = { event: TraceRecord; outcome?: TraceRecord };
type TraceGroup = { id: string; started?: TraceRecord; rows: TraceRow[] };

const EMPTY_BUDGET = {
  model_calls: 0,
  tool_calls: 0,
  retries: 0,
  schema_repairs: 0,
  revisions: 0,
};
const REFUND_GATE_CODES = new Set([
  "NO_MATCHING_APPROVAL",
  "APPROVAL_ALREADY_USED",
  "EXCEEDS_REMAINING_BALANCE",
  "NOT_ELIGIBLE",
  "APPROVAL_CONFLICT",
  "ORDER_NOT_FOUND",
  "WRONG_TICKET",
]);

// Unknown or evolving fields stay displayable without making assumptions about the real agent.
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

// Tool arguments are JSON strings today; malformed values still render safely as ordinary text.
function pretty(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value ?? null, null, 2);
}

// Log timestamps use an explicit UTC clock rather than relative laptop dates.
function clock(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZone: "UTC",
      }).format(parsed);
}

// Match requests and outcomes by run and call ID so one invocation becomes one timeline row.
function groupEvents(events: TraceRecord[]): TraceGroup[] {
  const groups = new Map<string, TraceGroup>();
  for (const event of events) {
    if (!groups.has(event.run_id))
      groups.set(event.run_id, { id: event.run_id, rows: [] });
  }
  const outcomes = new Map<string, TraceRecord>();
  for (const event of events) {
    if (
      ["tool_result", "tool_refused", "stop"].includes(event.event) &&
      typeof event.call_id === "string"
    )
      outcomes.set(`${event.run_id}:${event.call_id}`, event);
  }
  const requests = new Set(
    events
      .filter(
        (event) =>
          event.event === "tool_request" && typeof event.call_id === "string",
      )
      .map((event) => `${event.run_id}:${event.call_id}`),
  );
  for (const event of events) {
    const group = groups.get(event.run_id)!;
    if (event.event === "run_started") {
      group.started = event;
      continue;
    }
    const key = `${event.run_id}:${event.call_id}`;
    if (
      ["tool_result", "tool_refused", "stop"].includes(event.event) &&
      requests.has(key)
    )
      continue;
    group.rows.push({
      event,
      outcome: event.event === "tool_request" ? outcomes.get(key) : undefined,
    });
  }
  return [...groups.values()];
}

// An error filter includes stops and refusals while retaining unknown explicitly failed events.
function isError(row: TraceRow): boolean {
  const event = row.outcome ?? row.event;
  return (
    event.ok === false ||
    Boolean(event.error) ||
    (event.event === "run_finished" && event.status === "failed_safe") ||
    ["tool_refused", "stop", "failed_safe", "failed_safe_not_saved"].includes(
      event.event,
    )
  );
}

// Search evidence receives a compact rendering while its full payload remains inspectable.
function SearchEvidence({ result }: { result: Record<string, unknown> }) {
  const passages = Array.isArray(result.passages)
    ? result.passages.map(record)
    : [];
  return (
    <div className="trace-search-evidence">
      <div className="trace-search-tags">
        <span>
          {typeof result.method === "string"
            ? result.method.toUpperCase()
            : "POLICY SEARCH"}
        </span>
        {typeof result.best_score === "number" && (
          <span>Best score {result.best_score.toFixed(3)}</span>
        )}
        {result.weak_evidence === true && (
          <span className="trace-tag-warning">
            <TriangleAlert size={12} />
            Weak evidence
          </span>
        )}
      </div>
      {typeof result.fallback_reason === "string" && (
        <p className="trace-fallback">
          <RotateCcw size={12} />
          {result.fallback_reason}
        </p>
      )}
      {passages.map((passage, index) => (
        <div
          className="trace-policy-evidence"
          key={`${String(passage.policy_id)}-${index}`}
        >
          {typeof passage.policy_id === "string" ? (
            <PolicyChip id={passage.policy_id} />
          ) : (
            <span>Unidentified source</span>
          )}
          <span>
            {typeof passage.best_section === "string"
              ? passage.best_section
              : "Policy passage"}
            {typeof passage.score === "number"
              ? ` · ${passage.score.toFixed(3)}`
              : ""}
          </span>
          {typeof passage.text === "string" && (
            <details>
              <summary>Read retrieved passage</summary>
              <p>{passage.text}</p>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

// A completed request shows its receipt or error, never a guessed action outcome.
function ToolRow({
  row,
  showArguments,
}: {
  row: TraceRow;
  showArguments: boolean;
}) {
  const event = row.event;
  const outcome =
    row.outcome ?? (event.event === "tool_request" ? undefined : event);
  const result = record(outcome?.result);
  const error = record(outcome?.error);
  const tool = typeof event.tool === "string" ? event.tool : "Unknown tool";
  const failed = outcome ? isError({ event: outcome }) : false;
  const refused = outcome?.event === "tool_refused";
  const stopped = outcome?.event === "stop";
  const replayed = result.replayed === true;
  const Icon = failed
    ? refused
      ? ShieldX
      : CircleX
    : outcome
      ? replayed
        ? RotateCcw
        : CircleCheck
      : LoaderCircle;
  const status = stopped
    ? "Stopped"
    : refused
      ? "Refused"
      : failed
        ? "Error"
        : !outcome
          ? "In progress"
          : replayed
            ? "Receipt replayed"
            : "Succeeded";
  const gate =
    tool === "process_refund" &&
    typeof error.code === "string" &&
    REFUND_GATE_CODES.has(error.code);
  return (
    <div
      className={`trace-row trace-tool-row ${failed ? "trace-row-error" : ""}`}
    >
      <div
        className={`trace-node ${failed ? "trace-node-error" : "trace-node-tool"}`}
      >
        <Wrench size={15} />
      </div>
      <div className="trace-row-content">
        <div className="trace-row-heading">
          <strong className="trace-tool-name">{tool}</strong>
          <span
            className={`trace-outcome ${failed ? "trace-outcome-error" : ""}`}
          >
            <Icon size={12} className={!outcome ? "trace-spin" : ""} />
            {status}
          </span>
          <time dateTime={event.ts}>{clock(event.ts)}</time>
        </div>
        {gate && (
          <div className="trace-safety-callout">
            <ShieldX size={16} />
            <strong>Refund blocked by Python gate</strong>
          </div>
        )}
        {failed && (
          <p className="trace-error-message">
            <code>
              {typeof error.code === "string"
                ? error.code
                : stopped
                  ? String(outcome?.reason ?? "STOPPED")
                  : "TOOL_ERROR"}
            </code>
            {typeof error.message === "string" && <span>{error.message}</span>}
          </p>
        )}
        <div className="trace-row-flags">
          {refused && (
            <span>Rejected before execution · no tool budget used</span>
          )}
          {outcome?.retry === true && (
            <span className="trace-retry">
              <RotateCcw size={11} />
              Retry
            </span>
          )}
          {replayed && <span>Saved receipt reused · no duplicate action</span>}
          {typeof result.receipt_id === "string" && (
            <ReceiptChip id={result.receipt_id} />
          )}
        </div>
        {tool === "kb_lookup" && outcome?.ok === true && (
          <SearchEvidence result={result} />
        )}
        <div className="trace-inspectors">
          {showArguments && event.args !== undefined && (
            <details>
              <summary>Arguments</summary>
              <pre>{pretty(event.args)}</pre>
            </details>
          )}
          {outcome && (
            <details>
              <summary>{failed ? "Error payload" : "Tool result"}</summary>
              <pre>
                {pretty(
                  failed
                    ? (outcome.error ?? { reason: outcome.reason })
                    : outcome.result,
                )}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

// Known non-tool events have short labels; newly introduced events retain their raw payload.
function EventRow({
  event,
  modelLimit,
}: {
  event: TraceRecord;
  modelLimit?: number;
}) {
  let title = humanize(event.event);
  let detail = "";
  let kind = "generic";
  let Icon = Activity;
  if (event.event === "model_call") {
    title = `Model call ${String(event.n ?? "—")}${modelLimit ? `/${modelLimit}` : ""}`;
    const calls = Array.isArray(event.tool_calls)
      ? event.tool_calls.filter((value) => typeof value === "string")
      : [];
    detail =
      typeof event.text === "string"
        ? event.text
        : calls.length
          ? `Selected ${calls.join(", ")}`
          : "Decision recorded";
    kind = "model";
    Icon = Bot;
  } else if (event.event === "demo_notice") {
    title = "Scripted test context";
    detail =
      typeof event.text === "string"
        ? event.text
        : "Inspect the mock scenario context.";
    kind = "warning";
    Icon = TriangleAlert;
  } else if (event.event === "staff_approval") {
    title = "Staff approval activated";
    const approval = record(event.approval);
    detail = [approval.approval_id, approval.approved_by]
      .filter((value) => typeof value === "string")
      .join(" · ");
    kind = "staff";
    Icon = UserRound;
  } else if (event.event === "route") {
    title =
      typeof event.team === "string"
        ? `Routed to ${humanize(event.team)}`
        : "Routed";
    detail =
      typeof event.category === "string"
        ? `Category ${humanize(event.category)} · chosen by Python from the classification`
        : "";
    kind = "model";
    Icon = ArrowUpRight;
  } else if (event.event === "reply_check" || event.event === "revision") {
    const problems = Array.isArray(event.problems)
      ? event.problems.filter((value) => typeof value === "string")
      : [];
    // Python's rule checks run first; the evaluator model only sees replies that pass them.
    const checker =
      event.checked_by === "evaluator"
        ? "Python rules and the evaluator model"
        : "Python rules";
    if (event.event === "revision") {
      title = typeof event.n === "number" ? `Revision ${event.n}` : "Revision";
      detail = problems.length
        ? `Sent back to the model to fix: ${problems.join("; ")}`
        : "Sent back to the model to fix its reply.";
      kind = "warning";
      Icon = RotateCcw;
    } else if (event.supported === true) {
      title = "Reply check passed";
      detail = `Checked by ${checker}. The reply is supported by the evidence.`;
      kind = "finished";
      Icon = CircleCheck;
    } else {
      title = "Reply check found problems";
      detail = `${checker}: ${problems.join("; ") || "unsupported claims"}`;
      kind = "warning";
      Icon = ShieldX;
    }
  } else if (event.event === "run_finished") {
    const status = typeof event.status === "string" ? event.status : "";
    title = status
      ? `Run finished · ${STATUS_LABELS[status as keyof typeof STATUS_LABELS] ?? humanize(status)}`
      : "Run finished";
    detail = "The ticket’s saved result and records have been refreshed.";
    kind = status === "failed_safe" ? "error" : "finished";
    Icon = status === "failed_safe" ? TriangleAlert : Check;
  } else if (
    ["stop", "failed_safe", "failed_safe_not_saved", "schema_repair"].includes(
      event.event,
    )
  ) {
    const error = record(event.error);
    const errorDescription = [error.code, error.message]
      .filter((value) => typeof value === "string")
      .join(" · ");
    detail =
      typeof event.reason === "string"
        ? humanize(event.reason)
        : typeof event.error === "string"
          ? event.error
          : errorDescription || "Inspect the recorded event for details.";
    kind = event.event === "schema_repair" ? "warning" : "error";
    Icon = TriangleAlert;
  }
  const generic = kind === "generic";
  const tokensIn =
    typeof event.tokens_in === "number" ? event.tokens_in : undefined;
  const tokensOut =
    typeof event.tokens_out === "number" ? event.tokens_out : undefined;
  const receipt = record(event.receipt);
  return (
    <div
      className={`trace-row trace-event-row ${kind === "error" ? "trace-row-error" : ""}`}
    >
      <div className={`trace-node trace-node-${kind}`}>
        <Icon size={15} />
      </div>
      <div className="trace-row-content">
        <div className="trace-row-heading">
          <strong>{title}</strong>
          <time dateTime={event.ts}>{clock(event.ts)}</time>
        </div>
        {detail && <p className="trace-event-detail">{detail}</p>}
        {typeof receipt.receipt_id === "string" && (
          <div className="trace-row-flags">
            <ReceiptChip id={receipt.receipt_id} />
          </div>
        )}
        {event.event === "model_call" && (
          <div className="trace-model-cost">
            {tokensIn !== undefined && <span>{tokensIn} input tokens</span>}
            {tokensOut !== undefined && <span>{tokensOut} output tokens</span>}
            {typeof event.latency_ms === "number" && (
              <span>{event.latency_ms} ms</span>
            )}
          </div>
        )}
        {(generic || kind === "error" || kind === "warning") && (
          <details className="trace-generic-payload">
            <summary>Inspect event</summary>
            <pre>{pretty(event)}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

// Budgets come from server snapshots and stay cumulative when the ticket resumes.
export function TraceViewer({
  ticketId,
  compact = false,
}: {
  ticketId: string;
  compact?: boolean;
}) {
  const ticket = useTicket(ticketId);
  const health = useHealth();
  const running = Boolean(ticket.data?.running);
  const trace = useTrace(ticketId, running);
  const [showArguments, setShowArguments] = useState(false);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const events = (trace.data ?? []) as TraceRecord[];
  const groups = useMemo(() => groupEvents(events), [events]);
  const timeline = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  useEffect(() => {
    followLatest.current = true;
    if (timeline.current) timeline.current.scrollTop = 0;
  }, [ticketId]);
  useEffect(() => {
    // Follow new steps only while the reader remains near the compact timeline's bottom.
    if (compact && running && followLatest.current && timeline.current)
      timeline.current.scrollTop = timeline.current.scrollHeight;
  }, [compact, running, events.length]);
  const execution = ticket.data?.execution;
  const budget = execution?.budget ?? EMPTY_BUDGET;
  const limits = execution?.limits ?? health.data?.limits;
  const notice = execution?.budget_notice ?? health.data?.budget_notice;
  const counters = [
    { key: "model_calls", label: "Model calls" },
    { key: "tool_calls", label: "Tool calls" },
    { key: "retries", label: "Retries" },
    { key: "schema_repairs", label: "Schema repairs" },
    { key: "revisions", label: "Reply revisions" },
  ] as const;
  return (
    <section
      className={`panel trace-panel ${compact ? "trace-panel-compact" : "trace-panel-full"}`}
      aria-label="Execution trace"
    >
      {!compact && (
        <Link
          className="trace-back-link"
          to={`/tickets/${encodeURIComponent(ticketId)}`}
        >
          <ArrowLeft size={14} />
          Back to ticket {ticketId}
        </Link>
      )}
      <div className="trace-heading">
        <div>
          <div className="trace-title-line">
            <Activity size={18} />
            {compact ? <h2>Execution trace</h2> : <h1>Execution trace</h1>}
            {running && (
              <span className="trace-live">
                <i />
                LIVE
              </span>
            )}
          </div>
          <p>
            {compact
              ? "Each decision and action, with its evidence."
              : `${ticketId} · All runs, staff actions and resumed steps.`}
          </p>
        </div>
        {compact && (
          <Link
            className="trace-expand"
            to={`/tickets/${encodeURIComponent(ticketId)}/trace`}
          >
            Full trace
            <ArrowUpRight size={14} />
          </Link>
        )}
      </div>
      <div className="trace-budget-bar">
        <div className="trace-budget-caption">
          <Clock3 size={13} />
          Across this ticket, including resumed runs
        </div>
        <div className="trace-budget-meters">
          {counters.map((counter) => {
            const used = budget[counter.key] ?? 0,
              limit = limits?.[counter.key];
            return (
              <div
                className={`trace-budget ${typeof limit === "number" && used >= limit ? "trace-budget-full" : ""}`}
                key={counter.key}
              >
                <div>
                  <span>{counter.label}</span>
                  <strong>
                    {used}
                    <small>/{limit ?? "—"}</small>
                  </strong>
                </div>
                <meter
                  aria-label={counter.label}
                  min={0}
                  max={Math.max(limit ?? 1, 1)}
                  value={Math.min(used, Math.max(limit ?? 1, 1))}
                />
              </div>
            );
          })}
        </div>
      </div>
      {notice && (
        <div className="trace-budget-notice">
          <TriangleAlert size={14} />
          <p>{notice}</p>
        </div>
      )}
      <div className="trace-filters">
        <label>
          <input
            type="checkbox"
            checked={showArguments}
            onChange={(event) => setShowArguments(event.target.checked)}
          />
          Show arguments
        </label>
        <label>
          <input
            type="checkbox"
            checked={errorsOnly}
            onChange={(event) => setErrorsOnly(event.target.checked)}
          />
          Errors only
        </label>
        <span>
          {events.length} recorded {events.length === 1 ? "event" : "events"}
          <span> · UTC</span>
        </span>
      </div>
      {trace.isPending ? (
        <div className="trace-loading" role="status">
          <LoaderCircle size={18} className="trace-spin" />
          Loading execution history…
        </div>
      ) : trace.isError ? (
        <EmptyState
          error
          title="The trace couldn’t load"
          retry={() => {
            void trace.refetch();
          }}
        >
          {trace.error.message}
        </EmptyState>
      ) : !events.length ? (
        <div className="trace-empty">
          <Activity size={25} />
          <h3>No execution history yet</h3>
          <p>
            Run this ticket to follow its decisions, tool calls and saved
            receipts.
          </p>
        </div>
      ) : (
        <div
          className="trace-groups"
          ref={timeline}
          onScroll={(event) => {
            const pane = event.currentTarget;
            followLatest.current =
              pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
          }}
        >
          {groups.map((group, index) => {
            const rows = errorsOnly ? group.rows.filter(isError) : group.rows;
            if (!rows.length && errorsOnly) return null;
            const label =
              group.id === "staff" ||
              group.rows.some((row) => row.event.event === "staff_approval")
                ? "Staff action"
                : group.started?.mode === "resume"
                  ? "Resume run"
                  : group.started?.system === "baseline"
                    ? "Baseline run"
                    : "Agent run";
            return (
              <div className="trace-group" key={group.id}>
                <div className="trace-group-label">
                  <span>{label}</span>
                  <small>{group.id}</small>
                  {group.started && (
                    <time dateTime={group.started.ts}>
                      {clock(group.started.ts)} UTC
                    </time>
                  )}
                  {index === groups.length - 1 && running && (
                    <span className="trace-group-running">Running</span>
                  )}
                </div>
                <div className="trace-timeline">
                  {rows.map((row, rowIndex) =>
                    ["tool_request", "tool_result", "tool_refused"].includes(
                      row.event.event,
                    ) ? (
                      <ToolRow
                        key={`${String(row.event.call_id ?? row.event.event)}-${rowIndex}`}
                        row={row}
                        showArguments={showArguments}
                      />
                    ) : (
                      <EventRow
                        key={`${row.event.event}-${rowIndex}`}
                        event={row.event}
                        modelLimit={limits?.model_calls}
                      />
                    ),
                  )}
                </div>
              </div>
            );
          })}
          {errorsOnly && groups.every((group) => !group.rows.some(isError)) && (
            <div className="trace-no-errors">
              <CircleCheck size={22} />
              <strong>No errors in this ticket’s trace.</strong>
              <p>Turn off “Errors only” to see every decision and action.</p>
            </div>
          )}
          {running && (
            <div className="trace-running-footer" role="status">
              <LoaderCircle size={14} className="trace-spin" />
              Waiting for the next recorded step…
            </div>
          )}
        </div>
      )}
    </section>
  );
}
