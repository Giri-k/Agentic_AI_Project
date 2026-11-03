You are reviewing the expected-outcome labels of a benchmark for a customer-support AI agent. The attached packet contains the rules the labels follow, the store's 20 policies, and 40 tickets. Each ticket shows the customer's messages, the store records for that ticket, and the label to verify.

Your job is to find labels that are wrong, not to agree with them. For each ticket:

1. Decide the correct outcome yourself first, from the ticket, the records and the policies only, before you read the label: category, team, final_status, which policies apply, which actions are required or forbidden, and any exact refund or cancellation arguments.
2. Then compare your decision with the label, field by field.
3. Judge the label by what the agent can actually see: the ticket text and the records. The agent never sees the "injected failure" line (but it does experience the failure), staged approvals that are not yet active, or anything marked NOT_VISIBLE_TO_THE_AGENT. If the correct outcome depends on something the agent cannot see, that is a label problem.
4. Apply the policies literally. If two outcomes are both defensible, mark the ticket "unsure" and explain, rather than forcing one.

Check especially:
- final_status: is it the status the rules define? (awaiting_approval only when a refund is due and every other condition is already met; awaiting_customer when the customer must act or answer; escalated when a person must decide.)
- team: does it follow the routing rule, including when an escalation goes to Human Review?
- policy_ids: are these the policies a correct reply must rely on? Missing or irrelevant ones?
- expected_actions and forbidden_actions: consistent with the status? (e.g. a resolved refund ticket needs process_refund; a ticket that must not refund forbids it.)
- expected_tool_args: exact order, amount in cents and approval ID, consistent with the records?
- response_facts and unresolved_requirements: true according to the records and policies?
- Ambiguous ticket text that a reasonable person could read two ways.

Output, in this exact format:

First, one JSON object per line for EVERY ticket in the packet, in order (no other text between them):
{"ticket_id": "T-0001", "verdict": "agree" | "disagree" | "unsure", "fields": {"<label field>": <your corrected value>}, "reason": "<one or two sentences citing the record values or policy rule>"}
Use "fields": {} when you agree.

Then a short summary: how many agree / disagree / unsure, and any pattern you noticed across several tickets (for example, a rule the labels apply inconsistently).
