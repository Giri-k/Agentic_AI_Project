import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  AlertCircle,
  ArrowRight,
  CheckCheck,
  CircleDashed,
  Mail,
  MessageSquare,
  Phone,
  Globe,
  RotateCcw,
} from "lucide-react";
import { avatarColor, humanize, initials, STATUS_LABELS } from "./format";
import type { Channel, Priority, Status } from "./types";

// Status remains readable through its label and dot, rather than relying on color alone.
export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {STATUS_LABELS[status] ?? humanize(status)}
    </span>
  );
}

// Customer initials are generated locally; no external avatar service receives customer data.
export function Avatar({
  name,
  large = false,
}: {
  name: string;
  large?: boolean;
}) {
  return (
    <span
      aria-hidden="true"
      className={`avatar avatar-${avatarColor(name)} ${large ? "avatar-large" : ""}`}
    >
      {initials(name)}
    </span>
  );
}

// A visible urgency label accompanies each priority's colored indicator.
export function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <span className={`priority priority-${priority}`}>
      <i />
      {humanize(priority)}
    </span>
  );
}

// Channel names remain text so icons are decorative rather than the only meaning.
export function ChannelLabel({ channel }: { channel: Channel }) {
  const Icon =
    { email: Mail, chat: MessageSquare, phone: Phone, social_media: Globe }[
      channel
    ] ?? MessageSquare;
  return (
    <span className="channel-label">
      <Icon size={14} />
      {channel === "social_media" ? "Social" : humanize(channel)}
    </span>
  );
}

// Route-based policy links can be opened from either a result card or the library.
export function PolicyChip({ id }: { id: string }) {
  return (
    <Link className="policy-chip" to={`/policies/${encodeURIComponent(id)}`}>
      {id}
      <ArrowRight size={12} />
    </Link>
  );
}

// Reusable failure and empty states always provide a meaningful recovery action.
export function EmptyState({
  title,
  children,
  error = false,
  retry,
}: {
  title: string;
  children: ReactNode;
  error?: boolean;
  retry?: () => void;
}) {
  const Icon = error ? AlertCircle : CircleDashed;
  return (
    <div
      className={`empty-state ${error ? "error-state" : ""}`}
      role={error ? "alert" : undefined}
    >
      <div className="empty-icon">
        <Icon size={26} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {retry && (
        <button className="button button-secondary" onClick={retry}>
          <RotateCcw size={15} />
          Try again
        </button>
      )}
    </div>
  );
}

// Loading placeholders reserve the table's space while the local API responds.
export function TableSkeleton() {
  return (
    <div className="table-skeleton" aria-label="Loading tickets" role="status">
      {Array.from({ length: 7 }, (_, index) => (
        <div className="skeleton-row" key={index}>
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}

// Section titles share a small icon and consistent visual rhythm across records.
export function SectionTitle({
  icon,
  title,
  aside,
}: {
  icon?: ReactNode;
  title: string;
  aside?: ReactNode;
}) {
  return (
    <div className="section-title">
      <h2>
        {icon}
        {title}
      </h2>
      {aside}
    </div>
  );
}

// Existing receipts are displayed as evidence, never invented for an unprocessed ticket.
export function ReceiptChip({ id }: { id: string }) {
  return (
    <span className="receipt-chip">
      <CheckCheck size={13} />
      {id}
    </span>
  );
}
