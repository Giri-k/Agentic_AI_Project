import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { executionFixture } from "./execution-fixture";
import { detailFixture, fixtureFetch, health } from "./fixtures";

const clients: QueryClient[] = [];

// Exercise optional grader requests and demo routing through the real app boundary.
function workspace(path: string, fetcher: typeof fetch) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: Infinity },
    },
  });
  clients.push(client);
  const fetchMock = vi.fn(fetcher);
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

// Keep DOM-only navigation deterministic without relying on unavailable browser automation.
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

// Unmount and clear cache observers before another optional grader journey starts.
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// The grader compares only explicit fields; wording correctness remains unverified.
function expectedFixture() {
  return {
    ticket_id: "T-0039",
    scenario_type: "R5",
    category: "returns_refunds",
    team: "Returns",
    final_status: "awaiting_approval",
    policy_ids: ["POL-RET-01", "POL-RET-02"],
    expected_actions: [
      "ticket_get",
      "kb_lookup",
      "send_message",
      "ticket_update",
    ],
    forbidden_actions: ["process_refund", "cancel_order"],
    response_facts: ["Return received; approval missing"],
    mock: true,
    source: "data/synthetic/labels.jsonl",
    phase: "initial",
    warning:
      "Draft fixture labels and scripted results are not independent evaluation evidence.",
    grading: {
      state: "passed",
      checks: [
        {
          key: "status",
          label: "Final status",
          expected: "awaiting_approval",
          actual: "awaiting_approval",
          passed: true,
        },
        {
          key: "response_facts",
          label: "Required response facts",
          expected: ["Return received; approval missing"],
          actual: null,
          passed: null,
          note: "Semantic grounding requires independent human review.",
        },
      ],
      notes: ["Only deterministic fields are checked."],
    },
  };
}

describe("grader isolation and the demo lab", () => {
  it("keeps expected answers unfetched until the grader is explicitly enabled", async () => {
    const scenario = executionFixture();
    await scenario.fetch("/api/tickets/T-0039/run", {
      method: "POST",
      body: JSON.stringify({ system: "agent" }),
    });
    scenario.finishRun();
    const fetcher = async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/expected")
        ? new Response(JSON.stringify(expectedFixture()), {
            headers: { "Content-Type": "application/json" },
          })
        : scenario.fetch(input, init);
    const { user, fetchMock } = workspace("/tickets/T-0039", fetcher);
    const toggle = await screen.findByRole("checkbox", { name: "Grader view" });
    expect(toggle).not.toBeChecked();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/expected")),
    ).toBe(false);
    await user.click(toggle);
    expect(await screen.findByText("Checked fields match")).toBeTruthy();
    expect(screen.getByText("Not verified")).toBeTruthy();
    expect(
      screen.getByText(/Semantic grounding requires independent human review/i),
    ).toBeTruthy();
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/expected")),
    ).toHaveLength(1);
    await user.click(toggle);
    expect(screen.queryByText("Checked fields match")).toBeNull();
  });

  it("opens a demo ticket from the lab and runs it like any other ticket", async () => {
    const scenario = executionFixture();
    const demo = detailFixture("T-0052")!;
    const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost:5173").pathname;
      if (path === "/api/health")
        return new Response(
          JSON.stringify({
            ...health,
            capabilities: { ...health.capabilities, run: true, trace: true },
            limits: { ...health.limits, tool_calls: 8 },
          }),
          { headers: { "Content-Type": "application/json" } },
        );
      if (path === "/api/tickets/T-0052")
        return new Response(
          JSON.stringify({
            ...demo,
            execution: { ...scenario.detail().execution, can_run: true },
          }),
          { headers: { "Content-Type": "application/json" } },
        );
      if (path === "/api/tickets/T-0052/run")
        return new Response(JSON.stringify({ run_id: "timeout-demo" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        });
      if (path === "/api/tickets/T-0052/trace")
        return new Response("[]", {
          headers: { "Content-Type": "application/json" },
        });
      return fixtureFetch(input, init);
    };
    const { user, fetchMock } = workspace("/demos", fetcher);
    const links = await screen.findAllByRole("link");
    const timeout = links.find(
      (link) =>
        link.getAttribute("href") === "/tickets/T-0052",
    );
    expect(timeout).toBeTruthy();
    await user.click(timeout!);
    const run = await screen.findByRole("button", { name: "Run agent" });
    await waitFor(() => expect(run).toBeEnabled());
    expect(screen.getByRole("button", { name: "Run baseline" })).toBeEnabled();
    await user.click(run);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/T-0052/run") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    const request = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/T-0052/run") && init?.method === "POST",
    )!;
    expect(JSON.parse(String(request[1]?.body))).toEqual({ system: "agent" });
  });
});
