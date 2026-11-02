import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ApprovalsResponse,
  EvalRun,
  EvalRunSummary,
  ExpectedOutcome,
  Health,
  Policy,
  PolicySummary,
  RetrievalMetrics,
  RunScenario,
  TicketDetail,
  TicketSummary,
  TraceEvent,
} from "./types";

// Preserve HTTP status so missing records can have a useful page instead of a generic crash.
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

// Browser calls are local and same-origin; Vite forwards /api to the local Python server.
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    signal: AbortSignal.timeout(10000),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      typeof body?.detail === "string"
        ? body.detail
        : "The workspace could not complete this request.",
      response.status,
    );
  return body as T;
}

// Shared keys keep the sidebar, counters and main screens synchronized after a reset.
export const useHealth = () =>
  useQuery({
    queryKey: ["health"],
    queryFn: () => request<Health>("/health"),
    refetchInterval: 1_000,
  });
export const useTickets = () =>
  useQuery({
    queryKey: ["tickets"],
    queryFn: () => request<TicketSummary[]>("/tickets"),
  });
export const useTicket = (id: string) =>
  useQuery({
    queryKey: ["ticket", id],
    queryFn: () => request<TicketDetail>(`/tickets/${encodeURIComponent(id)}`),
    refetchInterval: (query) =>
      query.state.data?.running ||
      query.state.data?.state.status === "awaiting_approval"
        ? 1_000
        : false,
  });
export const usePolicies = () =>
  useQuery({
    queryKey: ["policies"],
    queryFn: () => request<PolicySummary[]>("/policies"),
  });
export const usePolicy = (id: string) =>
  useQuery({
    queryKey: ["policy", id],
    queryFn: () => request<Policy>(`/policies/${encodeURIComponent(id)}`),
  });

// Fetch complete append-only history for this small local dataset, including the last completion event.
export function useTrace(ticketId: string, running = false) {
  const query = useQuery({
    queryKey: ["trace", ticketId],
    queryFn: () =>
      request<TraceEvent[]>(
        `/tickets/${encodeURIComponent(ticketId)}/trace?since=0`,
      ),
    refetchInterval: running ? 1_000 : false,
  });
  const refetch = query.refetch;
  useEffect(() => {
    void refetch();
  }, [running, ticketId, refetch]);
  return query;
}

// Waiting approvals refresh while this screen is open, including progress started in another view.
export const useApprovals = () =>
  useQuery({
    queryKey: ["approvals"],
    queryFn: () => request<ApprovalsResponse>("/approvals"),
    refetchInterval: 1_000,
  });

// An operation invalidates evidence as well as summaries so receipts never lag behind the saved result.
function useExecutionRefresh() {
  const client = useQueryClient();
  return async () => {
    await Promise.all(
      ["health", "tickets", "ticket", "trace", "approvals", "expected"].map(
        (key) => client.invalidateQueries({ queryKey: [key] }),
      ),
    );
  };
}

export function useStartRun(ticketId: string) {
  const refresh = useExecutionRefresh();
  return useMutation({
    mutationFn: (
      input:
        | "agent"
        | "baseline"
        | { system: "agent" | "baseline"; scenario: RunScenario },
    ) =>
      request<{ run_id: string }>(
        `/tickets/${encodeURIComponent(ticketId)}/run`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            typeof input === "string" ? { system: input } : input,
          ),
        },
      ),
    onSuccess: refresh,
    onError: refresh,
  });
}

export function useResumeTicket(ticketId: string) {
  const refresh = useExecutionRefresh();
  return useMutation({
    mutationFn: () =>
      request<{ run_id: string }>(
        `/tickets/${encodeURIComponent(ticketId)}/resume`,
        { method: "POST" },
      ),
    onSuccess: refresh,
    onError: refresh,
  });
}

export function useApproveApproval() {
  const refresh = useExecutionRefresh();
  return useMutation({
    mutationFn: (approvalId: string) =>
      request<{ approval: unknown; already_active: boolean }>(
        `/approvals/${encodeURIComponent(approvalId)}/approve`,
        { method: "POST" },
      ),
    onSuccess: refresh,
    onError: refresh,
  });
}

// A disabled grader never requests labels, even when the ordinary ticket is polling.
export function useExpected(
  ticketId: string,
  enabled: boolean,
  running = false,
) {
  return useQuery({
    queryKey: ["expected", ticketId],
    queryFn: () =>
      request<ExpectedOutcome>(
        `/tickets/${encodeURIComponent(ticketId)}/expected`,
      ),
    enabled,
    staleTime: 0,
    refetchInterval: enabled && running ? 1_000 : false,
  });
}

// Evaluation displays server-provided snapshots; it never derives benchmark accuracy from live mock runs.
export const useEvalRuns = () =>
  useQuery({
    queryKey: ["eval-runs"],
    queryFn: () => request<EvalRunSummary[]>("/eval/runs"),
  });
export const useEvalRun = (id: string) =>
  useQuery({
    queryKey: ["eval-run", id],
    queryFn: () => request<EvalRun>(`/eval/runs/${encodeURIComponent(id)}`),
    enabled: Boolean(id),
  });
export const useRetrievalMetrics = () =>
  useQuery({
    queryKey: ["retrieval-metrics"],
    queryFn: () => request<RetrievalMetrics>("/eval/retrieval"),
  });
