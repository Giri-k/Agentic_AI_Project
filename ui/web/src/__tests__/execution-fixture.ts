import type { TicketDetail, TicketResult } from "../types";
import { detailFixture, fixtureFetch, health, summaries } from "./fixtures";

const zeroBudget = {
  model_calls: 0,
  tool_calls: 0,
  retries: 0,
  schema_repairs: 0,
  revisions: 0,
};
const limits = { ...health.limits, tool_calls: 8 };

// A stateful HTTP fixture exercises real query invalidation and polling without a model or browser bridge.
export function executionFixture() {
  const detail = structuredClone(detailFixture("T-0039")!);
  let active = false;
  let approved = false;
  let mode: "run" | "resume" | null = null;
  let runId = "";
  let budget = { ...zeroBudget };
  let system: "agent" | "baseline" = "agent";
  const events: Record<string, unknown>[] = [];
  const approval = {
    approval_id: "APR-8013",
    order_id: "O-60048",
    amount_cents: 114900,
    approved_by: "S-202",
    reason: "Return received like new, within window",
    created_at: "2026-10-20",
    used_by_refund: null as string | null,
  };

  // Events retain their run grouping and cumulative snapshots just like the API contract.
  function event(eventName: string, extra: Record<string, unknown> = {}) {
    events.push({
      ts: "2026-10-20T12:00:00Z",
      ticket_id: "T-0039",
      run_id: runId,
      event: eventName,
      budget: { ...budget },
      ...extra,
    });
  }

  // Deliberately finish only when a test asks, allowing assertions during the busy state.
  function finishRun() {
    const resumed = mode === "resume";
    budget = {
      ...budget,
      model_calls: resumed ? 4 : system === "baseline" ? 1 : 3,
      tool_calls: resumed ? 8 : 4,
    };
    const response = resumed
      ? "Your $1,149.00 refund has been issued. Receipt RF-4019."
      : "Your return was received. The $1,149.00 refund is waiting for staff approval.";
    const messageId = resumed ? "MSG-9005" : "MSG-9004";
    const receipts = resumed ? ["RF-4019", messageId] : [messageId];
    const result: TicketResult = {
      ticket_id: "T-0039",
      category: "returns_refunds",
      status: resumed ? "resolved" : "awaiting_approval",
      response,
      citations: ["POL-RET-01", "POL-RET-02"],
      receipt_ids: receipts,
      unresolved_requirements: resumed
        ? []
        : ["staff approval for 114900 cents"],
      stop_reason: resumed ? "refund_completed" : "awaiting_refund_approval",
    };
    if (resumed) {
      detail.orders[0].refunds.push({
        refund_id: "RF-4019",
        amount_cents: 114900,
        status: "issued",
        approval_id: "APR-8013",
        created_at: "2026-10-20",
      });
      detail.orders[0].remaining_refundable_cents = 0;
      detail.orders[0].status = "refunded";
      approval.used_by_refund = "RF-4019";
      detail.orders[0].approvals[0].used_by_refund = "RF-4019";
      event("tool_request", {
        call_id: `${runId}-refund`,
        tool: "process_refund",
        args: JSON.stringify({
          order_id: "O-60048",
          cents: 114900,
          approval_id: "APR-8013",
        }),
      });
      event("tool_result", {
        call_id: `${runId}-refund`,
        tool: "process_refund",
        ok: true,
        result: {
          receipt_id: "RF-4019",
          amount_cents: 114900,
          approval_id: "APR-8013",
          basis: "return_received",
        },
      });
    }
    detail.state = {
      ...detail.state,
      status: result.status,
      version: detail.state.version + 1,
      team: "Returns",
      last_result: result,
      actions: receipts.map((receipt_id) => ({
        type: receipt_id.startsWith("RF-") ? "process_refund" : "send_message",
        receipt_id,
      })),
    };
    detail.sent_messages.push({
      message_id: messageId,
      ticket_id: "T-0039",
      to: detail.customer.email,
      body: response,
      citations: result.citations,
      sent_at: "2026-10-20",
    });
    event("run_finished", {
      status: result.status,
      result,
      budget: { ...budget },
    });
    active = false;
    mode = null;
  }

  // Additive execution metadata keeps older adapter-shaped records usable during integration.
  function currentDetail(): TicketDetail {
    return {
      ...structuredClone(detail),
      running: active,
      execution: {
        budget: { ...budget },
        limits,
        active_run_id: active ? runId : null,
        can_run: !active && !detail.state.last_result,
        can_resume:
          !active && approved && detail.state.status === "awaiting_approval",
        resume_blocked_reason: approved
          ? null
          : "A matching staff approval is required before resuming.",
        system,
      },
    };
  }

  // Stub only execution-related routes; all other records still come from real sanitized fixtures.
  async function fetcher(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = new URL(String(input), "http://localhost:5173");
    const path = url.pathname;
    let body: unknown;
    let status = 200;
    if (path === "/api/health")
      body = {
        ...health,
        running: active,
        active_ticket_id: active ? "T-0039" : null,
        capabilities: {
          ...health.capabilities,
          run: true,
          resume: true,
          approve: true,
          trace: true,
        },
        limits,
      };
    else if (path === "/api/tickets")
      body = summaries.map((ticket) =>
        ticket.ticket_id === "T-0039"
          ? {
              ...ticket,
              status: detail.state.status,
              team: detail.state.team,
              category: detail.state.last_result?.category ?? null,
              running: active,
            }
          : ticket,
      );
    else if (path === "/api/tickets/T-0039") body = currentDetail();
    else if (path === "/api/tickets/T-0039/trace")
      body = events.slice(Number(url.searchParams.get("since") ?? "0"));
    else if (path === "/api/approvals")
      body = {
        waiting:
          detail.state.status === "awaiting_approval"
            ? [
                {
                  ...summaries.find((ticket) => ticket.ticket_id === "T-0039")!,
                  status: detail.state.status,
                  team: "Returns",
                  can_resume: !active && approved,
                  resume_blocked_reason: approved
                    ? null
                    : "A matching staff approval is required before resuming.",
                  pending_refund: {
                    order_id: "O-60048",
                    amount_cents: 114900,
                    reason: "Return received; exact approval needed",
                  },
                },
              ]
            : [],
        staged: [{ ...approval, active: approved, unblocks: ["T-0039"] }],
      };
    else if (
      path === "/api/approvals/APR-8013/approve" &&
      init?.method === "POST"
    ) {
      const already_active = approved;
      approved = true;
      if (!already_active) {
        detail.orders[0].approvals.push({ ...approval });
        const previous = runId;
        runId = "staff";
        event("staff_approval", { approval });
        runId = previous;
      }
      body = { approval, already_active };
    } else if (
      ["/api/tickets/T-0039/run", "/api/tickets/T-0039/resume"].includes(
        path,
      ) &&
      init?.method === "POST"
    ) {
      const resuming = path.endsWith("/resume");
      if (active) {
        status = 409;
        body = { detail: "A run is already in progress." };
      } else if (resuming && !approved) {
        status = 400;
        body = {
          detail: "A matching staff approval is required before resuming.",
        };
      } else {
        mode = resuming ? "resume" : "run";
        system = init.body
          ? (JSON.parse(String(init.body)).system ?? system)
          : system;
        runId = resuming ? "resume-2" : "run-1";
        active = true;
        event("run_started", { mode, system, model: "gpt-5-nano" });
        event("model_call", {
          n: resuming ? 4 : 1,
          tool_calls: ["ticket_get"],
          text: "Inspect the current customer-owned records.",
        });
        body = { run_id: runId };
        status = 202;
      }
    } else return fixtureFetch(input, init);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }
  return { fetch: fetcher, finishRun, detail: currentDetail, events };
}
