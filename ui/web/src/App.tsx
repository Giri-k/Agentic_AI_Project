import { useEffect, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenText,
  ChartNoAxesCombined,
  Check,
  ChevronRight,
  FlaskConical,
  Inbox,
  Layers3,
  LoaderCircle,
  Menu,
  RotateCcw,
  ShieldCheck,
  Type,
  X,
} from "lucide-react";
import { request, useHealth, usePolicies, useTickets } from "./api";
import { date } from "./format";
import { InboxPage } from "./pages/InboxPage";
import { TicketPage } from "./pages/TicketPage";
import { PoliciesPage, PolicyPage } from "./pages/PoliciesPage";
import { EmptyState } from "./components";
import { TraceViewer } from "./components/TraceViewer";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { DemoLabPage } from "./pages/DemoLabPage";
import { EvaluationPage } from "./pages/EvaluationPage";

// Shared navigation gives each workflow one clear entry point.
function Sidebar({ close }: { close: () => void }) {
  const { data: tickets } = useTickets();
  const { data: policies } = usePolicies();
  return (
    <>
      <Link className="brand" to="/" onClick={close}>
        <span className="brand-mark">
          <Layers3 size={24} strokeWidth={1.8} />
        </span>
        <span>
          OP<span className="brand-divider">—</span>US
          <small>SUPPORT WORKSPACE</small>
        </span>
      </Link>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        <NavLink
          to="/"
          end
          className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          onClick={close}
        >
          <Inbox size={18} />
          Inbox<span className="nav-count">{tickets?.length ?? "—"}</span>
        </NavLink>
        <NavLink
          to="/approvals"
          className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          onClick={close}
        >
          <ShieldCheck size={18} />
          Approvals
          <span className="nav-count">
            {tickets?.filter((ticket) => ticket.status === "awaiting_approval")
              .length ?? "—"}
          </span>
        </NavLink>
        <NavLink
          to="/policies"
          className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          onClick={close}
        >
          <BookOpenText size={18} />
          Policy library
          <span className="nav-count">{policies?.length ?? "—"}</span>
        </NavLink>
        <NavLink
          to="/demos"
          className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          onClick={close}
        >
          <FlaskConical size={18} />
          Demo lab
        </NavLink>
        <NavLink
          to="/eval"
          className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          onClick={close}
        >
          <ChartNoAxesCombined size={18} />
          Evaluation
        </NavLink>
      </nav>
      <div className="sidebar-bottom">
        <div className="local-indicator">
          <i />
          Local simulation
        </div>
        <p>Fictional customers. Simulated actions.</p>
      </div>
    </>
  );
}

// Confirmation is explicit: mock mode resets memory, real mode wipes runtime/ runs, traces and the outbox.
function ResetDialog({
  onClose,
  onReset,
  pending,
  error,
  real,
}: {
  onClose: () => void;
  onReset: () => void;
  pending: boolean;
  error: string | null;
  real: boolean;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const current = useRef({ onClose, pending });
  current.current = { onClose, pending };
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    cancel.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !current.current.pending)
        current.current.onClose();
      if (event.key === "Tab") {
        const buttons = Array.from(
          container.current?.querySelectorAll<HTMLButtonElement>(
            "button:not(:disabled)",
          ) ?? [],
        );
        if (!buttons.length) {
          event.preventDefault();
          container.current?.focus();
          return;
        }
        const first = buttons[0],
          last = buttons.at(-1);
        if (
          event.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === container.current)
        ) {
          event.preventDefault();
          last?.focus();
        }
        if (
          !event.shiftKey &&
          (document.activeElement === last ||
            document.activeElement === container.current)
        ) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop" onClick={() => !pending && onClose()}>
      <div
        ref={container}
        tabIndex={-1}
        className="reset-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reset-title"
        aria-describedby="reset-description"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-icon">
          <RotateCcw size={23} />
        </div>
        <h2 id="reset-title">
          {real ? "Reset the working store?" : "Reset the mock workspace?"}
        </h2>
        <p id="reset-description">
          {real
            ? "Delete every run, trace, checkpoint and sent reply in runtime/ and restore the dataset’s store. The dataset files and the embedding cache stay unchanged."
            : "Restore the original ticket and store snapshot. Only the preview’s in-memory state is reset; the project’s source files stay unchanged."}
        </p>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button
            ref={cancel}
            className="button button-secondary"
            onClick={onClose}
            disabled={pending}
          >
            Keep workspace
          </button>
          <button
            className="button button-primary"
            onClick={onReset}
            disabled={pending}
          >
            {pending ? "Resetting…" : "Reset workspace"}
          </button>
        </div>
      </div>
    </div>
  );
}

// The shell owns mode, reset and accessibility controls; pages read the same API cache.
export default function App() {
  const location = useLocation();
  const health = useHealth();
  const client = useQueryClient();
  const previouslyRunning = useRef<boolean | undefined>(undefined);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia?.("(max-width: 700px)").matches ?? false,
  );
  const navigation = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [largeType, setLargeType] = useState(
    () => localStorage.getItem("opus-large-type") === "true",
  );
  const reset = useMutation({
    mutationFn: () => request("/admin/reset", { method: "POST" }),
    onSuccess: async () => {
      await client.invalidateQueries();
      setResetOpen(false);
      setNotice(
        health.data?.adapter === "real"
          ? "Working store reset. Runs and traces were cleared."
          : "Mock workspace reset. Source data is unchanged.",
      );
    },
  });
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 700px)");
    if (!media) return;
    const change = () => {
      setIsMobile(media.matches);
      if (!media.matches) setMobileOpen(false);
    };
    change();
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    if (!mobileOpen || !isMobile) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    navigation.current?.querySelector<HTMLElement>("a")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileOpen(false);
        menuButton.current?.focus();
      }
      if (event.key === "Tab") {
        const links = Array.from(
          navigation.current?.querySelectorAll<HTMLElement>(
            ":is(a[href], button:not(:disabled))",
          ) ?? [],
        );
        const first = links[0],
          last = links.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", key);
    };
  }, [mobileOpen, isMobile]);
  useEffect(() => {
    document.documentElement.dataset.largeType = String(largeType);
    localStorage.setItem("opus-large-type", String(largeType));
  }, [largeType]);
  useEffect(() => {
    // A new route should open at its heading, even after scrolling through a long inbox.
    window.scrollTo(0, 0);
    document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [location.pathname]);
  useEffect(() => {
    // Completion can occur while the user is on another screen; refresh all saved evidence once.
    const running = health.data?.running;
    if (running === undefined) return;
    if (previouslyRunning.current === true && running === false) {
      for (const key of ["tickets", "ticket", "trace", "approvals", "expected"])
        void client.invalidateQueries({ queryKey: [key] });
    }
    previouslyRunning.current = running;
  }, [health.data?.running, client]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4500);
    return () => clearTimeout(timer);
  }, [notice]);
  const section = location.pathname.startsWith("/demos")
    ? "Demo lab"
    : location.pathname.startsWith("/eval")
      ? "Evaluation"
      : location.pathname.startsWith("/approvals")
        ? "Staff approvals"
        : location.pathname.endsWith("/trace")
          ? "Execution trace"
          : location.pathname.startsWith("/policies")
            ? "Policy library"
            : location.pathname.startsWith("/tickets/")
              ? "Ticket detail"
              : "Inbox";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {mobileOpen && (
        <button
          className="sidebar-overlay"
          aria-label="Dismiss navigation backdrop"
          tabIndex={-1}
          onClick={() => {
            setMobileOpen(false);
            menuButton.current?.focus();
          }}
        />
      )}
      <aside
        id="workspace-navigation"
        ref={navigation}
        className={`sidebar ${mobileOpen ? "sidebar-open" : ""}`}
        inert={isMobile && !mobileOpen}
        aria-hidden={isMobile && !mobileOpen ? true : undefined}
        role={isMobile && mobileOpen ? "dialog" : undefined}
        aria-modal={isMobile && mobileOpen ? true : undefined}
        aria-label="Workspace navigation"
      >
        <button
          className="icon-button mobile-menu navigation-close"
          aria-label="Close navigation"
          onClick={() => {
            setMobileOpen(false);
            menuButton.current?.focus();
          }}
        >
          <X size={18} />
        </button>
        <Sidebar close={() => setMobileOpen(false)} />
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="topbar-crumb">
            <button
              ref={menuButton}
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              aria-expanded={mobileOpen}
              aria-controls="workspace-navigation"
              onClick={() => setMobileOpen(true)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{section}</strong>
          </div>
          <div className="topbar-actions">
            {health.data?.running && (
              <Link
                className="global-running"
                aria-label={`Open live trace for ${health.data.active_ticket_id ?? "the active run"}`}
                to={
                  health.data.active_ticket_id
                    ? `/tickets/${health.data.active_ticket_id}/trace`
                    : "/"
                }
              >
                <LoaderCircle size={13} className="spinning" />
                Live trace
              </Link>
            )}
            <span className="store-date">
              Store date{" "}
              <strong>{health.data ? date(health.data.as_of) : "—"}</strong>
            </span>
            <span
              className={`mode-badge ${health.isError ? "mode-offline" : ""}`}
              title={
                health.data?.adapter === "mock"
                  ? "Local fixture preview. No model calls or real transactions."
                  : "Real agent: model calls go to gpt-5-nano. Refunds and emails are still simulated."
              }
            >
              <FlaskConical size={13} />
              {health.isError
                ? "OFFLINE"
                : (health.data?.adapter.toUpperCase() ?? "CONNECTING")}
            </span>
            <button
              className="button button-secondary workspace-reset"
              aria-label="Reset workspace"
              title={
                health.data?.running
                  ? "Wait for the current run to finish"
                  : health.data?.adapter === "real"
                    ? "Reset the working store"
                    : "Restore the mock workspace"
              }
              disabled={
                !health.data?.capabilities.reset ||
                health.isError ||
                health.data.running ||
                reset.isPending
              }
              onClick={() => {
                reset.reset();
                setResetOpen(true);
              }}
            >
              <RotateCcw size={15} />
              <span>Reset</span>
            </button>
            <button
              className={`icon-button type-toggle ${largeType ? "selected" : ""}`}
              onClick={() => setLargeType(!largeType)}
              aria-label="Large text"
              aria-pressed={largeType}
              title="Toggle large text for presenting"
            >
              <Type size={18} />
              <span>+</span>
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<InboxPage />} />
            <Route path="/tickets/:id" element={<TicketPage />} />
            <Route path="/tickets/:id/trace" element={<TracePage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/demos" element={<DemoLabPage />} />
            <Route path="/eval" element={<EvaluationPage />} />
            <Route path="/policies" element={<PoliciesPage />} />
            <Route path="/policies/:id" element={<PolicyPage />} />
            <Route
              path="*"
              element={
                <EmptyState title="This page isn’t here">
                  <Link to="/" className="text-link">
                    Return to the inbox
                  </Link>
                </EmptyState>
              }
            />
          </Routes>
        </main>
        <footer className="workspace-footer">
          <span>OP-US · Support with evidence.</span>
          <span>
            <ShieldCheck size={12} />
            Simulated store · No real messages or money
          </span>
        </footer>
      </div>
      {resetOpen && (
        <ResetDialog
          onClose={() => setResetOpen(false)}
          onReset={() => reset.mutate()}
          pending={reset.isPending}
          error={reset.error?.message ?? null}
          real={health.data?.adapter === "real"}
        />
      )}
      {notice && (
        <div className="toast" role="status">
          <Check size={17} />
          {notice}
          <button
            aria-label="Dismiss notification"
            onClick={() => setNotice("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

// The same viewer supports both the compact ticket panel and the full trace route.
function TracePage() {
  const { id = "" } = useParams();
  useEffect(() => {
    document.title = `${id} · Execution trace · OP-US`;
  }, [id]);
  return <TraceViewer ticketId={id} />;
}
