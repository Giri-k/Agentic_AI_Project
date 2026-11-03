# Independent label review (November 2026)

All 160 expected-outcome labels were reviewed a second time, independently of how they were written. Each disagreement was then checked against the store records and policies before any label changed.

## How

- `PROMPT.md`: the reviewer's instructions. The reviewer decides each outcome itself before reading the label, and judges only from what the agent can see.
- `packet_1.md` to `packet_4.md`: 40 tickets each. Every ticket appears with only its own records and its label, plus the labelling rules and all 20 policies. Scenario codes were removed.
- `label_review_160.jsonl`: the reviewer's verdict on every ticket.

## Agreement (original labels against the reviewer)

| | Agree | Disagree | Unsure |
|---|---|---|---|
| Tickets | 106 | 43 | 11 |

Most disagreements were about reply wording (`response_facts`, `unresolved_requirements`), which the evaluation does not score. On the scored fields:

| Field | Agreement | Cohen's kappa |
|---|---|---|
| final_status | 95.0% | 0.932 |
| team | 94.4% | 0.920 |
| category | 100% | 1.000 |

## Changes made (14 labels, 3 records)

| Tickets | Change | Why |
|---|---|---|
| T-0006, T-0152 | escalated → awaiting_approval | The approval is filed under a non-existent order ID, so the agent cannot see it; from what it can see, only staff approval is missing (POL-RET-02). |
| T-0011, T-0012, T-0112 | team → Human Review | POL-SEC-02: another customer's order is escalated for Human Review. |
| T-0079, T-0160 | team → Human Review | POL-SUP-01: unsupported product claims go to Human Review. |
| T-0085 | team → Human Review | POL-SUP-01: languages other than English and Spanish go to Human Review. |
| T-0010 | resolved → escalated | POL-ACC-01: PIN recovery not covered by Forgot password needs staff. |
| T-0148 | resolved → escalated | POL-BIL-01: a continuing payment failure goes to Billing staff. |
| T-0008, T-0082, T-0128 | awaiting_approval → escalated | The customer asks how to complain and hasn't chosen refund or replacement; POL-RET-05 uses awaiting_approval only when an exact refund is all that remains. T-0062, which asks for a refund, keeps awaiting_approval. |
| T-0131 | send_message required | The tracking question can be answered now. |
| T-0065 (and the other two refund-status tickets) | approval date moved before the refund | Data error: RF-4012 was dated a day before its approval. |

The same rules are in `data/generate.py`, so regenerating the dataset reproduces them.

## System change instead of a label change

T-0032, T-0059 and T-0090 expect a fraud review, because the customer already had two non-delivery refunds (POL-FRD-01). Those refunds are on the customer's other orders, which the agent could not see. `ticket_get` now returns `prior_refunds_on_other_orders`, for the agent and the baseline alike. The labels are unchanged.

## Rejected

T-0055 and T-0137: the reviewer said the ticket contained no injected approval. It does. The packets rendered a `---` line and an HTML comment inside the message as markdown, which hid the attack text. The labels are unchanged. Any future packets should put message bodies in code blocks.

## Left as judgement calls (the 11 "unsure")

These are T-0009, T-0019, T-0034, T-0041, T-0046, T-0060, T-0061, T-0071, T-0081, T-0116 and T-0145: whether warranty applies to a technical fault, what "termination charges" means, and whether an out-of-window question is an exception request. They keep their labels and are noted as a limitation.

## Effect on the v1 results

The existing runs were rescored from their saved traces against the revised labels, with no new model calls (`python -m agent.eval_run --rescore`). Agent success went from 53.0% to 52.2%; baseline success went from 32.2% to 31.3%. The fraud tickets still fail in v1, because the history was added after that run.
