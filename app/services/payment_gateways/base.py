"""What every payment gateway adapter offers, and the normalised results the payment service works with.

The payment service never talks to Razorpay or Stripe directly: it asks an adapter, which answers in these shapes. That keeps the
rules (statuses, retries, refunds, settlement) in one place and lets the gateway be changed by a setting.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Payment states a gateway can report.
REQUIRES_PAYMENT_METHOD = "requires_payment_method"   # created; the customer has not paid yet
REQUIRES_ACTION = "requires_action"                   # 3-D Secure / OTP step still to be done
PROCESSING = "processing"
AUTHORIZED = "authorized"                             # approved, money held, not captured
SUCCEEDED = "succeeded"                               # captured
FAILED = "failed"
CANCELLED = "cancelled"


class GatewayError(Exception):
    def __init__(self, message: str, code: str = "gateway_error", retryable: bool = False):
        super().__init__(message)
        self.message, self.code, self.retryable = message, code, retryable


class GatewayTimeout(GatewayError):
    """The gateway did not answer in time. The payment may or may not have gone through: it must be checked, not assumed."""

    def __init__(self, message: str = "The payment gateway did not answer in time"):
        super().__init__(message, code="gateway_timeout", retryable=True)


@dataclass
class GatewayCustomer:
    user_id: str | None = None
    email: str | None = None
    name: str | None = None
    gateway_customer_id: str | None = None


@dataclass
class GatewayRequest:
    payment_id: str
    amount_minor: int
    currency: str
    decimals: int = 2
    capture_method: str = "automatic"
    description: str = ""
    customer: GatewayCustomer = field(default_factory=GatewayCustomer)
    saved_token: str | None = None
    return_url: str | None = None
    idempotency_key: str | None = None
    gateway_order_id: str | None = None
    gateway_payment_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class GatewayResult:
    status: str
    gateway_order_id: str | None = None
    gateway_payment_id: str | None = None
    next_action: dict | None = None            # what the customer must do next (3-D Secure redirect, SDK step)
    client_params: dict = field(default_factory=dict)   # what the browser / app SDK needs to take the payment
    method_brand: str | None = None
    method_last4: str | None = None
    fee_minor: int | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    status: str                                 # succeeded | pending | failed
    gateway_refund_id: str | None = None
    failure_message: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    event_id: str
    type: str          # payment.succeeded | payment.authorized | payment.failed | payment.cancelled | refund.succeeded | refund.failed
    gateway_order_id: str | None = None
    gateway_payment_id: str | None = None
    gateway_refund_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    fee_minor: int | None = None
    method_brand: str | None = None
    method_last4: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class MethodInfo:
    token: str
    brand: str | None = None
    last4: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    gateway_customer_id: str | None = None


class WebhookSignatureError(Exception):
    pass


class PaymentGateway(ABC):
    name = "base"

    @abstractmethod
    def create(self, request: GatewayRequest) -> GatewayResult:
        """Starts the payment at the gateway. With a saved token it may charge straight away."""

    @abstractmethod
    def confirm(self, request: GatewayRequest, client_data: dict[str, Any]) -> GatewayResult:
        """Checks what the customer's browser / app reports (signature, payment id) and returns the true state."""

    @abstractmethod
    def fetch(self, request: GatewayRequest) -> GatewayResult:
        """The gateway's own view of the payment, for checking a payment whose answer was lost."""

    @abstractmethod
    def capture(self, request: GatewayRequest) -> GatewayResult:
        """Takes the money of an authorized payment."""

    @abstractmethod
    def cancel(self, request: GatewayRequest) -> GatewayResult:
        """Voids a payment that is not captured yet."""

    @abstractmethod
    def refund(self, request: GatewayRequest, amount_minor: int, reason: str | None, idempotency_key: str) -> RefundResult:
        """Returns money to the customer's original payment method."""

    @abstractmethod
    def parse_webhook(self, headers: dict[str, str], raw_body: bytes) -> list[WebhookEvent]:
        """Checks the signature and returns the events. Raises WebhookSignatureError when it is wrong."""

    @abstractmethod
    def save_method(self, customer: GatewayCustomer, client_data: dict[str, Any]) -> MethodInfo:
        """Attaches the customer's payment method (a gateway token, never a card number) and returns its safe details."""

    def remove_method(self, token: str, gateway_customer_id: str | None) -> None:
        return None
