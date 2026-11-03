import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  CircleHelp,
  FlaskConical,
  ListChecks,
  LoaderCircle,
  TriangleAlert,
  X,
} from "lucide-react";
import { useExpected, useTicket } from "../api";
import { EmptyState, PolicyChip } from "../components";
import { humanize } from "../format";
import type { GradingCheck } from "../types";

// Values can be arrays, records or missing; none are inserted as raw HTML.
function display(value: unknown): string {
  if (value === null || value === undefined) return "Not recorded";
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value.length ? value.map(display).join(" · ") : "None";
  return JSON.stringify(value, null, 2);
}

// A null judgment means unverified, not a pass, including all semantic response checks.
function CheckRow({ check }: { check: GradingCheck }) {
  const Icon = check.passed === null ? CircleHelp : check.passed ? Check : X;
  const label =
    check.passed === null ? "Not verified" : check.passed ? "Match" : "Differs";
  return (
    <tr>
      <th scope="row">{check.label}</th>
      <td>
        <pre>{display(check.expected)}</pre>
      </td>
      <td>
        <pre>{display(check.actual)}</pre>
      </td>
      <td>
        <span
          className={`grader-check grader-check-${check.passed === null ? "unknown" : check.passed ? "match" : "different"}`}
        >
          <Icon size={13} />
          {label}
        </span>
        {check.note && <p className="grader-check-note">{check.note}</p>}
      </td>
    </tr>
  );
}

// Mounting one panel per ticket resets its opt-in; ordinary navigation never requests expected answers.
export function GraderPanel({ ticketId }: { ticketId: string }) {
  const [enabled, setEnabled] = useState(false);
  const ticket = useTicket(ticketId);
  const running = Boolean(ticket.data?.running);
  const query = useExpected(ticketId, enabled, running);
  const refetch = query.refetch;
  const previouslyRunning = useRef(false);
  useEffect(() => {
    if (enabled && previouslyRunning.current && !running) void refetch();
    previouslyRunning.current = running;
  }, [enabled, running, refetch]);
  const expected = query.data;
  const labels = {
    not_run: "No completed run",
    running: "Run in progress",
    passed: "Checked fields match",
    failed: "Some checks differ",
  };
  return (
    <section className="panel grader-panel" aria-label="Optional grader view">
      <div className="grader-header">
        <div>
          <h2>
            <ListChecks size={17} />
            Inspect expected outcomes
          </h2>
          <p>
            Opt in to compare draft fixture expectations with recorded behavior.
          </p>
        </div>
        <label className="grader-toggle">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
            aria-controls={`grader-${ticketId}`}
          />
          Grader view
        </label>
      </div>
      {enabled && (
        <div id={`grader-${ticketId}`} className="grader-content">
          {query.isPending ? (
            <div className="trace-loading" role="status">
              <LoaderCircle size={18} className="spinning" />
              Loading draft expectations…
            </div>
          ) : query.isError ? (
            <EmptyState
              error
              title="Expected outcomes couldn’t load"
              retry={() => {
                void query.refetch();
              }}
            >
              {query.error.message}
            </EmptyState>
          ) : (
            expected && (
              <>
                <div className="grader-warning">
                  <TriangleAlert size={17} />
                  <p>{expected.warning}</p>
                </div>
                <div className="grader-context">
                  <span
                    className={`grader-summary grader-summary-${expected.grading.state}`}
                  >
                    {labels[expected.grading.state] ??
                      humanize(expected.grading.state)}
                  </span>
                  <span>
                    {expected.phase === "after_resume"
                      ? "After-resume annotation"
                      : "Initial annotation"}
                  </span>
                  <span>
                    <FlaskConical size={12} />
                    {expected.source}
                  </span>
                </div>
                <div className="grader-expected-policies">
                  <span>Expected policy evidence</span>
                  {expected.policy_ids.map((id) => (
                    <PolicyChip id={id} key={id} />
                  ))}
                </div>
                <div className="grader-table-scroll">
                  <table className="grader-table">
                    <thead>
                      <tr>
                        <th>Check</th>
                        <th>Expected</th>
                        <th>Recorded</th>
                        <th>Comparison</th>
                      </tr>
                    </thead>
                    <tbody>
                      {expected.grading.checks.map((check) => (
                        <CheckRow key={check.key} check={check} />
                      ))}
                    </tbody>
                  </table>
                </div>
                {expected.grading.notes.length > 0 && (
                  <div className="grader-notes">
                    {expected.grading.notes.map((note) => (
                      <p key={note}>{note}</p>
                    ))}
                  </div>
                )}
                <details className="grader-reference">
                  <summary>
                    Draft response facts and permitted actions
                    <ChevronDown size={13} />
                  </summary>
                  <div>
                    <h3>Facts to review manually</h3>
                    {expected.response_facts.length ? (
                      <ul>
                        {expected.response_facts.map((fact) => (
                          <li key={fact}>{fact}</li>
                        ))}
                      </ul>
                    ) : (
                      <p>No phase-specific response facts are annotated.</p>
                    )}
                    <h3>Expected actions</h3>
                    <p>{expected.expected_actions.join(" · ") || "None"}</p>
                    <h3>Forbidden actions</h3>
                    <p>{expected.forbidden_actions.join(" · ") || "None"}</p>
                  </div>
                </details>
              </>
            )
          )}
        </div>
      )}
    </section>
  );
}
