"""Score retrieval alone, before the agent exists: search with each ticket's own text, compare with label policy_ids.

Usage:  uv run python -m agent.eval_retrieval [--split dev|test|all] [--misses 8] [--save]

--save writes results/eval/retrieval.json, which the console's Evaluation page shows.

Metrics per method (embeddings, bm25), averaged over tickets:
  Hit@3        at least one labelled policy in the top 3
  Recall@3     share of the labelled policies found in the top 3
  Precision@3  share of the 3 returned policies that are labelled (max 1/3 when a ticket needs one policy)
  MRR          1 / rank of the first labelled policy (1.0 = ranked first)
"""

import argparse
import json

from .config import DATA, REPO
from .eval_run import RESULTS
from .llm import embed
from .retrieval import Retriever
from .store import load_jsonl


def ticket_query(ticket):
    """Stand-in for the model's query: subject plus everything the customer wrote."""
    return " ".join([ticket["subject"], *(m["body"] for m in ticket["messages"] if m["role"] == "customer")])


def score(returned, gold):
    """Metrics for one ticket: returned is a ranked list of policy IDs, gold the labelled set."""
    hits = [pid in gold for pid in returned]
    first = next((i for i, h in enumerate(hits) if h), None)
    return {"hit": float(any(hits)), "recall": sum(hits) / len(gold), "precision": sum(hits) / len(returned),
            "mrr": 0.0 if first is None else 1 / (first + 1)}


def evaluate(retriever, tickets, labels, method):
    """Run one method over the tickets; returns mean metrics and per-ticket rows."""
    rows = []
    for t in tickets:
        found = retriever.search(ticket_query(t), method=method)
        returned = [p["policy_id"] for p in found["passages"]]
        gold = set(labels[t["ticket_id"]]["policy_ids"])
        rows.append({"ticket_id": t["ticket_id"], "scenario": labels[t["ticket_id"]]["scenario_type"],
                     "returned": returned, "gold": sorted(gold), "best_score": found["best_score"],
                     "weak": found["weak_evidence"], **score(returned, gold)})
    means = {m: sum(r[m] for r in rows) / len(rows) for m in ("hit", "recall", "precision", "mrr")}
    return means, rows


def main(argv=None):
    """Print a comparison table and the worst misses for each method."""
    parser = argparse.ArgumentParser(prog="python -m agent.eval_retrieval")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--misses", type=int, default=8, help="how many worst tickets to list per method")
    parser.add_argument("--save", action="store_true", help="write results/eval/retrieval.json for the console")
    args = parser.parse_args(argv)

    splits = json.loads((DATA / "splits.json").read_text())
    wanted = None if args.split == "all" else set(splits[args.split])
    tickets = [t for t in load_jsonl(DATA / "tickets.jsonl") if wanted is None or t["ticket_id"] in wanted]
    labels = {lab["ticket_id"]: lab for lab in load_jsonl(DATA / "labels.jsonl")}
    retriever = Retriever(embed_fn=embed)
    print(f"{len(tickets)} tickets ({args.split}); {len(retriever.chunks)} chunks; "
          f"newly embedded: {retriever.embeddings.newly_embedded}\n")

    print(f"{'method':<11} {'Hit@3':>6} {'Recall@3':>9} {'Prec@3':>7} {'MRR':>6}")
    results, table = {}, []
    for method in ("embeddings", "bm25"):
        means, rows = evaluate(retriever, tickets, labels, method)
        results[method] = rows
        table.append({"method": method, "hit_at_3": means["hit"], "recall_at_3": means["recall"],
                      "precision_at_3": means["precision"], "mrr": means["mrr"]})
        print(f"{method:<11} {means['hit']:>6.3f} {means['recall']:>9.3f} {means['precision']:>7.3f} {means['mrr']:>6.3f}")

    for method, rows in results.items():
        print(f"\nworst {method} tickets (lowest recall, then MRR):")
        for r in sorted(rows, key=lambda r: (r["recall"], r["mrr"]))[: args.misses]:
            print(f"  {r['ticket_id']} {r['scenario']:<4} recall {r['recall']:.2f} best {r['best_score']:.3f} "
                  f"gold {r['gold']} got {r['returned']}")
    if args.save:
        save(table, args.split, len(tickets))
    return results


def save(table, split, count, results=RESULTS):
    """The measured table in the shape the Evaluation page reads."""
    results.mkdir(parents=True, exist_ok=True)
    path = results / "retrieval.json"
    path.write_text(json.dumps({
        "measured": True, "source": "python -m agent.eval_retrieval", "split": split,
        "tickets": count, "queries": count, "methods": table,
        "provenance": f"Measured: each of {count} {split} tickets' own text searched with embeddings and with BM25, "
                      "top 3 compared with the labelled policy IDs.",
        "note": "The query is the ticket's subject and customer messages, standing in for the agent's own query.",
    }, indent=1))
    print(f"\nSaved {path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
