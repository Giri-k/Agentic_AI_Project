import type { RunScenario } from "./types";

export interface DemoDefinition {
  ticketId: string;
  scenario: RunScenario;
  title: string;
  description: string;
  inspect: string;
  forced: boolean;
}

// Scenario selection is explicit; these links never reset records or start work automatically.
export const DEMOS: DemoDefinition[] = [
  {
    ticketId: "T-0039",
    scenario: "standard",
    title: "Approval and resume",
    description:
      "Pause an eligible refund until staff activate the exact approval.",
    inspect: "Awaiting approval → APR-8013 → resume → one refund receipt.",
    forced: false,
  },
  {
    ticketId: "T-0052",
    scenario: "timeout_retry",
    title: "Timeout and one retry",
    description:
      "Lose a tool reply after its write, then safely retry the same operation.",
    inspect: "TIMEOUT → one retry → replayed receipt; one customer message.",
    forced: false,
  },
  {
    ticketId: "T-0029",
    scenario: "repeated_call",
    title: "Repeated-call safe stop",
    description:
      "Deliberately request the same read three times to exercise the stop rule.",
    inspect:
      "Two reads execute; the third request stops safely before execution.",
    forced: true,
  },
  {
    ticketId: "T-0055",
    scenario: "refund_gate",
    title: "Forbidden refund gate",
    description:
      "Deliberately submit an invalid approval to the Python permission gate.",
    inspect: "NO_MATCHING_APPROVAL; no refund or balance change.",
    forced: true,
  },
  {
    ticketId: "T-0002",
    scenario: "standard",
    title: "Retrieval fallback",
    description: "Show a scripted embedding outage and the BM25 fallback.",
    inspect: "BM25 method and fallback reason remain visible in the trace.",
    forced: false,
  },
];
