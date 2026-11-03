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
import { detailFixture, fixtureFetch, policies, summaries } from "./fixtures";

const fetchMock = vi.fn(fixtureFetch);
const clients: QueryClient[] = [];

// Fresh query caches and local HTTP fixtures isolate every user journey.
function workspace(path = "/") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: Infinity },
    },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

// JSDOM omits scrolling; the test still exercises the same navigation/pagination handlers.
beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-large-type");
  fetchMock.mockReset().mockImplementation(fixtureFetch);
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

// Remove page effects, query timers and globals before the next journey.
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("read-only support workspace", () => {
  it("searches a real ticket and opens its owned records with execution disabled", async () => {
    const user = workspace();
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(16);
    expect(screen.getByText("REAL")).toBeTruthy();
    await user.type(
      screen.getByRole("textbox", { name: "Search tickets" }),
      "t-0039",
    );
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    const row = within(table).getByText("T-0039").closest("tr")!;
    expect(within(row).getByText("Approval demo")).toBeTruthy();
    const demo = detailFixture("T-0039")!;
    await user.click(
      within(row).getByRole("link", { name: demo.ticket.subject }),
    );
    await screen.findByRole("heading", { name: demo.ticket.subject });
    expect(screen.getByText("O-60048")).toBeTruthy();
    expect(screen.getByText("No active approval on this order.")).toBeTruthy();
    expect(screen.queryByText("APR-8013")).toBeNull();
    const balance = screen.getByText("Remaining refundable").parentElement!;
    expect(within(balance).getByText("$1,149.00")).toBeTruthy();
    for (const name of ["Run agent", "Run baseline", "Resume"]) {
      expect(
        (screen.getByRole("button", { name }) as HTMLButtonElement).disabled,
      ).toBe(true);
    }
    expect(screen.queryByRole("heading", { name: "Ticket result" })).toBeNull();
  });

  it("composes split and priority filters without changing source records", async () => {
    const user = workspace();
    await screen.findByRole("table");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "new",
    );
    await user.click(screen.getByRole("button", { name: "Development" }));
    await user.click(screen.getByRole("button", { name: "Filters" }));
    await user.selectOptions(screen.getByLabelText("Priority"), "critical");
    const expected = summaries.filter(
      (ticket) =>
        ticket.split === "dev" &&
        ticket.priority === "critical" &&
        ticket.status === "new",
    );
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(expected.length + 1);
    for (const ticket of expected)
      expect(within(table).getByText(ticket.ticket_id)).toBeTruthy();
    expect(within(table).queryByText("T-0039")).toBeNull();
  });

  it("paginates and resets the page when narrowing to the demo split", async () => {
    const user = workspace();
    const table = await screen.findByRole("table");
    const firstTicket = within(table).getAllByRole("row")[1].textContent;
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(within(table).getAllByRole("row")[1].textContent).not.toBe(
      firstTicket,
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Previous page",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    await user.click(screen.getByRole("button", { name: "Demo" }));
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("T-0039")).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: "Previous page",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Next page" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows inaccessible orders without rendering their private details", async () => {
    workspace("/tickets/T-0011");
    await screen.findByRole("heading", {
      name: detailFixture("T-0011")!.ticket.subject,
    });
    expect(
      screen.getByText("Not accessible on this customer’s account."),
    ).toBeTruthy();
    expect(screen.getByText("O-60106")).toBeTruthy();
    expect(screen.queryByText("Remaining refundable")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Payments" })).toBeNull();
  });

  it("provides a recoverable not-found page for an unknown direct ticket URL", async () => {
    workspace("/tickets/T-9999");
    expect(
      await screen.findByRole("heading", { name: "Ticket not found" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Back to inbox" }).getAttribute("href"),
    ).toBe("/");
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.queryByText("Remaining refundable")).toBeNull();
  });

  it("preserves historical agent replies while describing only the current preview as unsent", async () => {
    const historical = summaries
      .map((ticket) => detailFixture(ticket.ticket_id)!)
      .find((detail) =>
        detail.ticket.messages.some((message) => message.role === "agent"),
      )!;
    const agentReply = historical.ticket.messages.find(
      (message) => message.role === "agent",
    )!;
    workspace(`/tickets/${historical.ticket.ticket_id}`);
    expect(await screen.findByText(agentReply.body)).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "No replies sent in this preview" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("heading", { name: "No agent reply yet" }),
    ).toBeNull();
  });

  it("recovers inbox data and health-dependent controls after the local API comes back", async () => {
    let available = false;
    fetchMock.mockImplementation((input, init) => {
      const pathname = new URL(String(input), "http://localhost:5173").pathname;
      if (!available && ["/api/health", "/api/tickets"].includes(pathname)) {
        return Promise.reject(
          new TypeError("Local API is temporarily unavailable"),
        );
      }
      return fixtureFetch(input, init);
    });
    const user = workspace();
    await screen.findByRole("heading", { name: "The inbox couldn’t connect" });
    expect(await screen.findByText("OFFLINE")).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: "Reset workspace",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    available = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeTruthy();
    expect(await screen.findByText("REAL")).toBeTruthy();
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Reset workspace",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
  });

  it("renders recorded policy versions and follows policy-library search links", async () => {
    const user = workspace("/policies/POL-FRD-01");
    const fraudPolicy = policies.find(
      (policy) => policy.policy_id === "POL-FRD-01",
    )!;
    await screen.findByRole("heading", { name: fraudPolicy.title });
    expect(screen.getByText("v1.0")).toBeTruthy();
    for (const section of [
      "Scope",
      "Conditions",
      "Policy rules",
      "Exceptions",
    ]) {
      expect(screen.getByRole("heading", { name: section })).toBeTruthy();
    }
    expect(screen.getByText(fraudPolicy.sections.S1)).toBeTruthy();
    await user.click(
      screen.getByRole("link", { name: "Back to policy library" }),
    );
    await screen.findByRole("textbox", { name: "Search policies" });
    await user.type(
      screen.getByRole("textbox", { name: "Search policies" }),
      "POL-RET-02",
    );
    const refundPolicy = policies.find(
      (policy) => policy.policy_id === "POL-RET-02",
    )!;
    await user.click(
      screen.getByRole("link", { name: new RegExp(refundPolicy.title) }),
    );
    await screen.findByRole("heading", { name: refundPolicy.title });
    expect(screen.getByText("v1.1")).toBeTruthy();
  });

  it("requires reset confirmation and sends exactly one POST after confirming", async () => {
    const user = workspace("/policies");
    await screen.findByRole("heading", { name: /Policy library/ });
    await user.click(screen.getByRole("button", { name: "Reset workspace" }));
    let dialog = screen.getByRole("dialog", {
      name: "Reset the working store?",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Keep workspace" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Reset workspace" }));
    dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Reset workspace" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      await screen.findByText(
        "Working store reset. Runs and traces were cleared.",
      ),
    ).toBeTruthy();
    const posts = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "POST",
    );
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe("/api/admin/reset");
  });

  it("keeps closed mobile navigation out of the accessibility tree and returns focus on Escape", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    const user = workspace();
    await screen.findByRole("table");
    const menu = screen.getByRole("button", { name: "Open navigation" });
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    expect(
      screen.queryByRole("navigation", { name: "Main navigation" }),
    ).toBeNull();
    expect(
      document.getElementById("workspace-navigation")?.hasAttribute("inert"),
    ).toBe(true);
    await user.click(menu);
    const navigation = screen.getByRole("dialog", {
      name: "Workspace navigation",
    });
    expect(menu.getAttribute("aria-expanded")).toBe("true");
    expect(document.body.style.overflow).toBe("hidden");
    const lastLink = within(navigation).getByRole("link", {
      name: "Evaluation",
    });
    lastLink.focus();
    expect(document.activeElement).toBe(lastLink);
    await user.tab();
    expect(document.activeElement).toBe(
      within(navigation).getByRole("button", { name: "Close navigation" }),
    );
    await user.keyboard("{Escape}");
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(menu);
    expect(document.body.style.overflow).toBe("");
  });

  it("persists the projector-friendly large-text preference", async () => {
    const user = workspace();
    await screen.findByRole("table");
    const toggle = screen.getByRole("button", { name: "Large text" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    await user.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(document.documentElement.dataset.largeType).toBe("true");
    expect(localStorage.getItem("opus-large-type")).toBe("true");
  });
});
