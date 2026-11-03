import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { executionFixture } from "./execution-fixture";

const clients: QueryClient[] = [];

// Real hooks, routes and components run against a controllable local HTTP lifecycle.
function workspace(
  path: string,
  scenario: ReturnType<typeof executionFixture>,
) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: Infinity },
    },
  });
  clients.push(client);
  const fetchMock = vi.fn(scenario.fetch);
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), fetchMock };
}

// JSDOM has no visual scroll implementation; navigation is still exercised normally.
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

// Clear all observers and timers so busy-state polling never leaks into another case.
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("interactive mock execution", () => {
  it.each([
    {
      route: "/",
      endpoint: "/api/admin/reset",
      open: "Reset workspace",
      title: "Reset the mock workspace?",
      confirm: "Reset workspace",
      cancel: "Keep workspace",
    },
    {
      route: "/approvals",
      endpoint: "/api/approvals/APR-8013/approve",
      open: "Approve",
      title: "Approve this refund?",
      confirm: "Activate approval",
      cancel: "Cancel",
    },
  ])(
    "keeps focus inside $title during submission and recovers after a failed request",
    async (config) => {
      const scenario = executionFixture();
      if (config.route === "/approvals") {
        await scenario.fetch("/api/tickets/T-0039/run", {
          method: "POST",
          body: JSON.stringify({ system: "agent" }),
        });
        scenario.finishRun();
      }
      let release!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const fetch = scenario.fetch;
      scenario.fetch = (input, init) => {
        const path = new URL(String(input), "http://localhost:5173").pathname;
        return path === config.endpoint && init?.method === "POST"
          ? pending
          : fetch(input, init);
      };
      const { user } = workspace(config.route, scenario);
      const open = await screen.findByRole("button", { name: config.open });
      await waitFor(() => expect(open).toBeEnabled());
      await user.click(open);
      const dialog = screen.getByRole("dialog", { name: config.title });
      await user.click(
        within(dialog).getByRole("button", { name: config.confirm }),
      );
      await waitFor(() =>
        expect(
          within(dialog).getByRole("button", { name: config.cancel }),
        ).toBeDisabled(),
      );
      await user.tab();
      expect(document.activeElement).toBe(dialog);
      await user.keyboard("{Escape}");
      expect(screen.getByRole("dialog", { name: config.title })).toBe(dialog);
      release(
        new Response(
          JSON.stringify({ detail: "Temporary failure. Try again." }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      );
      await within(dialog).findByText("Temporary failure. Try again.");
      await user.tab();
      expect(document.activeElement).toBe(
        within(dialog).getByRole("button", { name: config.cancel }),
      );
      await user.click(
        within(dialog).getByRole("button", { name: config.cancel }),
      );
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );

  it("starts a run, blocks duplicate actions and refreshes the paused result and conversation", async () => {
    const scenario = executionFixture();
    const { user, fetchMock } = workspace("/tickets/T-0039", scenario);
    const run = await screen.findByRole("button", { name: "Run agent" });
    await waitFor(() => expect(run).toBeEnabled());
    const trace = screen
      .getByText("Tool calls, retries, and receipts")
      .closest("details")!;
    expect(trace.open).toBe(false);
    const records = screen.getByRole("complementary", {
      name: "Customer and order records",
    });
    expect(
      records.compareDocumentPosition(trace) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    await user.click(run);
    await waitFor(() => expect(trace.open).toBe(true));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/run") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(screen.getByRole("button", { name: "Run baseline" })).toBeDisabled();
    scenario.finishRun();
    const heading = await screen.findByRole(
      "heading",
      { name: "Ticket result" },
      { timeout: 3500 },
    );
    const result = heading.closest("section")!;
    expect(within(result).getByText("Awaiting approval")).toBeTruthy();
    expect(within(result).getByText("POL-RET-02")).toBeTruthy();
    expect(within(result).getByText("MSG-9004")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeDisabled();
    expect(
      screen.getAllByText(/refund is waiting for staff approval/i).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(scenario.notice).length).toBeGreaterThan(0);
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) =>
          String(url).endsWith("/run") && init?.method === "POST",
      ),
    ).toHaveLength(1);
  }, 10000);

  it("confirms staff approval, resumes from the approvals page and shows the final receipt-backed result", async () => {
    const scenario = executionFixture();
    await scenario.fetch("/api/tickets/T-0039/run", {
      method: "POST",
      body: JSON.stringify({ system: "agent" }),
    });
    scenario.finishRun();
    const { user, fetchMock } = workspace("/approvals", scenario);
    await screen.findByRole("heading", { name: /^Staff approvals/ });
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Approve this refund?" });
    expect(within(dialog).getByText("$1,149.00")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/approve")),
    ).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Activate approval",
      }),
    );
    await screen.findByText("Already active");
    const resume = screen.getByRole("button", { name: "Resume ticket" });
    await waitFor(() => expect(resume).toBeEnabled());
    await user.click(resume);
    await screen.findByRole("heading", { name: "Execution trace", level: 1 });
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/approve")),
    ).toHaveLength(1);
    scenario.finishRun();
    await user.click(
      screen.getByRole("link", { name: "Back to ticket T-0039" }),
    );
    await screen.findByRole(
      "heading",
      { name: "Ticket result" },
      { timeout: 3500 },
    );
    await waitFor(
      () =>
        expect(
          within(
            screen
              .getByRole("heading", { name: "Ticket result" })
              .closest("section")!,
          ).getByText("RF-4019"),
        ).toBeTruthy(),
      { timeout: 3500 },
    );
    const result = screen
      .getByRole("heading", { name: "Ticket result" })
      .closest("section")!;
    expect(within(result).getByText("Resolved")).toBeTruthy();
    expect(within(result).getByText("POL-RET-01")).toBeTruthy();
    const balance = screen.getByText("Remaining refundable").parentElement!;
    expect(within(balance).getByText("$0.00")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeDisabled();
  }, 12000);

  it("renders unknown events and malformed arguments safely while highlighting a blocked refund", async () => {
    const scenario = executionFixture();
    scenario.events.push(
      {
        ts: "2026-10-20T12:00:00Z",
        ticket_id: "T-0039",
        run_id: "test-run",
        event: "future_backend_event",
        detail: "New event supported generically",
      },
      {
        ts: "2026-10-20T12:00:01Z",
        ticket_id: "T-0039",
        run_id: "test-run",
        event: "tool_request",
        call_id: "blocked",
        tool: "process_refund",
        args: "{not valid JSON",
      },
      {
        ts: "2026-10-20T12:00:02Z",
        ticket_id: "T-0039",
        run_id: "test-run",
        event: "tool_result",
        call_id: "blocked",
        tool: "process_refund",
        ok: false,
        error: {
          code: "NO_MATCHING_APPROVAL",
          message: "No exact approval exists.",
          retryable: false,
        },
      },
    );
    const { user } = workspace("/tickets/T-0039/trace", scenario);
    await screen.findByRole("heading", { name: "Execution trace", level: 1 });
    expect(await screen.findByText(/Future backend event/i)).toBeTruthy();
    expect(screen.getByText("NO_MATCHING_APPROVAL")).toBeTruthy();
    expect(screen.getByText(/Refund blocked by Python gate/i)).toBeTruthy();
    await user.click(screen.getByRole("checkbox", { name: "Show arguments" }));
    expect(screen.getByText("{not valid JSON")).toBeTruthy();
    await user.click(screen.getByRole("checkbox", { name: "Errors only" }));
    expect(screen.queryByText(/Future backend event/i)).toBeNull();
    expect(screen.getByText("NO_MATCHING_APPROVAL")).toBeTruthy();
  });

  it("labels the agent's routing, reply checks and revisions", async () => {
    const scenario = executionFixture();
    const base = { ticket_id: "T-0039", run_id: "test-run" };
    scenario.events.push(
      { ...base, ts: "2026-10-20T12:00:00Z", event: "route", category: "returns_refunds", team: "returns" },
      { ...base, ts: "2026-10-20T12:00:01Z", event: "reply_check", supported: false,
        problems: ["citation POL-X-99 is not in the evidence"], checked_by: "python" },
      { ...base, ts: "2026-10-20T12:00:02Z", event: "revision", n: 1,
        problems: ["citation POL-X-99 is not in the evidence"] },
      { ...base, ts: "2026-10-20T12:00:03Z", event: "reply_check", supported: true, problems: [],
        checked_by: "evaluator" },
    );
    workspace("/tickets/T-0039/trace", scenario);
    await screen.findByRole("heading", { name: "Execution trace", level: 1 });
    expect(await screen.findByText("Routed to Returns")).toBeTruthy();
    expect(screen.getByText(/Category Returns refunds/i)).toBeTruthy();
    expect(screen.getByText("Reply check found problems")).toBeTruthy();
    expect(screen.getByText(/^Python rules: citation POL-X-99/)).toBeTruthy();
    expect(screen.getByText("Revision 1")).toBeTruthy();
    expect(screen.getByText(/Sent back to the model to fix: citation POL-X-99/)).toBeTruthy();
    expect(screen.getByText("Reply check passed")).toBeTruthy();
    expect(screen.getByText(/Python rules and the evaluator model/)).toBeTruthy();
  });

  it("disables reset while another ticket is running", async () => {
    const scenario = executionFixture();
    await scenario.fetch("/api/tickets/T-0039/run", {
      method: "POST",
      body: JSON.stringify({ system: "agent" }),
    });
    workspace("/", scenario);
    await screen.findByRole("table");
    expect(
      screen.getByRole("button", { name: "Reset workspace" }),
    ).toBeDisabled();
  });

  it("focuses on matching approvals and offers a way back to all staged records", async () => {
    const scenario = executionFixture();
    const fetch = scenario.fetch;
    scenario.fetch = async (input, init) => {
      const response = await fetch(input, init);
      if (
        new URL(String(input), "http://localhost:5173").pathname !==
        "/api/approvals"
      )
        return response;
      const body = await response.json();
      body.staged.push({
        ...body.staged[0],
        approval_id: "APR-OTHER",
        order_id: "O-OTHER",
        unblocks: [],
      });
      return new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
      });
    };
    const { user } = workspace("/approvals", scenario);
    const matching = await screen.findByRole("checkbox", {
      name: "Matching waiting tickets",
    });
    expect(matching).toBeChecked();
    expect(screen.queryByText("APR-OTHER")).toBeNull();
    await user.click(matching);
    expect(screen.getByText("APR-OTHER")).toBeTruthy();
    await user.type(
      screen.getByRole("textbox", { name: "Search approvals" }),
      "unmatched search",
    );
    await user.click(matching);
    await user.click(
      screen.getByRole("button", { name: "Show all staged approvals" }),
    );
    expect(matching).not.toBeChecked();
    expect(
      screen.getByRole("textbox", { name: "Search approvals" }),
    ).toHaveValue("");
    expect(screen.getByText("APR-OTHER")).toBeTruthy();
  });

  it("does not turn an unestablished approval amount into a zero-dollar refund", async () => {
    const scenario = executionFixture();
    await scenario.fetch("/api/tickets/T-0039/run", {
      method: "POST",
      body: JSON.stringify({ system: "agent" }),
    });
    scenario.finishRun();
    const fetch = scenario.fetch;
    scenario.fetch = async (input, init) => {
      const response = await fetch(input, init);
      if (
        new URL(String(input), "http://localhost:5173").pathname !==
        "/api/approvals"
      )
        return response;
      const body = await response.json();
      body.waiting[0].pending_refund.amount_cents = null;
      return new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
      });
    };
    workspace("/approvals", scenario);
    const waiting = await screen.findByRole("region", {
      name: "Tickets awaiting approval",
    });
    expect(within(waiting).queryByText("$0.00")).toBeNull();
    expect(
      within(waiting).getByText(
        /not established|not available|unknown|requires staff review/i,
      ),
    ).toBeTruthy();
    expect(
      within(waiting).getByRole("button", { name: "Resume ticket" }),
    ).toBeDisabled();
  });

  it("starts the selected single-pass baseline rather than silently running the agent", async () => {
    const scenario = executionFixture();
    const { user, fetchMock } = workspace("/tickets/T-0039", scenario);
    const baseline = await screen.findByRole("button", {
      name: "Run baseline",
    });
    await waitFor(() => expect(baseline).toBeEnabled());
    await user.click(baseline);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/run") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    const request = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith("/run") && init?.method === "POST",
    )!;
    expect(JSON.parse(String(request[1]?.body))).toEqual({
      system: "baseline",
    });
    scenario.finishRun();
    await screen.findByRole(
      "heading",
      { name: "Ticket result" },
      { timeout: 3500 },
    );
    expect(screen.getAllByText("Baseline run").length).toBeGreaterThan(0);
  }, 10000);
});
