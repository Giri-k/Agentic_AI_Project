import { useEffect, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Link,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowDownUp,
  ArrowRight,
  ArrowUpRight,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CircleDashed,
  Clock3,
  Inbox,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { useTickets } from "../api";
import {
  Avatar,
  ChannelLabel,
  EmptyState,
  PriorityBadge,
  StatusBadge,
  TableSkeleton,
} from "../components";
import { date, STATUS_LABELS } from "../format";
import type { Status } from "../types";

const priorities = { critical: 0, high: 1, medium: 2, low: 3 };
const statusOptions = Object.keys(STATUS_LABELS) as Status[];
const PAGE_SIZE = 15;

// Filters and sorting live in the URL so inbox state survives a trip to a ticket.
export function InboxPage() {
  const query = useTickets();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    document.title = "Support inbox · OP-US";
  }, []);
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    next.delete("page");
    setParams(next, { replace: true });
  };
  const tickets = query.data ?? [];
  const counts = Object.fromEntries(
    statusOptions.map((status) => [
      status,
      tickets.filter((ticket) => ticket.status === status).length,
    ]),
  ) as Record<Status, number>;
  const filtered = useMemo(() => {
    const needle = (params.get("q") ?? "").trim().toLowerCase();
    const sort = params.get("sort") ?? "priority";
    return tickets
      .filter(
        (ticket) =>
          ["status", "team", "priority", "channel", "split"].every(
            (key) =>
              !params.get(key) ||
              ticket[key as keyof typeof ticket] === params.get(key),
          ) &&
          (!needle ||
            [ticket.ticket_id, ticket.subject, ticket.customer_name].some(
              (value) => value.toLowerCase().includes(needle),
            )),
      )
      .sort((a, b) =>
        sort === "created"
          ? b.created_at.localeCompare(a.created_at) ||
            a.ticket_id.localeCompare(b.ticket_id)
          : sort === "id"
            ? a.ticket_id.localeCompare(b.ticket_id)
            : priorities[a.priority] - priorities[b.priority] ||
              a.ticket_id.localeCompare(b.ticket_id),
      );
  }, [tickets, params]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const page = Math.min(
    pageCount - 1,
    Math.max(0, Number.parseInt(params.get("page") ?? "0", 10) || 0),
  );
  const changePage = (nextPage: number) => {
    const next = new URLSearchParams(params);
    nextPage ? next.set("page", String(nextPage)) : next.delete("page");
    setParams(next);
    document
      .querySelector(".inbox-table")
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const hasFilters = [
    "q",
    "status",
    "team",
    "priority",
    "channel",
    "split",
  ].some((key) => params.has(key));
  const expandedFilters = params.get("filters") === "open";
  const stats = [
    {
      label: "All tickets",
      value: tickets.length,
      caption: "Across every channel",
      icon: Inbox,
      filter: "",
      color: "neutral",
    },
    {
      label: "New tickets",
      value: counts.new,
      caption: "Ready for a first look",
      icon: CircleDashed,
      filter: "new",
      color: "blue",
    },
    {
      label: "Awaiting approval",
      value: counts.awaiting_approval,
      caption: "A staff decision is needed",
      icon: ShieldCheck,
      filter: "awaiting_approval",
      color: "amber",
    },
    {
      label: "Resolved",
      value: counts.resolved,
      caption: `${counts.closed} customer-confirmed`,
      icon: CheckCheck,
      filter: "resolved",
      color: "green",
    },
  ];
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">CUSTOMER OPERATIONS</div>
          <h1>
            Support inbox<span className="heading-dot">.</span>
          </h1>
          <p>Review tickets, run the agent, and follow each decision.</p>
        </div>
        <div className="page-heading-actions">
          <Link className="button button-primary" to="/tickets/T-0039">
            Open demo ticket
            <ArrowUpRight size={16} />
          </Link>
        </div>
      </div>
      <section className="stats-grid" aria-label="Ticket counts">
        {stats.map((stat) => (
          <button
            className={`stat-card ${params.get("status") === stat.filter && stat.filter ? "stat-selected" : ""}`}
            key={stat.label}
            onClick={() => update("status", stat.filter)}
          >
            <div className="stat-label">
              {stat.label}
              <span className={`stat-icon stat-icon-${stat.color}`}>
                <stat.icon size={17} />
              </span>
            </div>
            <div className="stat-value">
              {query.isPending ? "—" : query.isError ? "—" : stat.value}
              <span>{stat.caption}</span>
            </div>
          </button>
        ))}
      </section>
      <div className="preview-note">
        <span className="preview-note-icon">
          <ShieldCheck size={17} />
        </span>
        <p>
          <strong>Real agent.</strong> Runs call the model; refunds and emails
          stay in the simulated store. Reset to replay a demo.
        </p>
      </div>
      <section className="inbox-panel" aria-label="Ticket inbox">
        <div className="inbox-tabs">
          <div className="ticket-tabs">
            <button
              className={!params.get("split") ? "active" : ""}
              onClick={() => update("split", "")}
            >
              All tickets<span>{tickets.length || "—"}</span>
            </button>
            <button
              className={params.get("split") === "dev" ? "active" : ""}
              onClick={() => update("split", "dev")}
            >
              Development
            </button>
            <button
              className={params.get("split") === "test" ? "active" : ""}
              onClick={() => update("split", "test")}
            >
              Test
            </button>
            <button
              className={params.get("split") === "demo" ? "active" : ""}
              onClick={() => update("split", "demo")}
            >
              <span className="tiny-amber-dot" />
              Demo
            </button>
          </div>
          <span className="dataset-caption">PROJECT DATASET</span>
        </div>
        <div className="table-toolbar">
          <label className="search-field">
            <Search size={17} />
            <input
              aria-label="Search tickets"
              placeholder="Search tickets, subjects, or customers…"
              value={params.get("q") ?? ""}
              onChange={(event) => update("q", event.target.value)}
            />
            {params.get("q") && (
              <button aria-label="Clear search" onClick={() => update("q", "")}>
                <X size={14} />
              </button>
            )}
            <span className="search-hint">{tickets.length || "—"}</span>
          </label>
          <div className="toolbar-controls">
            <label className="sort-control status-select">
              <select
                aria-label="Status"
                value={params.get("status") ?? ""}
                onChange={(event) => update("status", event.target.value)}
              >
                <option value="">All statuses</option>
                {statusOptions.map((status) => (
                  <option key={status} value={status}>
                    {STATUS_LABELS[status]} ({counts[status]})
                  </option>
                ))}
              </select>
            </label>
            <button
              className={`button button-secondary filter-button ${expandedFilters ? "selected" : ""}`}
              onClick={() => update("filters", expandedFilters ? "" : "open")}
              aria-expanded={expandedFilters}
              aria-controls="ticket-filters"
            >
              <SlidersHorizontal size={15} />
              Filters{hasFilters && <span className="filter-dot" />}
            </button>
            <label className="sort-control">
              <ArrowDownUp size={14} />
              <select
                aria-label="Sort tickets"
                value={params.get("sort") ?? "priority"}
                onChange={(event) => update("sort", event.target.value)}
              >
                <option value="priority">Priority</option>
                <option value="created">Newest first</option>
                <option value="id">Ticket ID</option>
              </select>
            </label>
            {hasFilters && (
              <button
                className="button button-secondary"
                onClick={() => setParams({})}
              >
                <X size={13} />
                Clear
              </button>
            )}
          </div>
        </div>
        {expandedFilters && (
          <div className="expanded-filters" id="ticket-filters">
            {[
              {
                key: "team",
                label: "Team",
                options: ["Delivery", "Returns", "Billing", "Human Review"].map(
                  (value) => [value, value],
                ),
              },
              {
                key: "priority",
                label: "Priority",
                options: [
                  ["critical", "Critical"],
                  ["high", "High"],
                  ["medium", "Medium"],
                  ["low", "Low"],
                ],
              },
              {
                key: "channel",
                label: "Channel",
                options: [
                  ["email", "Email"],
                  ["chat", "Chat"],
                  ["phone", "Phone"],
                  ["social_media", "Social"],
                ],
              },
            ].map((filter) => (
              <label key={filter.key}>
                {filter.label}
                <select
                  value={params.get(filter.key) ?? ""}
                  onChange={(event) => update(filter.key, event.target.value)}
                >
                  <option value="">
                    {filter.key === "priority"
                      ? "All priorities"
                      : `All ${filter.label.toLowerCase()}s`}
                  </option>
                  {filter.options.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
        {query.isPending ? (
          <TableSkeleton />
        ) : query.isError ? (
          <EmptyState
            error
            title="The inbox couldn’t connect"
            retry={() => {
              void query.refetch();
              void client.invalidateQueries({ queryKey: ["health"] });
            }}
          >
            Check that the local API is running, then try again.
          </EmptyState>
        ) : !filtered.length ? (
          <EmptyState title="No tickets match these filters">
            <button className="text-link" onClick={() => setParams({})}>
              Clear the filters
            </button>{" "}
            to see the full inbox.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="inbox-table">
              <thead>
                <tr>
                  <th>Ticket / subject</th>
                  <th>Customer</th>
                  <th>Channel</th>
                  <th>Priority</th>
                  <th>Status</th>
                  <th>Team</th>
                  <th>Created</th>
                  <th aria-label="Open ticket" />
                </tr>
              </thead>
              <tbody>
                {visible.map((ticket) => (
                  <tr
                    key={ticket.ticket_id}
                    className={ticket.split === "demo" ? "demo-row" : ""}
                    onClick={(event) => {
                      if (!(event.target as HTMLElement).closest("a"))
                        navigate(`/tickets/${ticket.ticket_id}`, {
                          state: { from: location.pathname + location.search },
                        });
                    }}
                  >
                    <td>
                      <Link
                        className="ticket-subject"
                        to={`/tickets/${ticket.ticket_id}`}
                        state={{ from: location.pathname + location.search }}
                      >
                        {ticket.subject}
                      </Link>
                      <div className="ticket-subline">
                        <span>{ticket.ticket_id}</span>
                        <span className={`split-badge split-${ticket.split}`}>
                          {ticket.split === "dev"
                            ? "DEV"
                            : ticket.split.toUpperCase()}
                        </span>
                        {ticket.split === "demo" && (
                          <span className="demo-row-label">Approval demo</span>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="customer-cell">
                        <Avatar name={ticket.customer_name} />
                        <span>{ticket.customer_name}</span>
                      </div>
                    </td>
                    <td>
                      <ChannelLabel channel={ticket.channel} />
                    </td>
                    <td>
                      <PriorityBadge priority={ticket.priority} />
                    </td>
                    <td>
                      <StatusBadge status={ticket.status} />
                    </td>
                    <td>
                      <span className={ticket.team ? "team-name" : "muted"}>
                        {ticket.team ?? "Unassigned"}
                      </span>
                      {ticket.category && (
                        <small className="category-label">
                          {ticket.category.replaceAll("_", " ")}
                        </small>
                      )}
                    </td>
                    <td className="date-cell">
                      {date(ticket.created_at, true)}
                    </td>
                    <td>
                      <ChevronRight size={15} className="row-arrow" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!query.isPending && !query.isError && (
          <div className="table-footer">
            <span>
              {filtered.length ? (
                <>
                  <strong>
                    {page * PAGE_SIZE + 1}–
                    {Math.min((page + 1) * PAGE_SIZE, filtered.length)}
                  </strong>{" "}
                  of {filtered.length} tickets
                </>
              ) : (
                "0 tickets"
              )}
            </span>
            <div className="pagination">
              <button
                className="icon-button"
                disabled={page === 0}
                onClick={() => changePage(0)}
                aria-label="First page"
              >
                <ChevronsLeft size={15} />
              </button>
              <button
                className="icon-button"
                disabled={page === 0}
                onClick={() => changePage(page - 1)}
                aria-label="Previous page"
              >
                <ChevronLeft size={16} />
              </button>
              <span>
                Page <strong>{page + 1}</strong> of {pageCount}
              </span>
              <button
                className="icon-button"
                disabled={page + 1 >= pageCount}
                onClick={() => changePage(page + 1)}
                aria-label="Next page"
              >
                <ChevronRight size={16} />
              </button>
              <button
                className="icon-button"
                disabled={page + 1 >= pageCount}
                onClick={() => changePage(pageCount - 1)}
                aria-label="Last page"
              >
                <ChevronsRight size={15} />
              </button>
            </div>
          </div>
        )}
      </section>
      <div className="inbox-bottom-note">
        <Clock3 size={13} />
        <span>Dates follow the simulated store’s clock.</span>
        <Link to="/policies">
          Explore the policy library
          <ArrowRight size={13} />
        </Link>
      </div>
    </>
  );
}
