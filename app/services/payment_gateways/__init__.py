"""The configured payment gateway. Change PAYMENT_GATEWAY (test | razorpay | stripe) to switch."""
from app.core.config import settings
from app.services.payment_gateways.base import GatewayError, PaymentGateway


def get_gateway(name: str | None = None) -> PaymentGateway:
    name = (name or settings.PAYMENT_GATEWAY).lower()
    if name == "test":
        if settings.ENVIRONMENT == "production" and not settings.PAYMENT_ALLOW_TEST_GATEWAY:
            raise GatewayError("The test payment gateway is switched off in production. Set PAYMENT_GATEWAY to razorpay or stripe", code="not_configured")
        from app.services.payment_gateways.test_gateway import TestGateway

        return TestGateway()
    if name == "razorpay":
        from app.services.payment_gateways.razorpay_gateway import RazorpayGateway

        return RazorpayGateway()
    if name == "stripe":
        from app.services.payment_gateways.stripe_gateway import StripeGateway

        return StripeGateway()
    raise GatewayError(f"Unknown payment gateway: {name}", code="not_configured")
