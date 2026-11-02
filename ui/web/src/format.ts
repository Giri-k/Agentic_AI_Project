import type { Status } from "./types";

export const STATUS_LABELS: Record<Status, string> = {
  new: "New",
  resolved: "Resolved",
  closed: "Closed",
  awaiting_customer: "Awaiting customer",
  awaiting_approval: "Awaiting approval",
  escalated: "Escalated",
  failed_safe: "Failed safe",
};

// Currency is calculated from integer cents; store dates never depend on the laptop's clock.
export const money = (cents: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    cents / 100,
  );
export const date = (value?: string | null, compact = false) => {
  if (!value) return "Not recorded";
  const parsed = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        ...(compact ? {} : { year: "numeric" as const }),
        timeZone: "UTC",
      }).format(parsed);
};
export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
export const humanize = (value: string) =>
  value.replaceAll("_", " ").replace(/^./, (char) => char.toUpperCase());

// A stable color makes a customer recognizable across the inbox and detail screen.
export const avatarColor = (name: string) =>
  ["sage", "peach", "lavender", "blue"][
    Array.from(name).reduce((sum, char) => sum + char.charCodeAt(0), 0) % 4
  ];
