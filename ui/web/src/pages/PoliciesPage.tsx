import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpenText,
  CalendarDays,
  FileText,
  Search,
} from "lucide-react";
import { ApiError, usePolicies, usePolicy } from "../api";
import { EmptyState } from "../components";
import { date } from "../format";

const SECTION_TITLES = {
  S1: "Scope",
  S2: "Conditions",
  S3: "Policy rules",
  S4: "Exceptions",
} as const;

// Browse the actual versioned policy corpus; policy text is never supplied by an external service.
export function PoliciesPage() {
  const query = usePolicies();
  const [search, setSearch] = useState("");
  useEffect(() => {
    document.title = "Policy library · OP-US";
  }, []);
  const policies = (query.data ?? []).filter((policy) =>
    `${policy.policy_id} ${policy.title}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">THE STORE RULEBOOK</div>
          <h1>
            Policy library<span className="heading-dot">.</span>
          </h1>
          <p>The evidence behind every support decision.</p>
        </div>
        <span className="library-count">
          <BookOpenText size={18} />
          {query.data?.length ?? "—"} versioned policies
        </span>
      </div>
      <div className="policy-toolbar">
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search policies"
            placeholder="Search by policy ID or title…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <span>Source versions are shown as recorded.</span>
      </div>
      {query.isPending ? (
        <div
          className="policy-grid"
          role="status"
          aria-label="Loading policies"
        >
          {Array.from({ length: 6 }, (_, index) => (
            <div className="policy-card policy-skeleton" key={index} />
          ))}
        </div>
      ) : query.isError ? (
        <div className="panel">
          <EmptyState
            error
            title="The policy library couldn’t load"
            retry={() => {
              void query.refetch();
            }}
          >
            {query.error.message}
          </EmptyState>
        </div>
      ) : !policies.length ? (
        <div className="panel">
          <EmptyState title="No matching policies">
            Try a different title or policy ID.
          </EmptyState>
        </div>
      ) : (
        <div className="policy-grid">
          {policies.map((policy) => (
            <Link
              className="policy-card"
              key={policy.policy_id}
              to={`/policies/${policy.policy_id}`}
            >
              <div>
                <span className="document-icon">
                  <FileText size={20} />
                </span>
                <span className="policy-version">v{policy.version}</span>
              </div>
              <span className="policy-card-id">{policy.policy_id}</span>
              <h2>{policy.title}</h2>
              <span className="policy-card-footer">
                Read policy
                <ArrowUpRight size={16} />
              </span>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

// Render plain policy sections safely while preserving paragraphs and original wording.
export function PolicyPage() {
  const { id = "" } = useParams();
  const query = usePolicy(id);
  useEffect(() => {
    document.title = `${id} · Policy library · OP-US`;
  }, [id]);
  return (
    <>
      <Link className="back-link" to="/policies">
        <ArrowLeft size={15} />
        Back to policy library
      </Link>
      {query.isPending ? (
        <div
          className="detail-skeleton"
          role="status"
          aria-label="Loading policy"
        >
          <span />
          <span />
          <span />
        </div>
      ) : query.isError ? (
        <div className="panel">
          <EmptyState
            error
            title={
              query.error instanceof ApiError && query.error.status === 404
                ? "Policy not found"
                : "This policy couldn’t load"
            }
            retry={() => {
              void query.refetch();
            }}
          >
            {query.error.message}
          </EmptyState>
        </div>
      ) : (
        <article className="policy-document">
          <header>
            <div className="document-heading-icon">
              <BookOpenText size={27} />
            </div>
            <div className="detail-id">
              <span>{query.data.policy_id}</span>
              <span className="policy-version">v{query.data.version}</span>
            </div>
            <h1>{query.data.title}</h1>
            <div className="policy-effective">
              <CalendarDays size={14} />
              Effective {date(query.data.effective_date)}
              <span>Store policy</span>
            </div>
          </header>
          <div className="policy-sections">
            {Object.entries(SECTION_TITLES).map(([key, title]) => (
              <section key={key}>
                <div className="policy-section-label">{key}</div>
                <div>
                  <h2>{title}</h2>
                  <p>
                    {query.data.sections[key as keyof typeof SECTION_TITLES] ||
                      "No additional text recorded."}
                  </p>
                </div>
              </section>
            ))}
          </div>
          <footer>
            <FileText size={14} />
            Read-only source policy · {query.data.policy_id} v
            {query.data.version}
          </footer>
        </article>
      )}
    </>
  );
}
