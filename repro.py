import traceback
from uuid import uuid4
from app.db.database import Base, SessionLocal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tests.group_chat_support import person, uid, TENANT
from app.services import group_chat_compliance as c
from app.services import event_notification_service as members

eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(eng)
s = sessionmaker(bind=eng)()

enzo = person("Dr Enzo", "provider")
records = [{"userId": str(uid(enzo)), "membershipId": str(uuid4()), "role": "internal_user", "membershipStatus": "active", "name": "Dr Enzo"}]
members.owning_tenant_users = lambda tenant, token: (list(records), "stub")

cu = {"id": str(uuid4()), "role": "admin", "email": "a@b.com", "tenant_id": str(TENANT)}
# need tenant scope: set caller_tenant
import app.services.group_chat_common as common
common.caller_tenant = lambda db, cu, tok: TENANT

try:
    res = c.list_verified_providers(s, cu, None)
    print("list_verified OK:", res)
except Exception:
    traceback.print_exc()

try:
    res = c.list_provider_candidates(s, cu, None, tenant_id=TENANT, q=None, include_verified=False, all_roles=False, page=1, page_size=50)
    print("candidates OK:", res)
except Exception:
    traceback.print_exc()
