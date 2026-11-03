// Public API shapes stay independent of the agent implementation and its grader labels.
export type Status =
  | "new"
  | "resolved"
  | "closed"
  | "awaiting_customer"
  | "awaiting_approval"
  | "escalated"
  | "failed_safe";
export type Priority = "low" | "medium" | "high" | "critical";
export type Channel = "email" | "chat" | "phone" | "social_media";
export type Split = "dev" | "test" | "demo";
export type Team = "Delivery" | "Returns" | "Billing" | "Human Review";
export type RunScenario =
  "standard" | "timeout_retry" | "repeated_call" | "refund_gate";

export interface Budget {
  model_calls: number;
  tool_calls: number;
  retries: number;
  schema_repairs: number;
  revisions: number;
}

export interface ExecutionState {
  budget: Budget;
  limits: Budget;
  backend_limits?: Budget;
  budget_notice?: string | null;
  active_run_id: string | null;
  can_run: boolean;
  run_blocked_reason?: string | null;
  can_resume: boolean;
  resume_blocked_reason: string | null;
  system: "agent" | "baseline" | null;
  scenario?: RunScenario;
  last_mode?: "run" | "resume" | null;
}

// Unknown event fields are intentional: the real adapter can add evidence without breaking the viewer.
export interface TraceEvent {
  ts: string;
  ticket_id: string;
  run_id: string;
  event: string;
  [key: string]: unknown;
}

export interface Health {
  adapter: "mock" | "real";
  as_of: string;
  running: boolean;
  active_ticket_id?: string | null;
  backend_limits?: Budget;
  budget_notice?: string | null;
  capabilities: {
    run: boolean;
    resume: boolean;
    approve: boolean;
    reset: boolean;
    trace: boolean;
    evaluation: boolean;
    grader?: boolean;
  };
  limits: {
    model_calls: number;
    tool_calls: number;
    retries: number;
    schema_repairs: number;
    revisions: number;
  };
}

export interface TicketSummary {
  ticket_id: string;
  subject: string;
  customer_id: string;
  customer_name: string;
  channel: Channel;
  priority: Priority;
  created_at: string;
  status: Status;
  version: number;
  team: Team | null;
  category: string | null;
  split: Split;
  running: boolean;
}

export interface Message {
  role: string;
  at: string;
  body: string;
}
export interface OutboxMessage {
  message_id: string;
  ticket_id: string;
  to: string;
  body: string;
  citations: string[];
  sent_at: string;
}
export interface TicketResult {
  ticket_id: string;
  category: string;
  status: Status;
  response: string | null;
  citations: string[];
  receipt_ids: string[];
  unresolved_requirements: string[];
  stop_reason: string;
}
export interface Payment {
  payment_id: string;
  method: string;
  amount_cents: number;
  status: string;
  captured_at?: string;
}
export interface Approval {
  approval_id: string;
  amount_cents: number;
  approved_by: string;
  reason: string;
  used_by_refund: string | null;
}
export interface WaitingTicket extends TicketSummary {
  pending_refund: {
    order_id: string;
    amount_cents: number | null;
    reason: string;
  } | null;
  unresolved_requirements?: string[];
  can_resume?: boolean;
  resume_blocked_reason?: string | null;
}
export interface StagedApproval extends Omit<Approval, "used_by_refund"> {
  order_id: string;
  created_at: string;
  used_by_refund?: string | null;
  active: boolean;
  unblocks: string[];
}
export interface ApprovalsResponse {
  waiting: WaitingTicket[];
  staged: StagedApproval[];
}
export interface Refund {
  refund_id: string;
  amount_cents: number;
  status: string;
  created_at?: string;
  approval_id: string;
}
export interface Order {
  order_id: string;
  customer_id: string;
  placed_at: string;
  total_cents: number;
  status: string;
  items: {
    name: string;
    qty: number;
    unit_cents: number;
    final_sale?: boolean;
  }[];
  shipment: {
    carrier: string;
    tracking: string;
    status: string;
    shipped_at: string;
    promised_by: string;
    delivered_at: string | null;
    last_event?: string | null;
  } | null;
  return: {
    received_at?: string | null;
    condition?: string | null;
    requested_at?: string | null;
  } | null;
  payments: Payment[];
  approvals: Approval[];
  refunds: Refund[];
  remaining_refundable_cents: number;
}
export interface TicketDetail {
  ticket: {
    ticket_id: string;
    created_at: string;
    channel: Channel;
    priority: Priority;
    subject: string;
    messages: Message[];
    order_ids: string[];
  };
  customer: { customer_id: string; name: string; email: string };
  orders: Order[];
  orders_not_on_this_account: string[];
  state: {
    status: Status;
    version: number;
    team: Team | null;
    actions: { type: string; receipt_id: string }[];
    last_result: TicketResult | null;
  };
  sent_messages: OutboxMessage[];
  as_of: string;
  running: boolean;
  execution?: ExecutionState;
}
export interface PolicySummary {
  policy_id: string;
  title: string;
  version: string;
}
export interface Policy extends PolicySummary {
  effective_date: string;
  sections: Record<"S1" | "S2" | "S3" | "S4", string>;
}

// Expected outcomes are requested only by the explicit grader view, never by the normal ticket view.
export interface GradingCheck {
  key: string;
  label: string;
  expected: unknown;
  actual: unknown;
  passed: boolean | null;
  note?: string;
}
export interface ExpectedOutcome {
  ticket_id: string;
  category: string;
  team: Team;
  final_status: Status;
  policy_ids: string[];
  expected_actions: string[];
  optional_actions?: string[];
  forbidden_actions: string[];
  response_facts: string[];
  unresolved_requirements?: string[];
  phase: "initial" | "after_resume";
  source: string;
  mock: boolean;
  warning: string;
  grading: {
    state: "not_run" | "running" | "passed" | "failed" | "informational";
    checks: GradingCheck[];
    notes: string[];
  };
}

export interface EvalRunSummary {
  run_id: string;
  system: "agent" | "baseline";
  split: "dev" | "test";
  created_at: string;
  mock: boolean;
  measured?: boolean;
  provenance?: string;
  benchmark_id?: string;
  policy_snapshot?: string;
  tickets?: number | EvalRun["tickets"];
  metrics?: EvalRun["metrics"];
  settings?: {
    model?: string;
    reasoning_effort?: string;
    prompt_versions?: Record<string, string>;
  };
}
export interface EvalRun extends EvalRunSummary {
  metrics: {
    status_accuracy: number;
    unauthorized_refunds_attempted: number;
    unauthorized_refunds_executed: number;
    refund_risk_tickets: number;
    unsupported_replies: number;
    correct_pauses: number;
    expected_pauses: number;
    citation_precision: number;
    citation_recall: number;
    avg_model_calls: number | null;
    avg_tool_calls: number | null;
    avg_tokens: number | null;
    avg_seconds: number | null;
    // Measured runs only (agent.eval_run); illustrative snapshots leave them out.
    success_rate?: number | null;
    category_accuracy?: number | null;
    category_macro_f1?: number | null;
    routing_accuracy?: number | null;
    action_accuracy?: number | null;
    tool_call_precision?: number | null;
    tool_call_recall?: number | null;
    tool_argument_accuracy?: number | null;
    tool_error_rate?: number | null;
    avg_tokens_in?: number | null;
    avg_tokens_out?: number | null;
    p95_seconds?: number | null;
    avg_model_latency_ms?: number | null;
    p95_model_latency_ms?: number | null;
    judge_fact_coverage?: number | null;
    judge_contradicted_replies?: number;
    judge_unsupported_replies?: number;
    harness_errors?: number;
  };
  by_group: { group: string; tickets: number; status_accuracy: number }[];
  by_end_state: { status: Status; tickets: number; status_accuracy: number }[];
  tickets: {
    ticket_id: string;
    scenario_type: string;
    expected_status: Status;
    actual_status: Status | null;
    passed: boolean;
    notes: string[];
  }[];
}
export interface RetrievalMetrics {
  mock: boolean;
  measured: boolean;
  provenance: string;
  queries: number;
  methods: {
    method: "embeddings" | "bm25";
    hit_at_3: number;
    recall_at_3: number;
    precision_at_3: number;
    mrr: number;
  }[];
}
