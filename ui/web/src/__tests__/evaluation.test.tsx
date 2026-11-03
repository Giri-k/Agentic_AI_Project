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
import { fixtureFetch, summaries } from "./fixtures";
import type { EvalRun, RetrievalMetrics } from "../types";

const clients: QueryClient[] = [];

// Small synthetic observations use actual split membership and remain explicitly non-measured.
function makeRun(system: "agent" | "baseline", split: "dev" | "test"): EvalRun {
  const tickets = summaries
    .filter((ticket) => ticket.split === split)
    .map((ticket, index) => ({
      ticket_id: ticket.ticket_id,
      scenario_type: "D1",
      expected_status: "resolved" as const,
      actual_status:
        system === "baseline" && index === 0
          ? ("failed_safe" as const)
          : ("resolved" as const),
      passed: system !== "baseline" || index !== 0,
      notes: ["Illustrative test row, not an executed benchmark trace."],
    }));
  return {
    run_id: `mock-${system}-${split}`,
    system,
    split,
    created_at: "2026-10-20T00:00:00Z",
    mock: true,
    measured: false,
    provenance: "Synthetic UI fixtures; no measured model performance.",
    benchmark_id: `mock-${split}`,
    policy_snapshot: "policies-v1",
    metrics: {
      status_accuracy:
        tickets.filter((row) => row.passed).length / tickets.length,
      unauthorized_refunds_attempted: 0,
      unauthorized_refunds_executed: 0,
      refund_risk_tickets: 0,
      unsupported_replies: 0,
      correct_pauses: 0,
      expected_pauses: 0,
      citation_precision: 1,
      citation_recall: 1,
      avg_model_calls: system === "agent" ? 3 : 1,
      avg_tool_calls: 4,
      avg_tokens: null,
      avg_seconds: null,
    },
    by_group: [
      { group: "Delivery", tickets: tickets.length, status_accuracy: 1 },
    ],
    by_end_state: [
      { status: "resolved", tickets: tickets.length, status_accuracy: 1 },
    ],
    tickets,
  };
}

// The real route/query code consumes only read-only evaluation responses.
function workspace(path = "/eval", mismatched = false, withMetrics = false) {
  const runs = [
    makeRun("agent", "test"),
    makeRun("baseline", "test"),
    makeRun("agent", "dev"),
    makeRun("baseline", "dev"),
  ];
  if (mismatched) runs[1].policy_snapshot = "other-policies";
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: Infinity },
    },
  });
  clients.push(client);
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const pathname = new URL(String(input), "http://localhost:5173").pathname;
      let body: unknown;
      if (pathname === "/api/eval/runs")
        body = runs.map(
          ({ tickets, metrics, by_group, by_end_state, ...summary }) => {
            void by_group;
            void by_end_state;
            // The real API includes headline metrics in summaries; older servers did not.
            return withMetrics
              ? { ...summary, tickets: tickets.length, metrics, settings: { reasoning_effort: "low" } }
              : summary;
          },
        );
      else if (pathname.startsWith("/api/eval/runs/"))
        body = runs.find((run) => run.run_id === pathname.split("/").at(-1));
      else if (pathname === "/api/eval/retrieval")
        body = {
          mock: true,
          measured: false,
          provenance: "Illustrative ranking fixtures.",
          queries: 44,
          methods: [
            {
              method: "embeddings",
              hit_at_3: 0.8,
              recall_at_3: 0.7,
              precision_at_3: 0.3,
              mrr: 0.75,
            },
            {
              method: "bm25",
              hit_at_3: 0.7,
              recall_at_3: 0.6,
              precision_at_3: 0.25,
              mrr: 0.65,
            },
          ],
        } satisfies RetrievalMetrics;
      else return fixtureFetch(input, init);
      return new Response(
        JSON.stringify(body ?? { detail: "Unknown evaluation" }),
        {
          status: body ? 200 : 404,
          headers: { "Content-Type": "application/json" },
        },
      );
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), fetchMock, runs };
}

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("evaluation dashboard", () => {
  it("discloses mock results, separates safety counts and preserves missing cost measurements", async () => {
    const { user, fetchMock } = workspace();
    expect(
      await screen.findByText("MOCK DATA · Illustrative benchmark results"),
    ).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Unauthorized refunds" }),
    ).toBeTruthy();
    expect(screen.getByText("Attempted")).toBeTruthy();
    expect(screen.getByText("Executed")).toBeTruthy();
    await screen.findByText(/Same test split/);
    // Core comparison is visible; diagnostics expand without leaving this page.
    expect(
      screen.getByRole("rowheader", { name: "Unauthorized refunds executed" }),
    ).toBeTruthy();
    const costs = screen
      .getByText("Citations and execution costs")
      .closest("details")!;
    expect(costs.open).toBe(false);
    await user.click(within(costs).getByText("Citations and execution costs"));
    expect(costs.open).toBe(true);
    expect(within(costs).getAllByText("Not measured").length).toBeGreaterThan(
      0,
    );
    const retrieval = screen
      .getByText("Policy retrieval details")
      .closest("details")!;
    expect(retrieval.open).toBe(false);
    await user.click(within(retrieval).getByText("Policy retrieval details"));
    expect(retrieval.open).toBe(true);
    expect(
      within(retrieval).getByRole("columnheader", { name: "Hit@3" }),
    ).toBeTruthy();
    expect(screen.getByText(/no new retrieval experiment/)).toBeTruthy();
    expect(
      fetchMock.mock.calls.every(
        ([, init]) => !init?.method || init.method === "GET",
      ),
    ).toBe(true);
  });

  it("switches systems and split, filters ticket outcomes, and links to clearly identified current traces", async () => {
    const { user, fetchMock, runs } = workspace();
    await screen.findByText("MOCK DATA · Illustrative benchmark results");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Evaluation system" }),
      "baseline",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Evaluation split" }),
      "dev",
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/mock-baseline-dev"),
        ),
      ).toBe(true),
    );
    const section = (
      await screen.findByRole("heading", { name: /Per-ticket outcomes/ })
    ).closest("section")!;
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter ticket outcomes" }),
      "fail",
    );
    const first = runs.find(
      (run) => run.system === "baseline" && run.split === "dev",
    )!.tickets[0];
    await waitFor(() =>
      expect(within(section).getByText(first.ticket_id)).toBeTruthy(),
    );
    expect(within(section).getAllByRole("row")).toHaveLength(2);
    expect(
      within(section)
        .getByRole("link", { name: "Current trace" })
        .getAttribute("href"),
    ).toBe(`/tickets/${first.ticket_id}/trace`);
    await user.type(
      screen.getByRole("textbox", { name: "Search evaluation tickets" }),
      "no-matching-ticket",
    );
    expect(
      screen.getByRole("heading", { name: "No evaluation tickets match" }),
    ).toBeTruthy();
  });

  it("compares every run side by side, test and dev split apart", async () => {
    workspace("/eval", false, true);
    const section = await screen.findByRole("region", { name: "All runs compared" });
    const tables = within(section).getAllByRole("table");
    expect(tables).toHaveLength(2);
    expect(within(section).getByText(/Test split/)).toBeTruthy();
    expect(within(section).getByText(/Development split/)).toBeTruthy();
    const headers = within(tables[0]).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Metric", "Agent v1low effort", "Baseline v1low effort"]);
    const success = within(tables[0]).getByRole("row", { name: /Overall success/ });
    expect(within(success).getAllByRole("cell")).toHaveLength(2);
    expect(within(tables[0]).getByRole("row", { name: /Average model calls/ }).textContent).toContain("3.0");
  });

  it("leaves out the all-runs table when summaries carry no metrics", async () => {
    workspace();
    await screen.findByText(/Agent versus baseline/);
    expect(screen.queryByRole("region", { name: "All runs compared" })).toBeNull();
  });

  it("refuses a misleading comparison when policy snapshots differ", async () => {
    workspace("/eval", true);
    expect(
      await screen.findByText("No matching comparison run is available."),
    ).toBeTruthy();
    expect(screen.queryByText(/Same test split/)).toBeNull();
  });

  it("gives a useful recovery action for an invalid evaluation selection", async () => {
    const { user } = workspace("/eval?split=invalid");
    await screen.findByRole("heading", {
      name: "This evaluation selection isn’t available",
    });
    await user.click(
      screen.getByRole("button", { name: "Show the default evaluation view" }),
    );
    expect(
      await screen.findByText("MOCK DATA · Illustrative benchmark results"),
    ).toBeTruthy();
  });
});
