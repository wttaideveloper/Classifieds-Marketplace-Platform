from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.currency_schema import Money


class RefundCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"payment_id": "3f2a0b6e-0c1d-4a53-9a8e-6a4f3d6f9b10", "amount": "250.00", "reason": "The session was cancelled"}})

    payment_id: UUID
    amount: Decimal | None = Field(None, gt=0, description="Leave out to refund everything that is left")
    reason: str | None = Field(None, max_length=500, description="Required when a customer asks")
    destination: Literal["original", "bank_transfer", "wallet"] | None = Field(None, description="Only the business or platform can choose: original (default), bank_transfer")
    bank_reference: str | None = Field(None, max_length=120)


class RefundDecision(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = Field(None, max_length=500, description="Required to reject; the customer reads it")


class ManualCompletion(BaseModel):
    bank_reference: str = Field(..., min_length=3, max_length=120, description="The bank transfer reference")


class RefundResponse(BaseModel):
    id: UUID
    payment_id: UUID
    tenant_id: UUID | None = None
    status: str
    status_label: str
    refund_type: str
    amount: Money
    currency: str
    reason: str | None = None
    destination: str
    bank_reference: str | None = None
    tax_refunded: Money
    decision_reason: str | None = None
    decided_at: datetime | None = None
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    sla_due_at: datetime | None = None
    is_overdue: bool = False
    escalated_at: datetime | None = None
    failure_reason: str | None = None
    credit_note_id: UUID | None = None
    requested_by_role: str | None = None
    created_at: datetime
    actions: list[str] = Field(default_factory=list)


class RefundListResponse(BaseModel):
    items: list[RefundResponse]
    total: int
    page: int
    page_size: int
