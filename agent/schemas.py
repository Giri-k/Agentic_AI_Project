"""Strict data shapes shared by the tools, the dispatcher and the agent loop."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class Category(str, Enum):
    """The 7 ticket categories the agent can assign."""
    DELIVERY = "delivery"
    RETURNS_REFUNDS = "returns_refunds"
    BILLING = "billing"
    CANCELLATION = "cancellation"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    PRODUCT_INQUIRY = "product_inquiry"


class Status(str, Enum):
    """The 6 end states a ticket can be saved in."""
    RESOLVED = "resolved"
    CLOSED = "closed"
    AWAITING_CUSTOMER = "awaiting_customer"
    AWAITING_APPROVAL = "awaiting_approval"
    ESCALATED = "escalated"
    FAILED_SAFE = "failed_safe"


class Team(str, Enum):
    """The 4 staff queues a ticket can be routed to."""
    DELIVERY = "Delivery"
    RETURNS = "Returns"
    BILLING = "Billing"
    HUMAN_REVIEW = "Human Review"


# POL-SUP-01: each category has one queue; escalations stay there unless they need Human Review.
CATEGORY_TEAM = {
    Category.DELIVERY: Team.DELIVERY,
    Category.PRODUCT_INQUIRY: Team.DELIVERY,
    Category.RETURNS_REFUNDS: Team.RETURNS,
    Category.BILLING: Team.BILLING,
    Category.CANCELLATION: Team.BILLING,
    Category.TECHNICAL: Team.HUMAN_REVIEW,
    Category.ACCOUNT: Team.HUMAN_REVIEW,
}
HUMAN_REVIEW_REASONS = ("fraud_review", "policy_conflict", "unsupported_request")

NEEDS_OPEN_ITEMS = {Status.AWAITING_CUSTOMER, Status.AWAITING_APPROVAL, Status.ESCALATED}


class Strict(BaseModel):
    """Base for every model: unknown fields and loose types (e.g. "5" for 5) are rejected."""
    model_config = ConfigDict(extra="forbid", strict=True)


class TicketResult(Strict):
    """The agent's final answer for a ticket, exactly the spec's schema."""
    ticket_id: str
    category: Category
    status: Status
    response: str | None
    citations: list[str]
    receipt_ids: list[str]
    unresolved_requirements: list[str]
    stop_reason: str

    @model_validator(mode="after")
    def _status_matches_open_items(self):
        """Done means nothing outstanding; a pause or escalation must say what is outstanding."""
        if self.status in (Status.RESOLVED, Status.CLOSED) and self.unresolved_requirements:
            raise ValueError(f"{self.status.value} cannot have unresolved requirements")
        if self.status in NEEDS_OPEN_ITEMS and not self.unresolved_requirements:
            raise ValueError(f"{self.status.value} must list what is still outstanding")
        return self


def route_team(category, status, stop_reason):
    """Queue for a saved result: the category's team, or Human Review for the escalations POL-SUP-01 names."""
    if Status(status) == Status.ESCALATED and stop_reason.startswith(HUMAN_REVIEW_REASONS):
        return Team.HUMAN_REVIEW
    return CATEGORY_TEAM[Category(category)]


class ToolError(Exception):
    """A tool refused or failed. The model sees the code and whether a retry can help."""

    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable

    def as_dict(self):
        """Shape sent back to the model as the tool result."""
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class StopRun(Exception):
    """Ends the run immediately (budget spent, repeated call, unknown ticket). Python saves failed_safe."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
