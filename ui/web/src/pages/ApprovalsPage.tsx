import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CircleCheck,
  Clock3,
  FileCheck2,
  LoaderCircle,
  LockKeyhole,
  Play,
  Search,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import {
  useApprovals,
  useApproveApproval,
  useHealth,
  useResumeTicket,
} from "../api";
import { Avatar, EmptyState, StatusBadge } from "../components";
import { date, money } from "../format";
import type { StagedApproval, WaitingTicket } from "../types";

// Resuming is a separate action from approval and uses the server's current eligibility state.
function WaitingCard({
  ticket,
  disabled,
}: {
  ticket: WaitingTicket;
  disabled: boolean;
}) {
  const resume = useResumeTicket(ticket.ticket_id);
  const navigate = useNavigate();
  const canResume = Boolean(ticket.can_resume);
  return (
    <article
      className="waiting-ticket-card"
      aria-labelledby={`waiting-${ticket.ticket_id}`}
    >
      <div className="waiting-ticket-heading">
        <Link
          id={`waiting-${ticket.ticket_id}`}
          to={`/tickets/${ticket.ticket_id}`}
        >
          {ticket.ticket_id}
          <ArrowUpRight size={13} />
        </Link>
        <StatusBadge status="awaiting_approval" />
      </div>
      <h3>{ticket.subject}</h3>
      <div className="waiting-customer">
        <Avatar name={ticket.customer_name} />
        <span>{ticket.customer_name}</span>
      </div>
      {ticket.pending_refund ? (
        <div className="pending-refund">
          <div>
            <span>Order</span>
            <strong>{ticket.pending_refund.order_id}</strong>
          </div>
          <div>
            <span>Approval amount</span>
            <strong>
              {typeof ticket.pending_refund.amount_cents === "number"
                ? money(ticket.pending_refund.amount_cents)
                : "Amount requires staff review"}
            </strong>
          </div>
        </div>
      ) : (
        <p className="approval-missing-amount">
          The required order and refund amount have not been established yet.
        </p>
      )}
      {ticket.unresolved_requirements &&
        ticket.unresolved_requirements.length > 0 && (
          <ul className="waiting-requirements">
            {ticket.unresolved_requirements.map((requirement) => (
              <li key={requirement}>{requirement}</li>
            ))}
          </ul>
        )}
      <div className="waiting-card-actions">
        <Link className="text-link" to={`/tickets/${ticket.ticket_id}`}>
          Open ticket
          <ArrowRight size={13} />
        </Link>
        <button
          className="button button-primary"
          disabled={disabled || resume.isPending || !canResume}
          title={
            ticket.resume_blocked_reason ??
            (canResume
              ? "Continue with the current staff approval"
              : "A matching active approval is required")
          }
          onClick={() =>
            resume.mutate(undefined, {
              onSuccess: () => navigate(`/tickets/${ticket.ticket_id}/trace`),
            })
          }
        >
          {resume.isPending ? (
            <LoaderCircle size={13} className="trace-spin" />
          ) : (
            <Play size={13} />
          )}
          Resume ticket
        </button>
      </div>
      {!canResume && ticket.resume_blocked_reason && (
        <p className="resume-blocked-reason">
          <LockKeyhole size={12} />
          {ticket.resume_blocked_reason}
        </p>
      )}
      {resume.isError && (
        <p className="approval-inline-error" role="alert">
          {resume.error.message}
        </p>
      )}
    </article>
  );
}

// Confirmation names the exact staged record and amount; approving does not itself issue a refund.
function ApprovalDialog({
  approval,
  pending,
  blocked,
  error,
  onClose,
  onApprove,
}: {
  approval: StagedApproval;
  pending: boolean;
  blocked: boolean;
  error: string | null;
  onClose: () => void;
  onApprove: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const latest = useRef({ pending, onClose });
  latest.current = { pending, onClose };
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    cancel.current?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !latest.current.pending)
        latest.current.onClose();
      if (event.key === "Tab") {
        const buttons = Array.from(
          dialog.current?.querySelectorAll<HTMLButtonElement>(
            "button:not(:disabled)",
          ) ?? [],
        );
        if (!buttons.length) {
          event.preventDefault();
          dialog.current?.focus();
          return;
        }
        const first = buttons[0],
          last = buttons.at(-1);
        if (
          event.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === dialog.current)
        ) {
          event.preventDefault();
          last?.focus();
        }
        if (
          !event.shiftKey &&
          (document.activeElement === last ||
            document.activeElement === dialog.current)
        ) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", keyboard);
    return () => {
      document.removeEventListener("keydown", keyboard);
      previous?.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop" onClick={() => !pending && onClose()}>
      <div
        ref={dialog}
        tabIndex={-1}
        className="approval-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-dialog-title"
        aria-describedby="approval-dialog-description"
        onClick={(event) => event.stopPropagation()}
      >
        <span className="approval-dialog-icon">
          <ShieldCheck size={25} />
        </span>
        <h2 id="approval-dialog-title">Approve this refund?</h2>
        <p id="approval-dialog-description">
          Activate this exact staged staff approval. The ticket must be resumed
          separately before a simulated refund can be issued.
        </p>
        <dl className="approval-confirm-record">
          <div>
            <dt>Approval</dt>
            <dd>{approval.approval_id}</dd>
          </div>
          <div>
            <dt>Order</dt>
            <dd>{approval.order_id}</dd>
          </div>
          <div>
            <dt>Amount</dt>
            <dd>{money(approval.amount_cents)}</dd>
          </div>
          <div>
            <dt>Recorded staff member</dt>
            <dd>{approval.approved_by}</dd>
          </div>
        </dl>
        {error && (
          <p className="approval-inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button
            ref={cancel}
            className="button button-secondary"
            disabled={pending}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="button button-primary"
            disabled={pending || blocked}
            onClick={onApprove}
          >
            {pending && <LoaderCircle size={14} className="trace-spin" />}
            Activate approval
          </button>
        </div>
      </div>
    </div>
  );
}

// Staff controls operate through the adapter; the agent never receives an approval-creation tool.
export function ApprovalsPage() {
  const query = useApprovals();
  const health = useHealth();
  const approve = useApproveApproval();
  const [selected, setSelected] = useState<StagedApproval | null>(null);
  const [search, setSearch] = useState("");
  const [matchingOnly, setMatchingOnly] = useState(true);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    document.title = "Staff approvals · OP-US";
  }, []);
  const busy = Boolean(health.data?.running);
  const unavailable = health.isError || !health.data?.capabilities.approve;
  const waiting = query.data?.waiting ?? [];
  const staged = query.data?.staged ?? [];
  const filtered = staged.filter(
    (approval) =>
      (!matchingOnly || approval.unblocks.length > 0) &&
      `${approval.approval_id} ${approval.order_id} ${approval.approved_by} ${approval.reason}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">HUMAN IN THE LOOP</div>
          <h1>
            Staff approvals<span className="heading-dot">.</span>
          </h1>
          <p>Review and activate staff refund approvals.</p>
        </div>
        <Link className="button button-secondary" to="/tickets/T-0039">
          Open approval demo
          <ArrowUpRight size={15} />
        </Link>
      </div>
      <div className="approval-overview">
        <div>
          <span className="approval-stat-icon">
            <Clock3 size={18} />
          </span>
          <div>
            <strong>{query.isPending ? "—" : waiting.length}</strong>
            <span>Waiting tickets</span>
          </div>
        </div>
        <div>
          <span className="approval-stat-icon">
            <FileCheck2 size={18} />
          </span>
          <div>
            <strong>
              {query.isPending
                ? "—"
                : staged.filter((item) => !item.active).length}
            </strong>
            <span>Staged approvals</span>
          </div>
        </div>
        <div>
          <span className="approval-stat-icon">
            <CircleCheck size={18} />
          </span>
          <div>
            <strong>
              {query.isPending
                ? "—"
                : staged.filter((item) => item.active).length}
            </strong>
            <span>Active staged approvals</span>
          </div>
        </div>
      </div>
      <div className="approval-staff-note">
        <UserRound size={17} />
        <p>
          <strong>Staff action.</strong> Activate the exact order-and-amount
          approval, then resume the ticket to continue processing.
        </p>
      </div>
      {busy && (
        <div className="approval-busy" role="status">
          <LoaderCircle size={15} className="trace-spin" />A ticket is running.
          Staff actions are paused until it finishes.
        </div>
      )}
      {notice && (
        <div className="approval-success" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      {query.isPending ? (
        <div className="panel">
          <div className="trace-loading" role="status">
            <LoaderCircle size={18} className="trace-spin" />
            Loading staff approvals…
          </div>
        </div>
      ) : query.isError ? (
        <div className="panel">
          <EmptyState
            error
            title="Approvals couldn’t load"
            retry={() => {
              void query.refetch();
              void health.refetch();
            }}
          >
            {query.error.message}
          </EmptyState>
        </div>
      ) : (
        <>
          <section
            className="waiting-section"
            aria-label="Tickets awaiting approval"
          >
            <div className="approval-section-heading">
              <h2>
                Waiting for a decision<span>{waiting.length}</span>
              </h2>
            </div>
            {waiting.length ? (
              <div className="waiting-ticket-grid">
                {waiting.map((ticket) => (
                  <WaitingCard
                    key={ticket.ticket_id}
                    ticket={ticket}
                    disabled={
                      busy ||
                      health.isError ||
                      !health.data?.capabilities.resume
                    }
                  />
                ))}
              </div>
            ) : (
              <div className="approval-no-waiting">
                <CircleCheck size={20} />
                <div>
                  <strong>No tickets are waiting for approval.</strong>
                  <p>Run the demo ticket to create an approval requirement.</p>
                </div>
                <Link className="text-link" to="/tickets/T-0039">
                  Open T-0039
                  <ArrowRight size={13} />
                </Link>
              </div>
            )}
          </section>
          <section
            className="panel staged-approval-panel"
            aria-label="Staged staff approvals"
          >
            <div className="approval-section-heading">
              <div>
                <h2>
                  {matchingOnly ? "Matching approvals" : "All staged approvals"}
                  <span>{filtered.length}</span>
                </h2>
                <p>
                  {matchingOnly
                    ? "Records matching tickets that are waiting for approval."
                    : "All prepared staff records, including those without a waiting ticket."}
                </p>
              </div>
              <span className="approval-record-source">
                {filtered.length} of {staged.length} records
              </span>
            </div>
            <div className="approval-toolbar">
              <label className="search-field">
                <Search size={16} />
                <input
                  aria-label="Search approvals"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search approval, order, or staff ID…"
                />
              </label>
              <label className="approval-match-filter">
                <input
                  type="checkbox"
                  checked={matchingOnly}
                  onChange={(event) => setMatchingOnly(event.target.checked)}
                />
                Matching waiting tickets
              </label>
            </div>
            {!filtered.length ? (
              <EmptyState title="No matching approvals">
                {matchingOnly ? (
                  <>
                    No staged approval matches the current waiting tickets and
                    search.
                    <br />
                    <button
                      className="text-link"
                      onClick={() => {
                        setMatchingOnly(false);
                        setSearch("");
                      }}
                    >
                      Show all staged approvals
                    </button>
                  </>
                ) : (
                  <>
                    Try a different approval, order, or staff ID.
                    {search && (
                      <>
                        <br />
                        <button
                          className="text-link"
                          onClick={() => setSearch("")}
                        >
                          Clear search
                        </button>
                      </>
                    )}
                  </>
                )}
              </EmptyState>
            ) : (
              <div className="staged-approval-grid">
                {filtered.map((approval) => (
                  <article
                    className={`staged-approval-card ${approval.active ? "approval-card-active" : ""}`}
                    key={approval.approval_id}
                    aria-labelledby={`approval-${approval.approval_id}`}
                  >
                    <div className="staged-approval-title">
                      <h3 id={`approval-${approval.approval_id}`}>
                        {approval.approval_id}
                      </h3>
                      <span
                        className={`approval-active-badge ${approval.active ? "active" : ""}`}
                      >
                        {approval.active ? (
                          <Check size={11} />
                        ) : (
                          <Clock3 size={11} />
                        )}
                        {approval.active ? "Active" : "Staged"}
                      </span>
                    </div>
                    <div className="approval-order-amount">
                      <span>{approval.order_id}</span>
                      <strong>{money(approval.amount_cents)}</strong>
                    </div>
                    <p className="staged-approval-reason">{approval.reason}</p>
                    <div className="approval-record-meta">
                      <span>
                        <UserRound size={12} />
                        {approval.approved_by}
                      </span>
                      <span>{date(approval.created_at)}</span>
                    </div>
                    {approval.unblocks.length > 0 && (
                      <div className="approval-unblocks">
                        <span>Matching waiting tickets</span>
                        {approval.unblocks.map((ticketId) => (
                          <Link key={ticketId} to={`/tickets/${ticketId}`}>
                            {ticketId}
                            <ArrowUpRight size={11} />
                          </Link>
                        ))}
                      </div>
                    )}
                    <div className="staged-approval-actions">
                      <span>
                        {approval.used_by_refund
                          ? `Used by ${approval.used_by_refund}`
                          : approval.active
                            ? "Available in the active store"
                            : "Not available to the agent"}
                      </span>
                      <button
                        className={`button ${approval.active ? "button-secondary" : "button-primary"}`}
                        disabled={
                          approval.active ||
                          busy ||
                          unavailable ||
                          approve.isPending
                        }
                        onClick={() => {
                          approve.reset();
                          setNotice("");
                          setSelected(approval);
                        }}
                      >
                        {approval.active ? (
                          <>
                            <Check size={13} />
                            Already active
                          </>
                        ) : (
                          <>
                            <ShieldCheck size={13} />
                            Approve
                          </>
                        )}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      )}
      {selected && (
        <ApprovalDialog
          approval={selected}
          pending={approve.isPending}
          blocked={busy || unavailable}
          error={
            approve.error?.message ??
            (busy
              ? "Wait for the running ticket to finish before approving."
              : null)
          }
          onClose={() => setSelected(null)}
          onApprove={() =>
            approve.mutate(selected.approval_id, {
              onSuccess: (result) => {
                setNotice(
                  result.already_active
                    ? `${selected.approval_id} was already active. No duplicate approval was created.`
                    : `${selected.approval_id} activated. Resume the matching ticket to continue.`,
                );
                setSelected(null);
              },
            })
          }
        />
      )}
    </>
  );
}
