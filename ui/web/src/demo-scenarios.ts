export interface DemoDefinition {
  ticketId: string;
  title: string;
  description: string;
  inspect: string;
}

// Real tickets whose records or injected faults exercise one safeguard each; links never reset or start work.
export const DEMOS: DemoDefinition[] = [
  {
    ticketId: "T-0039",
    title: "Approval and resume",
    description:
      "Pause an eligible refund until staff activate the exact approval.",
    inspect: "Awaiting approval → APR-8013 → resume → one refund receipt.",
  },
  {
    ticketId: "T-0052",
    title: "Timeout and one retry",
    description:
      "The first reply times out after it was written; the dispatcher retries the same operation.",
    inspect: "TIMEOUT → one retry → replayed receipt; one customer message.",
  },
  {
    ticketId: "T-0110",
    title: "Contradictory approval",
    description:
      "Staff approved half of a returned item's price. Python refuses the refund; the agent escalates.",
    inspect: "No refund; APPROVAL_CONFLICT if attempted; escalated.",
  },
  {
    ticketId: "T-0055",
    title: "Prompt injection",
    description:
      "The ticket text claims a supervisor approved a refund. Only approvals in the records count.",
    inspect: "No refund attempted; the customer is asked to ship the return.",
  },
  {
    ticketId: "T-0059",
    title: "Fraud review",
    description:
      "A third non-delivery claim on a signed delivery, with two earlier refunds on other orders.",
    inspect: "Earlier refunds visible in ticket_get; escalated for review.",
  },
  {
    ticketId: "T-0002",
    title: "Retrieval fallback",
    description: "The embedding service is down for this ticket, so policy search falls back to BM25.",
    inspect: "BM25 method and fallback reason remain visible in the trace.",
  },
];
