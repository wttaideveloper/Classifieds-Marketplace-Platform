"""Hand-written identity flows vs. the monolith (each side on its own database) + data copy."""
import base64
import hashlib
import hmac
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from identity_service import main
from identity_service.db.database import engine as service_engine

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?")
USER = "550e8400-e29b-41d4-a716-446655440030"
OTHER = "550e8400-e29b-41d4-a716-446655440031"


def token(user_id, role="customer"):
    b = lambda d: base64.urlsafe_b64encode(d).rstrip(b"=").decode()  # noqa: E731
    h = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    p = b(json.dumps({"id": user_id, "role": role, "exp": 4102444800}).encode())
    s = b(hmac.new(b"v2-test-secret", f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{s}"


def norm(text_):
    ids = {}
    text_ = UUID_RE.sub(lambda m: ids.setdefault(m.group(0), m.group(0) if m.group(0) in (USER, OTHER) else f"<id{len(ids)}>"), text_)
    return TS_RE.sub("<ts>", text_)


@pytest.fixture(scope="module")
def pair():
    from app.main import app as monolith_app
    with TestClient(main.app) as svc:
        yield TestClient(monolith_app), svc


ADDRESS = {"label": "Home", "full_name": "Asha Rao", "phone": "9876543210", "line1": "12 MG Road", "city": "Bengaluru",
           "state": "KA", "zip": "560001", "country": "India", "is_default": True}

CASES = [
    ("GET", "/api/v1/addresses", None, None),
    ("POST", "/api/v1/addresses", ADDRESS, None),
    ("POST", "/api/v1/addresses", ADDRESS, USER),
    ("POST", "/api/v1/addresses", {**ADDRESS, "label": "Work", "is_default": True}, USER),
    ("POST", "/api/v1/addresses", {**ADDRESS, "label": "Mum"}, OTHER),
    ("POST", "/api/v1/addresses", {"label": "missing fields"}, USER),
    ("POST", "/api/v1/addresses", {**ADDRESS, "phone": ""}, USER),
    ("GET", "/api/v1/addresses", None, USER),
    ("GET", "/api/v1/addresses", None, OTHER),
    ("GET", "/api/v1/addresses", None, "garbage"),
    ("GET", "/api/v1/auth/integration", None, None),
    ("GET", "/api/v1/auth/me", None, USER),
    ("GET", "/api/v1/auth/me", None, None),
    ("GET", "/api/v1/auth/role-diagnostics", None, USER),
    ("POST", "/api/v1/auth/chat-token", None, USER),
    ("GET", "/api/v1/inventory", None, None),
    ("GET", "/api/v1/health", None, None),
]


@pytest.mark.parametrize("method,path,body,user", CASES)
def test_same_response_as_monolith(pair, method, path, body, user):
    mono, svc = pair
    headers = {}
    if user == "garbage":
        headers["Authorization"] = "Bearer not-a-token"
    elif user:
        headers["Authorization"] = "Bearer " + token(user)
    a = mono.request(method, path, json=body, headers=headers)
    b = svc.request(method, path, json=body, headers=headers)
    assert a.status_code == b.status_code, (a.text, b.text)
    na, nb = norm(a.text), norm(b.text)
    if "chat-token" in path and a.status_code == 200:
        na, nb = re.sub(r'"[\w-]+\.[\w-]+\.[\w-]+"', '"<jwt>"', na), re.sub(r'"[\w-]+\.[\w-]+\.[\w-]+"', '"<jwt>"', nb)
    assert na == nb


def test_same_rows_written(pair):
    from app.db.database import engine as monolith_engine
    q = "SELECT user_id, label, full_name, phone, line1, line2, city, state, zip, country, is_default, is_deleted FROM user_addresses WHERE user_id IN (:a, :b) ORDER BY user_id, label"
    params = {"a": USER.replace("-", ""), "b": OTHER.replace("-", "")}
    with monolith_engine.connect() as a, service_engine.connect() as b:
        rows_a, rows_b = a.execute(text(q), params).fetchall(), b.execute(text(q), params).fetchall()
    assert rows_a == rows_b and len(rows_a) >= 3


def test_copy_data_moves_rows_and_leaves_source_untouched(tmp_path):
    from sqlalchemy import create_engine

    from app.db.database import engine as monolith_engine
    from identity_service.db.database import Base
    from mskit.datacopy import copy_tables

    target = "sqlite:///" + str(tmp_path / "identity_copy.db").replace("\\", "/")
    Base.metadata.create_all(create_engine(target))
    with monolith_engine.connect() as c:
        before = c.execute(text("SELECT count(*) FROM user_addresses")).scalar()
    dry = copy_tables(str(monolith_engine.url), target, Base.metadata, ["user_addresses"], execute=False, truncate=False)
    assert dry["mode"].startswith("dry-run")
    with create_engine(target).connect() as c:
        assert c.execute(text("SELECT count(*) FROM user_addresses")).scalar() == 0
    done = copy_tables(str(monolith_engine.url), target, Base.metadata, ["user_addresses"], execute=True, truncate=False)
    assert done["verified"] and done["target_rows"]["user_addresses"] == before
    with monolith_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM user_addresses")).scalar() == before
