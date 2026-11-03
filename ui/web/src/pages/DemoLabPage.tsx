import { useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, FlaskConical, Info, ShieldCheck } from "lucide-react";
import { DEMOS } from "../demo-scenarios";

// The lab links to real tickets that each exercise one safeguard; opening one never starts a run.
export function DemoLabPage() {
  useEffect(() => {
    document.title = "Demo lab · OP-US";
  }, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">REAL AGENT, VISIBLE SAFEGUARDS</div>
          <h1>
            Demo lab<span className="heading-dot">.</span>
          </h1>
          <p>
            Run real tickets that exercise recovery, permissions and evidence.
          </p>
        </div>
        <span className="library-count">
          <FlaskConical size={17} />
          {DEMOS.length} SCENARIOS
        </span>
      </div>
      <div className="lab-note">
        <Info size={18} />
        <p>
          Open a scenario, review its context, then click{" "}
          <strong>Run agent</strong>. Runs call the real model, so the exact
          wording varies, but the safeguard should hold every time. Each ticket
          runs once; use <strong>Reset</strong> in the top bar before replaying
          it.
        </p>
      </div>
      <div className="demo-grid">
        {DEMOS.map((demo) => (
          <article className="demo-card" key={demo.ticketId}>
            <div className="demo-card-top">
              <span className="demo-ticket-id">{demo.ticketId}</span>
              <span className="demo-kind">REAL TICKET</span>
            </div>
            <h2>{demo.title}</h2>
            <p>{demo.description}</p>
            <div className="demo-inspect">
              <ShieldCheck size={15} />
              <span>{demo.inspect}</span>
            </div>
            <Link
              className="button button-secondary"
              to={`/tickets/${demo.ticketId}`}
            >
              Open ticket
              <ArrowUpRight size={14} />
            </Link>
          </article>
        ))}
      </div>
    </>
  );
}
