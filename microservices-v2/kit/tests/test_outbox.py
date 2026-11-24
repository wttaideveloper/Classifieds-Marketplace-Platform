"""Outbox (D3) and idempotent receiver."""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
os.environ.update({"DATABASE_URL": "sqlite://", "SECRET_KEY": "x", "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "x",
                   "GOOGLE_REDIRECT_URI": "x", "email_user": "x", "email_pass": "x", "LOG_LEVEL": "WARNING"})

from sqlalchemy import Column, Integer, String, create_engine  # noqa: E402
from sqlalchemy.orm import declarative_base, sessionmaker  # noqa: E402

from mskit import internal, outbox  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    Base = declarative_base()

    class Thing(Base):
        __tablename__ = "things"
        id = Column(Integer, primary_key=True)
        name = Column(String(50))

    outbox.models_for(Base)
    engine = create_engine("sqlite:///" + str(tmp_path / "o.db").replace("\\", "/"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sent, fail = [], {"n": 0}

    def fake_call(service, method, path, *, json=None, headers=None, expect=None, **kw):
        if fail["n"] > 0:
            fail["n"] -= 1
            raise internal.ServiceUnavailable("down")
        sent.append((service, method, path, json, headers["Idempotency-Key"]))

    monkeypatch.setattr(internal, "call", fake_call)
    return Base, Thing, Session, sent, fail


def test_delivered_right_after_commit(env):
    Base, Thing, Session, sent, _ = env
    ob = outbox.Outbox(Base, Session)
    with Session() as db:
        db.add(Thing(name="paid"))
        ob.enqueue(db, "events", "POST", "/event-orders/1/payment-confirmed", {"payment_id": "p1"}, "pay:p1:confirmed")
        assert sent == []                       # nothing before the commit
        db.commit()
    assert sent == [("events", "POST", "/event-orders/1/payment-confirmed", {"payment_id": "p1"}, "pay:p1:confirmed")]
    assert ob.pending_count() == 0


def test_rolled_back_change_sends_nothing(env):
    Base, Thing, Session, sent, _ = env
    ob = outbox.Outbox(Base, Session)
    with Session() as db:
        db.add(Thing(name="x"))
        ob.enqueue(db, "events", "POST", "/x", {}, "k-rollback")
        db.rollback()
    ob.deliver_due()
    assert sent == [] and ob.pending_count() == 0


def test_failure_is_retried_with_backoff_then_delivered(env):
    Base, Thing, Session, sent, fail = env
    ob = outbox.Outbox(Base, Session)
    fail["n"] = 1
    with Session() as db:
        ob.enqueue(db, "catalog", "POST", "/stock/releases", {"order": "o1"}, "release:o1")
        db.commit()
    assert sent == [] and ob.pending_count() == 1
    Msg = ob.Message
    with Session() as db:
        row = db.query(Msg).one()
        assert row.attempts == 1 and row.next_attempt_at > datetime.utcnow()
        row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)   # time passes
        db.commit()
    assert ob.deliver_due() == {"delivered": 1, "failed": 0}
    assert sent[0][4] == "release:o1"


def test_dead_after_max_attempts(env, monkeypatch):
    Base, Thing, Session, sent, fail = env
    monkeypatch.setenv("OUTBOX_MAX_ATTEMPTS", "2")
    ob = outbox.Outbox(Base, Session)
    fail["n"] = 10
    with Session() as db:
        ob.enqueue(db, "catalog", "POST", "/x", {}, "k-dead")
        db.commit()
    with Session() as db:
        row = db.query(ob.Message).one()
        row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    ob.deliver_due()
    with Session() as db:
        assert db.query(ob.Message).one().status == "dead"


def test_receiver_applies_a_message_once(env):
    Base, Thing, Session, _, _ = env
    with Session() as db:
        assert outbox.first_time(db, "pay:p1:confirmed", Base) is True
        db.commit()
    with Session() as db:
        assert outbox.first_time(db, "pay:p1:confirmed", Base) is False
