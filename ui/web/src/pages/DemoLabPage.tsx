import { useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, FlaskConical, Info, ShieldCheck } from "lucide-react";
import { DEMOS } from "../demo-scenarios";

// The lab links to explicit mock scenarios and explains the difference between recovery and forced tests.
export function DemoLabPage() {
  useEffect(() => {
    document.title = "Demo lab · OP-US";
  }, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">SCRIPTED, VISIBLE, REPEATABLE</div>
          <h1>
            Demo lab<span className="heading-dot">.</span>
          </h1>
          <p>
            Inspect recovery, permissions and evidence in the mock workspace.
          </p>
        </div>
        <span className="library-count">
          <FlaskConical size={17} />
          MOCK SCENARIOS
        </span>
      </div>
      <div className="lab-note">
        <Info size={18} />
        <p>
          Open a scenario, review its context, then click{" "}
          <strong>Run agent</strong>. Each ticket runs once per snapshot. Use{" "}
          <strong>Reset</strong> in the top bar before replaying it; links never
          reset your history.
        </p>
      </div>
      <div className="demo-grid">
        {DEMOS.map((demo) => (
          <article
            className="demo-card"
            key={`${demo.ticketId}-${demo.scenario}`}
          >
            <div className="demo-card-top">
              <span className="demo-ticket-id">{demo.ticketId}</span>
              <span
                className={
                  demo.forced ? "demo-kind demo-kind-forced" : "demo-kind"
                }
              >
                {demo.forced ? "FORCED TEST" : "SCRIPTED DEMO"}
              </span>
            </div>
            <h2>{demo.title}</h2>
            <p>{demo.description}</p>
            <div className="demo-inspect">
              <ShieldCheck size={15} />
              <span>{demo.inspect}</span>
            </div>
            {demo.forced && (
              <p className="demo-forced-disclosure">
                This intentionally injected behavior is a gate/recovery test,
                not a correct agent trajectory or an accuracy result.
              </p>
            )}
            <Link
              className="button button-secondary"
              to={`/tickets/${demo.ticketId}${demo.scenario === "standard" ? "" : `?scenario=${demo.scenario}`}`}
            >
              Open scenario
              <ArrowUpRight size={14} />
            </Link>
          </article>
        ))}
      </div>
    </>
  );
}
