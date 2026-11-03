"""LLM judge for what the replies say: does each sent reply convey the label's expected facts, and does it make
claims the records, receipts and policies don't support?

Usage:  uv run python -m agent.judge --runs agent-test,baseline-test [--workers 6] [--redo]
        uv run python -m agent.judge --spotcheck agent-test,baseline-test   # 20 verdicts to check by hand
        uv run python -m agent.judge --agreement docs/judge_spotcheck.csv   # judge vs your verdicts

It reads saved traces only (no agent is run) and writes results/eval/<run>/judge.jsonl, then rescores the run so
the Evaluation page shows the judge's numbers. The judge never sees which system wrote a reply. Structured checks
(status, actions, refunds, citations) stay deterministic in agent.eval_run; the judge only covers meaning.
"""

import argparse
import csv
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from .config import DATA, POLICY_DIR
from .eval_run import RESULTS, rescore
from .schemas import Strict
from .store import load_jsonl

PROMPT_VERSION = "judge_v1"
PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()
JUDGE_EFFORT = "high"  # the judge is the same model as the agent (the only one on the course key), so it thinks harder


class FactVerdict(Strict):
    fact: str
    verdict: Literal["stated", "missing", "contradicted"]
    quote: str


class Judgement(Strict):
    facts: list[FactVerdict]
    unsupported_claims: list[str]


def build_case(label, events):
    """What the judge sees for one ticket, or None when no reply was sent (nothing to judge)."""
    ok = {e["call_id"]: e for e in events if e["event"] == "tool_result" and e.get("ok")}
    sent, records, receipts = [], None, []
    for e in events:
        if e["event"] != "tool_request" or e["call_id"] not in ok:
            continue
        result = ok[e["call_id"]]["result"]
        if e["tool"] == "ticket_get" and records is None:
            records = {k: result.get(k) for k in ("as_of", "ticket", "customer_name", "orders", "actions",
                                                  "orders_not_on_this_account", "prior_refunds_on_other_orders")}
        if e["tool"] == "send_message":
            sent.append(json.loads(e["args"]) if isinstance(e["args"], str) else e["args"])
        if isinstance(result, dict) and result.get("receipt_id"):
            receipts.append({"tool": e["tool"], "receipt_id": result["receipt_id"],
                             **{k: result[k] for k in ("amount_cents", "order_id", "status") if k in result}})
    if not sent:
        return None
    cited = sorted({c for m in sent for c in m.get("citations", [])})
    policies = {pid: (POLICY_DIR / f"{pid}.md").read_text() for pid in cited if (POLICY_DIR / f"{pid}.md").exists()}
    return {"reply": [m["body"] for m in sent], "expected_facts": label.get("response_facts", []),
            "records": records or {}, "receipts": receipts, "policies": policies}


def judge_case(llm, case):
    """One judge call; the verdicts come back in the order of the expected facts."""
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(case)}]
    return llm.parse(messages, Judgement)


def judge_row(ticket_id, label, events, llm):
    """The judge's row for one ticket. No reply sent: no call, and the facts don't count toward coverage."""
    case = build_case(label, events)
    if case is None:
        return {"ticket_id": ticket_id, "replied": False, "facts": [], "unsupported_claims": [], "error": None}
    try:
        out = judge_case(llm, case)
    except Exception as e:  # recorded, not hidden; a rerun with --redo tries again
        return {"ticket_id": ticket_id, "replied": True, "facts": [], "unsupported_claims": [],
                "error": f"{type(e).__name__}: {e}"[:200]}
    facts = [f.model_dump() for f in out.facts]
    if len(facts) != len(case["expected_facts"]):  # a malformed answer must not shift verdicts onto other facts
        return {"ticket_id": ticket_id, "replied": True, "facts": [], "unsupported_claims": [],
                "error": f"judge returned {len(facts)} verdicts for {len(case['expected_facts'])} facts"}
    for f, expected in zip(facts, case["expected_facts"]):
        f["fact"] = expected
    return {"ticket_id": ticket_id, "replied": True, "facts": facts,
            "unsupported_claims": out.unsupported_claims, "error": None}


def judge_run(run_id, labels, workers=6, redo=False, llm_factory=None, results=RESULTS):
    """Judge every ticket of a finished run (resuming), then rescore the run so its file includes the judge."""
    out = Path(results) / run_id
    path = out / "judge.jsonl"
    if redo:
        path.unlink(missing_ok=True)
    kept = [r for r in load_jsonl(path) if r["error"] is None] if path.exists() else []
    path.write_text("".join(json.dumps(r) + "\n" for r in kept))  # failed attempts are retried
    done = {r["ticket_id"] for r in kept}
    rows = load_jsonl(out / "rows.jsonl")
    todo = [r["ticket_id"] for r in rows if r["ticket_id"] not in done]
    print(f"{run_id}: {len(rows)} tickets, {len(done)} already judged, {len(todo)} to judge")

    local = threading.local()

    def make_llm():
        if llm_factory is not None:
            return llm_factory()
        from .llm import OpenAILLM
        return OpenAILLM(reasoning_effort=JUDGE_EFFORT)

    def one(ticket_id):
        if not hasattr(local, "llm"):
            local.llm = make_llm()
        events = load_jsonl(out / "traces" / f"{ticket_id}.jsonl")
        return judge_row(ticket_id, labels[ticket_id], events, local.llm)

    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for n, future in enumerate(as_completed([pool.submit(one, t) for t in todo]), 1):
            row = future.result()
            with lock, path.open("a") as f:
                f.write(json.dumps(row) + "\n")
            verdicts = [x["verdict"] for x in row["facts"]]
            print(f"  [{n}/{len(todo)}] {row['ticket_id']} " + ("no reply" if not row["replied"] else
                  row["error"] or f"{verdicts.count('stated')}/{len(verdicts)} stated, "
                                  f"{len(row['unsupported_claims'])} unsupported"))
    return rescore(run_id, labels, results=results)


def spotcheck(run_ids, n=20, seed=7, results=RESULTS, path=Path("docs/judge_spotcheck.csv")):
    """A random sample of judged facts for a person to check: fill in your_verdict, then run --agreement."""
    rng, sample = random.Random(seed), []
    for run_id in run_ids:
        for row in load_jsonl(Path(results) / run_id / "judge.jsonl"):
            for f in row["facts"]:
                sample.append((run_id, row["ticket_id"], f))
    picked = rng.sample(sample, min(n, len(sample)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["run", "ticket_id", "expected_fact", "judge_verdict", "judge_quote", "reply", "your_verdict"])
        for run_id, ticket_id, f in sorted(picked, key=lambda x: (x[0], x[1])):
            events = load_jsonl(Path(results) / run_id / "traces" / f"{ticket_id}.jsonl")
            reply = " | ".join(json.loads(e["args"])["body"] for e in events
                               if e["event"] == "tool_request" and e["tool"] == "send_message")
            w.writerow([run_id, ticket_id, f["fact"], f["verdict"], f["quote"], reply, ""])
    print(f"Wrote {len(picked)} verdicts to {path}: fill in your_verdict (stated / missing / contradicted).")


def agreement(path):
    """Share of hand-checked verdicts where the judge agrees with the person."""
    rows = [r for r in csv.DictReader(open(path)) if r["your_verdict"].strip()]
    agree = sum(r["your_verdict"].strip().lower() == r["judge_verdict"] for r in rows)
    print(f"{agree}/{len(rows)} judge verdicts match the hand check" + (f" ({agree / len(rows):.0%})" if rows else ""))
    return agree, len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m agent.judge")
    parser.add_argument("--runs", help="comma-separated run IDs to judge, e.g. agent-test,baseline-test")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--redo", action="store_true", help="discard saved verdicts and judge again")
    parser.add_argument("--spotcheck", help="runs to sample 20 verdicts from, for a hand check")
    parser.add_argument("--agreement", help="a filled-in spot-check CSV")
    args = parser.parse_args(argv)
    labels = {lab["ticket_id"]: lab for lab in load_jsonl(DATA / "labels.jsonl")}
    if args.runs:
        for run_id in args.runs.split(","):
            m = judge_run(run_id.strip(), labels, args.workers, args.redo)["metrics"]
            print(f"{run_id}: fact coverage {m['judge_fact_coverage']}, contradicted replies "
                  f"{m['judge_contradicted_replies']}, replies with unsupported claims {m['judge_unsupported_replies']}")
    if args.spotcheck:
        spotcheck([r.strip() for r in args.spotcheck.split(",")])
    if args.agreement:
        agreement(args.agreement)


if __name__ == "__main__":
    main()
