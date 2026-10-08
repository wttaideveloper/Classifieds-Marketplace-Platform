"""customer_name resolution against the DOCUMENTED Invigorate shapes (HTTP mocked, no network).

- GET /api/v1/internal/tenants/{tenant_id}/users -> data[]: userId (application id), membershipId, fullName
- GET /api/v1/tenant/members                     -> data[]: id (MEMBERSHIP id), userId, fullName
The conversation's other_participant_user_id is the application user id and must never change.
"""

from uuid import uuid4

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import conversation as conversation_routes
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.services import chat_service, chat_user_names
from app.services import invigorate_auth_client as client
from tests.test_chat_customer_name import PROVIDER, TENANT, conversation, db, provider_user  # noqa: F401

BASE = "https://admin.example.test"
KEY = "key-SECRET"
TOKEN = "token-SECRET"


class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


@pytest.fixture(autouse=True)
def invigorate(monkeypatch):
    chat_user_names.clear_cache()
    state = {"routes": {}, "calls": []}

    def fake_get(url, headers=None, params=None, timeout=None):
        state["calls"].append((url, dict(headers or {})))
        return state["routes"].get(url, Resp({"detail": "nope"}, 404))

    monkeypatch.setattr(settings, "INVIGORATE_ADMIN_API_BASE_URL", BASE)
    monkeypatch.setattr(settings, "INVIGORATE_INTERNAL_API_KEY", KEY)
    monkeypatch.setattr(client.requests, "get", fake_get)
    yield state
    chat_user_names.clear_cache()


def tenant_users_url(tenant_id=TENANT):
    return f"{BASE}/api/v1/internal/tenants/{tenant_id}/users"


def internal_member(user_id, name):
    return {"membershipId": str(uuid4()), "userId": str(user_id), "fullName": name, "role": "external_user",
            "membershipStatus": "active", "userStatus": "active", "isSuperAdmin": False}


def test_list_and_detail_use_tenant_users_with_key_and_bearer(db, invigorate):  # noqa: F811
    session, _ = db
    customers = [uuid4() for _ in range(3)]
    convos = [conversation(session, c, tenant_id=TENANT, n=i) for i, c in enumerate(customers)]
    invigorate["routes"][tenant_users_url()] = Resp(
        {"message": "ok", "total": 3, "data": [internal_member(c, f"Customer {i}") for i, c in enumerate(customers)]}
    )

    page = chat_service.list_provider_conversations_service(
        session, provider_user(), page=1, page_size=20, access_token=TOKEN
    ).model_dump()["items"]
    assert {i["other_participant_user_id"]: i["customer_name"] for i in page} == {
        c: f"Customer {i}" for i, c in enumerate(customers)
    }
    # one tenant lookup for the whole page, carrying both documented credentials
    assert [u for u, _ in invigorate["calls"]] == [tenant_users_url()]
    assert invigorate["calls"][0][1] == {"X-Internal-Api-Key": KEY, "Authorization": f"Bearer {TOKEN}"}

    detail = chat_service.get_conversation_service(session, provider_user(), convos[1].id, access_token=TOKEN)
    assert detail.customer_name == "Customer 1" and detail.created_by == customers[1]


def test_membership_id_is_never_treated_as_the_user_id(db, invigorate):  # noqa: F811
    session, _ = db
    customer = uuid4()
    conversation(session, customer, tenant_id=TENANT)
    # /tenant/members: `id` is the membership id; only `userId` identifies the user
    invigorate["routes"][f"{BASE}/api/v1/tenant/members"] = Resp({"data": [
        {"id": str(customer), "userId": str(uuid4()), "fullName": "Someone Else", "role": "tenant_owner", "roleName": "Owner", "status": "active"},
        {"id": str(uuid4()), "userId": str(customer), "fullName": "Asha Rao", "role": "external_user", "roleName": "External", "status": "active"},
    ]})

    [item] = chat_service.list_provider_conversations_service(
        session, provider_user(), page=1, page_size=20, access_token=TOKEN
    ).model_dump()["items"]

    assert item["other_participant_user_id"] == customer
    assert item["customer_name"] == "Asha Rao"


def test_no_token_means_no_name_but_no_error(db, invigorate):  # noqa: F811
    session, _ = db
    conversation(session, uuid4(), tenant_id=TENANT)
    invigorate["routes"][tenant_users_url()] = Resp({"detail": "Missing Authorization"}, 400)

    [item] = chat_service.list_provider_conversations_service(session, provider_user(), page=1, page_size=20).model_dump()["items"]

    assert item["customer_name"] is None


def test_a_deleted_or_unknown_user_gives_null(db, invigorate):  # noqa: F811
    session, _ = db
    customer = uuid4()
    conversation(session, customer, tenant_id=TENANT)
    invigorate["routes"][tenant_users_url()] = Resp({"data": [internal_member(uuid4(), "Another Person")]})

    [item] = chat_service.list_provider_conversations_service(
        session, provider_user(), page=1, page_size=20, access_token=TOKEN
    ).model_dump()["items"]

    assert item["other_participant_user_id"] == customer and item["customer_name"] is None


def test_an_unfiltered_by_id_answer_never_names_the_wrong_person(db, monkeypatch):  # noqa: F811
    session, _ = db
    customer, someone_else = uuid4(), uuid4()
    conversation(session, customer)
    # the by-id lookup is not a documented endpoint and may return the first user of an unfiltered list
    monkeypatch.setattr(
        chat_user_names.identity, "fetch_internal_user_by_id",
        lambda user_id: {"id": str(someone_else), "fullName": "Someone Else"},
    )

    [item] = chat_service.list_provider_conversations_service(session, provider_user(), page=1, page_size=20).model_dump()["items"]

    assert item["customer_name"] is None


def test_endpoints_pass_the_callers_token_to_the_lookup(db, invigorate):  # noqa: F811
    session, _ = db
    customer = uuid4()
    convo = conversation(session, customer, tenant_id=TENANT)
    invigorate["routes"][tenant_users_url()] = Resp({"data": [internal_member(customer, "Asha Rao")]})

    app = FastAPI()
    app.include_router(conversation_routes.router, prefix="/api/v1/conversations")
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = provider_user
    http = TestClient(app)
    headers = {"Authorization": f"Bearer {TOKEN}"}

    listed = http.get("/api/v1/conversations/provider", headers=headers).json()["items"]
    detail = http.get(f"/api/v1/conversations/{convo.id}", headers=headers).json()

    assert listed[0]["customer_name"] == "Asha Rao" and listed[0]["other_participant_user_id"] == str(customer)
    assert detail["customer_name"] == "Asha Rao"
    assert all(h.get("Authorization") == f"Bearer {TOKEN}" for _, h in invigorate["calls"])
