"""Commerce's durable outbox (design D3): stock releases to catalog, payment confirmations to events."""
from commerce_service.db.database import Base, SessionLocal
from mskit.outbox import Outbox

OUTBOX = Outbox(Base, SessionLocal)
