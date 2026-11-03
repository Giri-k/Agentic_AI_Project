import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  Activity,
  BookOpenText,
  Check,
  ChevronDown,
  Clock3,
  CreditCard,
  FileCheck2,
  Info,
  LockKeyhole,
  LoaderCircle,
  Mail,
  MessageSquare,
  Package,
  Play,
  RotateCcw,
  ShieldCheck,
  Truck,
  Undo2,
} from "lucide-react";
import {
  ApiError,
  useHealth,
  useResumeTicket,
  useStartRun,
  useTicket,
} from "../api";
import { TraceViewer } from "../components/TraceViewer";
import { GraderPanel } from "../components/GraderPanel";
import {
  Avatar,
  ChannelLabel,
  EmptyState,
  PolicyChip,
  PriorityBadge,
  ReceiptChip,
  SectionTitle,
  StatusBadge,
} from "../components";
import { date, humanize, money } from "../format";
import type { Order, TicketDetail } from "../types";

// Render only owned records returned by the API; inaccessible orders never trigger extra lookups.
function OrderCard({ order, first }: { order: Order; first: boolean }) {
  return (
    <details className="order-card" open={first}>
      <summary>
        <span className="order-heading-icon">
          <Package size={18} />
        </span>
        <span>
          <strong>{order.order_id}</strong>
          <small>Placed {date(order.placed_at)}</small>
        </span>
        <span className={`order-status order-status-${order.status}`}>
          {humanize(order.status)}
        </span>
        <ChevronDown size={15} className="order-chevron" />
      </summary>
      <div className="order-content">
        <div className="order-items">
          {order.items.map((item, index) => (
            <div className="order-item" key={`${item.name}-${index}`}>
              <div className="product-icon">
                <Package size={24} strokeWidth={1.2} />
              </div>
              <div>
                <strong>{item.name}</strong>
                <span>
                  Quantity {item.qty}
                  {item.final_sale ? " · Final sale" : ""}
                </span>
              </div>
              <strong>{money(item.unit_cents * item.qty)}</strong>
            </div>
          ))}
        </div>
        <div className="order-total">
          <span>Order total</span>
          <strong>{money(order.total_cents)}</strong>
        </div>
        <div className="record-section">
          <h3>
            <Truck size={14} />
            Delivery
          </h3>
          {order.shipment ? (
            <>
              <div className="record-line">
                <span>{order.shipment.carrier}</span>
                <span className="record-state">
                  <i />
                  {humanize(order.shipment.status)}
                </span>
              </div>
              <div className="tracking-id">{order.shipment.tracking}</div>
              <div className="record-line">
                <span>Promised by</span>
                <strong>{date(order.shipment.promised_by)}</strong>
              </div>
              {order.shipment.delivered_at && (
                <div className="record-line">
                  <span>Delivered</span>
                  <strong>{date(order.shipment.delivered_at)}</strong>
                </div>
              )}
            </>
          ) : (
            <p className="record-empty">No shipment recorded.</p>
          )}
        </div>
        {order.return && (
          <div className="record-section">
            <h3>
              <Undo2 size={14} />
              Return
            </h3>
            <div className="record-line">
              <span>Received</span>
              <strong>{date(order.return.received_at)}</strong>
            </div>
            <div className="record-line">
              <span>Condition</span>
              <strong>
                {order.return.condition
                  ? humanize(order.return.condition)
                  : "Not recorded"}
              </strong>
            </div>
          </div>
        )}
        <div className="record-section">
          <h3>
            <CreditCard size={14} />
            Payments
          </h3>
          {order.payments.length ? (
            order.payments.map((payment) => (
              <div className="payment-row" key={payment.payment_id}>
                <div>
                  <strong>{humanize(payment.method)}</strong>
                  <small>
                    {payment.payment_id} · {humanize(payment.status)}
                  </small>
                </div>
                <strong>{money(payment.amount_cents)}</strong>
              </div>
            ))
          ) : (
            <p className="record-empty">No payments recorded.</p>
          )}
        </div>
        <div className="record-section">
          <h3>
            <ShieldCheck size={14} />
            Staff approvals
            <span className="small-count">{order.approvals.length}</span>
          </h3>
          {order.approvals.length ? (
            order.approvals.map((approval) => (
              <div className="approval-record" key={approval.approval_id}>
                <div className="record-line">
                  <strong>{approval.approval_id}</strong>
                  <strong>{money(approval.amount_cents)}</strong>
                </div>
                <p>{approval.reason}</p>
                <small>
                  {approval.approved_by}
                  {approval.used_by_refund
                    ? ` · Used by ${approval.used_by_refund}`
                    : " · Unused"}
                </small>
              </div>
            ))
          ) : (
            <div className="approval-empty">
              <LockKeyhole size={14} />
              <span>No active approval on this order.</span>
            </div>
          )}
        </div>
        <div className="record-section refund-section">
          <h3>
            <FileCheck2 size={14} />
            Refunds<span className="small-count">{order.refunds.length}</span>
          </h3>
          {order.refunds.map((refund) => (
            <div className="payment-row" key={refund.refund_id}>
              <div>
                <strong>{refund.refund_id}</strong>
                <small>
                  {humanize(refund.status)} · {refund.approval_id}
                </small>
              </div>
              <strong>{money(refund.amount_cents)}</strong>
            </div>
          ))}
          {!order.refunds.length && (
            <p className="record-empty">No refunds issued.</p>
          )}
          <div className="refundable-total">
            <span>Remaining refundable</span>
            <strong>{money(order.remaining_refundable_cents)}</strong>
          </div>
        </div>
      </div>
    </details>
  );
}

// Original customer messages and outbox receipts are shown separately without duplicating the thread.
function Conversation({ detail }: { detail: TicketDetail }) {
  const ticket = detail.ticket;
  return (
    <section className="panel conversation-panel">
      <SectionTitle
        icon={<MessageSquare size={18} />}
        title="Conversation"
        aside={
          <span className="section-count">
            {ticket.messages.length + detail.sent_messages.length}{" "}
            {ticket.messages.length + detail.sent_messages.length === 1
              ? "message"
              : "messages"}
          </span>
        }
      />
      <div className="conversation-content">
        <div className="conversation-day">
          <span />
          {date(ticket.created_at)}
          <span />
        </div>
        {ticket.messages.map((message, index) => (
          <article
            className={`conversation-message ${message.role === "customer" ? "" : "agent-message"}`}
            key={index}
          >
            <Avatar
              name={
                message.role === "customer" ? detail.customer.name : "OP US"
              }
            />
            <div className="message-content">
              <div className="message-meta">
                <strong>
                  {message.role === "customer"
                    ? detail.customer.name
                    : "Support agent"}
                </strong>
                <span>
                  {message.role === "customer" ? "Customer" : "Agent"}
                </span>
                <time>{date(message.at, true)}</time>
              </div>
              <div className="message-body">{message.body}</div>
              <div className="message-source">
                <ChannelLabel channel={ticket.channel} />
                <span>Original ticket message</span>
              </div>
            </div>
          </article>
        ))}
        {detail.sent_messages.map((message) => (
          <article
            className="conversation-message agent-message"
            key={message.message_id}
          >
            <span className="agent-avatar">
              <LayersMark />
            </span>
            <div className="message-content">
              <div className="message-meta">
                <strong>Support agent</strong>
                <span>Sent · {message.message_id}</span>
                <time>{date(message.sent_at, true)}</time>
              </div>
              <div className="message-body">{message.body}</div>
              <div className="message-citations">
                {message.citations.map((id) => (
                  <PolicyChip key={id} id={id} />
                ))}
              </div>
            </div>
          </article>
        ))}
        {!detail.sent_messages.length && (
          <div className="no-reply">
            <span>
              <MessageSquare size={22} strokeWidth={1.4} />
            </span>
            <h3>No replies sent in this preview</h3>
            <p>
              The recorded conversation is ready to review.
              <br />
              Run the agent to add a reply and its receipt.
            </p>
          </div>
        )}
      </div>
      <div className="conversation-footer">
        <LockKeyhole size={13} />
        Read-only conversation · Messages are simulated
      </div>
    </section>
  );
}

// A simple local mark distinguishes support messages without fetching remote avatars.
function LayersMark() {
  return <ShieldCheck size={17} />;
}

// Actual result evidence stays together; no success card is synthesized from expected labels.
function ResultCard({ detail }: { detail: TicketDetail }) {
  const result = detail.state.last_result;
  if (!result) return null;
  return (
    <section
      className="panel result-panel"
      aria-label="Ticket result evidence"
      data-status={result.status}
    >
      <SectionTitle
        icon={<FileCheck2 size={18} />}
        title="Ticket result"
        aside={<StatusBadge status={result.status} />}
      />
      <div className="result-content">
        <div className="result-evidence">
          <div>
            <span className="field-label">DECISION</span>
            <strong>{humanize(result.stop_reason)}</strong>
          </div>
          <div>
            <span className="field-label">POLICY EVIDENCE</span>
            <div className="chip-list">
              {result.citations.length ? (
                result.citations.map((id) => <PolicyChip id={id} key={id} />)
              ) : (
                <span className="muted">No citations recorded</span>
              )}
            </div>
          </div>
          <div>
            <span className="field-label">
              {result.receipt_ids.some((id) => id.startsWith("RF-"))
                ? "REFUND & ACTION RECEIPTS"
                : "ACTION RECEIPTS"}
            </span>
            <div className="chip-list">
              {result.receipt_ids.length ? (
                [...result.receipt_ids]
                  .sort(
                    (a, b) =>
                      Number(b.startsWith("RF-")) - Number(a.startsWith("RF-")),
                  )
                  .map((id) => (
                    <span
                      key={id}
                      className={
                        id.startsWith("RF-")
                          ? "refund-receipt-highlight"
                          : undefined
                      }
                    >
                      <ReceiptChip id={id} />
                    </span>
                  ))
              ) : (
                <span className="muted">No receipts recorded</span>
              )}
            </div>
          </div>
        </div>
        {result.response && (
          <p className="result-response">{result.response}</p>
        )}
        {result.unresolved_requirements.length > 0 && (
          <div className="open-requirements">
            <strong>Still needed</strong>
            <ul>
              {result.unresolved_requirements.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

// Execution updates the same public detail record; all actions stay behind the adapter boundary.
export function TicketPage() {
  const { id = "" } = useParams();
  const query = useTicket(id);
  const health = useHealth();
  const start = useStartRun(id);
  const resume = useResumeTicket(id);
  const location = useLocation();
  const previous = (location.state as { from?: string } | null)?.from ?? "/";
  const [traceOpen, setTraceOpen] = useState(false);
  useEffect(() => {
    document.title = `${id} · OP-US`;
    start.reset();
    resume.reset();
    setTraceOpen(false);
  }, [id]);
  useEffect(() => {
    if (query.data?.running) setTraceOpen(true);
  }, [query.data?.running]);
  if (query.isPending)
    return (
      <>
        <Link className="back-link" to={previous}>
          <ArrowLeft size={15} />
          Back to inbox
        </Link>
        <div
          className="detail-skeleton"
          role="status"
          aria-label="Loading ticket"
        >
          <span />
          <span />
          <span />
        </div>
      </>
    );
  if (query.isError)
    return (
      <>
        <Link className="back-link" to={previous}>
          <ArrowLeft size={15} />
          Back to inbox
        </Link>
        <div className="panel">
          <EmptyState
            error
            title={
              query.error instanceof ApiError && query.error.status === 404
                ? "Ticket not found"
                : "This ticket couldn’t load"
            }
            retry={() => {
              void query.refetch();
            }}
          >
            {query.error.message}
          </EmptyState>
        </div>
      </>
    );
  const detail = query.data;
  const ticket = detail.ticket;
  const execution = detail.execution;
  const requestedSystem = start.variables;
  const pending = start.isPending || resume.isPending;
  const busy = pending || detail.running || Boolean(health.data?.running);
  const canRun = Boolean(
    !busy &&
    !health.isError &&
    health.data?.capabilities.run &&
    execution?.can_run,
  );
  const canResume = Boolean(
    !busy &&
    !health.isError &&
    health.data?.capabilities.resume &&
    execution?.can_resume,
  );
  const actionError = start.error?.message ?? resume.error?.message;
  const blockingReason = detail.running
    ? "The agent is working. Follow each step in the trace below."
    : health.data?.running
      ? `Another run is in progress${health.data.active_ticket_id ? ` on ${health.data.active_ticket_id}` : ""}.`
      : !health.data?.capabilities.run
        ? "Execution is not available from this adapter."
        : execution?.can_resume
          ? "The matching staff approval is active. Resume this ticket to continue."
          : execution?.can_run
            ? null
            : [
                  "awaiting_approval",
                  "awaiting_customer",
                  "failed_safe",
                ].includes(detail.state.status)
              ? (execution?.resume_blocked_reason ??
                execution?.run_blocked_reason ??
                null)
              : (execution?.run_blocked_reason ?? null);
  return (
    <>
      <Link className="back-link" to={previous}>
        <ArrowLeft size={15} />
        Back to inbox
      </Link>
      <div className="detail-heading">
        <div>
          <div className="detail-id">
            <span>{ticket.ticket_id}</span>
            {id === "T-0039" && (
              <span className="demo-label">APPROVAL DEMO</span>
            )}
            <span className="detail-version">
              Version {detail.state.version}
            </span>
          </div>
          <h1>{ticket.subject}</h1>
          <div className="detail-metadata">
            <PriorityBadge priority={ticket.priority} />
            <span className="meta-divider" />
            <ChannelLabel channel={ticket.channel} />
            <span className="meta-divider" />
            <span>
              <Clock3 size={13} />
              Created {date(ticket.created_at)}
            </span>
          </div>
        </div>
        <StatusBadge status={detail.state.status} />
      </div>
      <section className="run-panel">
        <div className="run-overview">
          <span className="run-state-icon">
            {detail.running ? (
              <LoaderCircle size={21} className="spinning" />
            ) : (
              <ShieldCheck size={21} />
            )}
          </span>
          <div>
            <strong>
              {detail.running ? "Processing ticket" : "Ticket workspace"}
            </strong>
            <span>
              {detail.state.team ?? "Team unassigned"}
              <i />
              {detail.state.last_result?.category
                ? humanize(detail.state.last_result.category)
                : "Not classified yet"}
            </span>
          </div>
        </div>
        <div className="run-actions">
          <span className="read-only-label">
            <ShieldCheck size={12} />
            Real agent
          </span>
          <button
            className="button button-secondary"
            disabled={!canRun}
            onClick={() => {
              resume.reset();
              start.mutate("baseline");
            }}
            title={
              canRun
                ? "Run one retrieval and one model call"
                : (blockingReason ?? "Reset the workspace for a fresh comparison")
            }
          >
            {start.isPending && requestedSystem === "baseline"
              ? "Starting baseline…"
              : detail.running && execution?.system === "baseline"
                ? "Running baseline…"
                : "Run baseline"}
          </button>
          <button
            className="button button-secondary"
            disabled={!canResume}
            onClick={() => {
              start.reset();
              resume.mutate();
            }}
            title={
              canResume
                ? "Continue with the current approval and remaining lifetime budget"
                : (execution?.resume_blocked_reason ??
                  "This ticket is not ready to resume")
            }
          >
            <RotateCcw size={14} />
            {resume.isPending ? "Resuming…" : "Resume"}
          </button>
          <button
            className="button button-primary"
            disabled={!canRun}
            onClick={() => {
              resume.reset();
              start.mutate("agent");
            }}
            title={
              canRun
                ? "Start a real agent run (calls the model)"
                : (blockingReason ?? "Reset the workspace to start a fresh run")
            }
          >
            {(start.isPending && requestedSystem === "agent") ||
            (detail.running && execution?.system === "agent") ? (
              <LoaderCircle size={14} className="spinning" />
            ) : (
              <Play size={14} />
            )}
            {start.isPending && requestedSystem === "agent"
              ? "Starting agent…"
              : detail.running && execution?.system === "agent"
                ? "Running agent…"
                : "Run agent"}
          </button>
        </div>
      </section>
      {actionError && (
        <div className="execution-action-error" role="alert">
          <Info size={16} />
          <span>{actionError}</span>
          <button
            className="text-link"
            onClick={() => {
              start.reset();
              resume.reset();
              void query.refetch();
            }}
          >
            Dismiss and refresh
          </button>
        </div>
      )}
      {blockingReason && !actionError && (
        <div
          className="execution-guidance"
          role={detail.running ? "status" : undefined}
        >
          <Info size={14} />
          <span>{blockingReason}</span>
          {detail.state.status === "awaiting_approval" && (
            <Link to="/approvals" className="text-link">
              Review approvals
              <ArrowUpRight size={13} />
            </Link>
          )}
        </div>
      )}
      <ResultCard detail={detail} />
      <div className="detail-grid">
        <div className="detail-left">
          <Conversation detail={detail} />
          <Link className="policy-help-card" to="/policies">
            <span className="policy-help-icon">
              <BookOpenText size={22} />
            </span>
            <span>
              <strong>View store policies</strong>
              <small>
                Explore the store’s delivery, returns, billing, and safety
                rules.
              </small>
            </span>
            <ArrowUpRight size={20} />
          </Link>
          <div className="record-integrity">
            <Check size={13} />
            Actions shown here are simulated; project source records remain
            unchanged.
          </div>
        </div>
        <aside className="detail-right" aria-label="Customer and order records">
          <section className="panel customer-panel">
            <SectionTitle
              title="Customer"
              aside={
                <span className="customer-id">
                  {detail.customer.customer_id}
                </span>
              }
            />
            <div className="customer-profile">
              <Avatar name={detail.customer.name} large />
              <div>
                <strong>{detail.customer.name}</strong>
                <a href={`mailto:${detail.customer.email}`}>
                  <Mail size={12} />
                  {detail.customer.email}
                </a>
              </div>
            </div>
          </section>
          <div className="orders-title">
            <h2>
              Linked orders<span>{detail.orders.length}</span>
            </h2>
            <span>USD</span>
          </div>
          {detail.orders.map((order, index) => (
            <OrderCard key={order.order_id} order={order} first={index === 0} />
          ))}
          {detail.orders_not_on_this_account.map((orderId) => (
            <div className="inaccessible-order" key={orderId}>
              <LockKeyhole size={18} />
              <div>
                <strong>{orderId}</strong>
                <p>Not accessible on this customer’s account.</p>
              </div>
            </div>
          ))}
          {!detail.orders.length &&
            !detail.orders_not_on_this_account.length && (
              <div className="panel no-orders">
                <Package size={25} strokeWidth={1.4} />
                <h3>No linked orders</h3>
                <p>
                  This ticket has no linked order records. General policy
                  questions may still be answerable.
                </p>
              </div>
            )}
          <div className="store-clock">
            <Info size={14} />
            <span>
              Store records are evaluated as of{" "}
              <strong>{date(detail.as_of)}</strong>.
            </span>
          </div>
        </aside>
      </div>
      <GraderPanel key={id} ticketId={id} />
      {health.data?.capabilities.trace && (
        <details
          className="ticket-trace-disclosure"
          open={traceOpen}
          onToggle={(event) => setTraceOpen(event.currentTarget.open)}
        >
          <summary>
            <Activity size={17} />
            <strong>Execution trace</strong>
            <span>Tool calls, retries, and receipts</span>
            <ChevronDown size={16} />
          </summary>
          <TraceViewer ticketId={id} compact />
        </details>
      )}
    </>
  );
}
