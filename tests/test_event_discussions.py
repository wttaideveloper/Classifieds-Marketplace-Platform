"""Event Discussions: access, AI moderation (safe / flagged / extreme / unavailable), the Enterprise Admin's review, the Super Admin's read-only
view, notifications with deep links, and the two nudges."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.event_discussion_model import EventDiscussionMember, EventDiscussionMessage, EventDiscussionModeration, EventDiscussionNudge
from app.models.notification_model import Notification, UserNotification
from app.services import event_discussion_jobs as jobs
from app.services import event_discussion_moderation as moderation
from app.services import event_discussion_notifications as notify
from event_sql_support import client_for, make_enterprise, make_event, make_registration, make_session, reset_overrides, staff_user, super_admin_user

API = "/api/v1/events"
ADMIN_API = "/api/v1/event-discussions"
BLOCKED = "Your message violates community guidelines and cannot be posted."


def fake_classify(text):
    lowered = text.lower()
    if "ai-down" in lowered:
        raise moderation.ModerationUnavailable("timeout")
    if "killyou" in lowered:
        return moderation.Verdict("extreme", "AI detected severe content: harassment - threatening", ["harassment/threatening"], "test")
    if "badword" in lowered:
        return moderation.Verdict("flagged", "AI flagged: harassment", ["harassment"], "test")
    return moderation.Verdict("safe", None, [], "test")


@pytest.fixture
def env(monkeypatch):
    for target in ("app.services.super_admin_identity.fetch_internal_user_by_id", "app.services.super_admin_identity.fetch_auth_me_profile",
                   "app.services.invigorate_auth_client.fetch_tenant_me_profile", "app.services.invigorate_auth_client.fetch_auth_me_profile"):
        monkeypatch.setattr(target, lambda *a, **k: None)
    db = make_session()
    bind = db.get_bind()
    for model in (EventDiscussionMessage, EventDiscussionModeration, EventDiscussionMember, EventDiscussionNudge, Notification, UserNotification):
        model.__table__.create(bind=bind, checkfirst=True)
    tenant, other_tenant = uuid4(), uuid4()
    enterprise = make_enterprise(db, tenant, "Apollo Events")
    event = make_event(db, tenant, enterprise, title="Health Harmony Summit", status="published")

    def person(name, email, role="customer"):
        return {"id": str(uuid4()), "name": name, "email": email, "role": role}

    admin = {**staff_user(tenant, "admin", "admin@apollo.test"), "id": str(uuid4()), "name": "Anita Admin"}
    admin2 = {**staff_user(tenant, "admin", "admin2@apollo.test"), "id": str(uuid4()), "name": "Arun Admin"}
    stranger_admin = {**staff_user(other_tenant, "admin", "x@other.test"), "id": str(uuid4()), "name": "Other Admin"}
    people = SimpleNamespace(priya=person("Priya", "priya@example.com"), ravi=person("Ravi", "ravi@example.com"), cancelled=person("Cara", "cara@example.com"),
                             outsider=person("Omar", "omar@example.com"), admin=admin, admin2=admin2, stranger_admin=stranger_admin,
                             root={**super_admin_user(), "name": "Platform Root"})
    make_registration(db, event, "priya@example.com")
    make_registration(db, event, "ravi@example.com")
    make_registration(db, event, "cara@example.com", status="cancelled")

    notices: list[dict] = []
    seen: set = set()
    monkeypatch.setattr(moderation, "classify", fake_classify)
    monkeypatch.setattr(notify, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(notify.notification_idempotency, "claim", lambda _db, key: key not in seen and not seen.add(key))
    monkeypatch.setattr(notify.notification_idempotency, "release", lambda _db, key: seen.discard(key))
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda _db, **kw: notices.append(kw) or SimpleNamespace(id=uuid4()))
    monkeypatch.setattr("app.services.event_notification_service.resolve_enterprise_admin_user_ids", lambda *a, **k: ([], tenant))

    @contextmanager
    def session_factory():
        yield db

    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)
    yield SimpleNamespace(db=db, event=event, tenant=tenant, notices=notices, people=people, monkeypatch=monkeypatch)
    reset_overrides()
    db.close()


def as_user(env, user):
    return client_for(env.db, user)


def base(env):
    return f"{API}/{env.event.id}/discussion"


def say(env, user, text, **extra):
    return as_user(env, user).post(f"{base(env)}/messages", json={"body": text, **extra})


def opened(env, user):
    """Everyone is known to the discussion once they open it."""
    assert as_user(env, user).get(f"{base(env)}/access").status_code == 200


def kinds(env, category):
    return [n for n in env.notices if n["category"] == category]


# --- access ------------------------------------------------------------------------------------------------------------------

def test_only_registered_participants_and_the_events_enterprise_admin_can_open_the_discussion(env):
    p = env.people
    open_for = {u["id"]: as_user(env, u).get(f"{base(env)}/access").json() for u in (p.priya, p.admin, p.root)}
    assert open_for[p.priya["id"]] | {} == {"event_id": str(env.event.id), "event_name": "Health Harmony Summit", "role": "participant", "can_read": True,
                                           "can_post": True, "can_moderate": False, "is_closed": False, "locked_reason": None, "message": None}
    assert open_for[p.admin["id"]]["role"] == "enterprise_admin" and open_for[p.admin["id"]]["can_moderate"] is True
    assert open_for[p.root["id"]]["role"] == "super_admin" and open_for[p.root["id"]]["can_read"] and not open_for[p.root["id"]]["can_post"]

    locked = as_user(env, p.outsider).get(f"{base(env)}/access").json()
    assert locked["can_read"] is False and locked["locked_reason"] == "NOT_REGISTERED" and locked["message"] == "Register for this event to join the discussion."
    gone = as_user(env, p.cancelled).get(f"{base(env)}/access").json()
    assert gone["can_read"] is False and gone["locked_reason"] == "REGISTRATION_CANCELLED"
    other = as_user(env, p.stranger_admin).get(f"{base(env)}/access").json()
    assert other["can_read"] is False and other["locked_reason"] == "NOT_EVENT_ADMIN"

    for user in (p.outsider, p.cancelled, p.stranger_admin):
        denied = as_user(env, user).get(f"{base(env)}/messages")
        assert denied.status_code == 403 and denied.json()["code"] == "DISCUSSION_LOCKED"
        assert say(env, user, "hello").status_code == 403
    assert as_user(env, None).get(f"{base(env)}/access").status_code == 401


def test_cancelling_a_registration_revokes_access(env):
    assert as_user(env, env.people.ravi).get(f"{base(env)}/messages").status_code == 200
    from app.models.event_aux_models import EventRegistration
    env.db.query(EventRegistration).filter_by(participant_email="ravi@example.com").update({"status": "cancelled"})
    env.db.commit()
    assert as_user(env, env.people.ravi).get(f"{base(env)}/messages").json()["code"] == "DISCUSSION_LOCKED"


def test_a_closed_discussion_stays_readable_but_nothing_new_can_be_posted(env):
    first = say(env, env.people.priya, "Before the event").json()["message"]
    env.event.status = "completed"
    env.db.commit()
    assert as_user(env, env.people.priya).get(f"{base(env)}/access").json()["is_closed"] is True
    assert as_user(env, env.people.ravi).get(f"{base(env)}/messages").json()["pagination"]["total"] == 1
    closed = say(env, env.people.ravi, "Too late")
    assert closed.status_code == 409 and closed.json()["code"] == "DISCUSSION_CLOSED" and first["status"] == "published"


# --- safe messages and replies -------------------------------------------------------------------------------------------------

def test_a_safe_message_is_published_at_once_and_a_reply_notifies_the_person_replied_to(env):
    p = env.people
    opened(env, p.priya)
    posted = say(env, p.priya, "Is parking available at the venue?")
    assert posted.status_code == 201 and posted.json()["user_message"] is None
    message = posted.json()["message"]
    assert message["status"] == "published" and message["is_mine"] is True and message["author"] == {"id": p.priya["id"], "name": "Priya", "role": "participant"}

    seen_by_ravi = as_user(env, p.ravi).get(f"{base(env)}/messages").json()
    assert [m["body"] for m in seen_by_ravi["items"]] == ["Is parking available at the venue?"] and seen_by_ravi["items"][0]["is_mine"] is False

    reply = say(env, p.ravi, "Yes, near gate 2.", parent_id=message["id"]).json()["message"]
    assert reply["parent_id"] == message["id"] and reply["root_id"] == message["id"]
    [note] = kinds(env, "event_discussion_reply")
    assert note["user_ids"][0] == __import__("uuid").UUID(p.priya["id"]) and note["message"] == "Someone replied to your discussion message."
    meta = note["metadata"]
    assert meta["event_id"] == str(env.event.id) and meta["message_id"] == reply["id"] and meta["parent_id"] == message["id"] and meta["event_name"] == "Health Harmony Summit"
    assert meta["deep_link"] == settings.EVENT_DISCUSSION_LINK_PARTICIPANT.replace("{event_id}", str(env.event.id)).replace("{message_id}", reply["id"])
    assert "reason" not in meta

    top = as_user(env, p.priya).get(f"{base(env)}/messages").json()["items"][0]
    assert top["reply_count"] == 1
    detail = as_user(env, p.priya).get(f"{base(env)}/messages/{message['id']}").json()
    assert [r["body"] for r in detail["replies"]] == ["Yes, near gate 2."]
    assert say(env, p.priya, "thanks", parent_id=message["id"]).status_code == 201
    assert len(kinds(env, "event_discussion_reply")) == 1                         # replying to yourself, or to a message of your own, notifies nobody


def test_a_reply_to_an_enterprise_admin_goes_to_the_admins_own_screen(env):
    p = env.people
    opened(env, p.admin)
    top = say(env, p.admin, "Welcome everyone").json()["message"]
    say(env, p.priya, "Thank you!", parent_id=top["id"])
    [note] = kinds(env, "event_discussion_reply")
    assert note["message"] == "A participant replied to your discussion."
    assert note["metadata"]["deep_link"].startswith("/enterprise/events/")


def test_message_rules(env):
    p = env.people
    assert say(env, p.priya, "   ").status_code == 422
    too_long = say(env, p.priya, "x" * (settings.EVENT_DISCUSSION_MAX_CHARS + 1))
    assert too_long.status_code == 422 and too_long.json()["code"] == "MESSAGE_TOO_LONG"
    assert say(env, p.priya, "Same text").status_code == 201
    assert say(env, p.priya, "Same text").json()["code"] == "DUPLICATE_MESSAGE"
    assert say(env, p.priya, "reply", parent_id=str(uuid4())).json()["code"] == "PARENT_NOT_AVAILABLE"
    assert say(env, p.priya, "Big news", is_announcement=True).json()["code"] == "ANNOUNCEMENT_ADMIN_ONLY"


# --- flagged messages ---------------------------------------------------------------------------------------------------------

def test_a_flagged_message_is_hidden_and_reaches_the_enterprise_admin_who_approves_it(env):
    p = env.people
    opened(env, p.admin)
    opened(env, p.ravi)
    sent = say(env, p.priya, "This is a badword moment")
    assert sent.status_code == 201
    assert sent.json()["message"]["status"] == "pending_review" and sent.json()["user_message"] == "Your message is pending admin review."
    mid = sent.json()["message"]["id"]

    assert as_user(env, p.ravi).get(f"{base(env)}/messages").json()["items"] == []                      # hidden from others
    assert as_user(env, p.ravi).get(f"{base(env)}/messages/{mid}").json()["code"] == "MESSAGE_UNAVAILABLE"
    mine = as_user(env, p.priya).get(f"{base(env)}/messages/mine", params={"status": "pending_review"}).json()["items"]
    assert [m["status_message"] for m in mine] == ["Your message is pending admin review."]

    [flag] = kinds(env, "event_discussion_flagged")
    assert flag["message"] == "A discussion message requires your review." and str(flag["user_ids"][0]) == p.admin["id"]
    assert flag["metadata"]["deep_link"] == settings.EVENT_DISCUSSION_LINK_ADMIN_FLAGGED.replace("{event_id}", str(env.event.id)).replace("{message_id}", mid)
    assert flag["metadata"]["reason"] == "AI flagged: harassment" and flag["metadata"]["is_appeal"] is False

    queue = as_user(env, p.admin).get(f"{base(env)}/moderation/queue").json()
    assert queue["pending_count"] == 1 and queue["pagination"]["total"] == 1
    item = queue["items"][0]
    assert item["event_name"] == "Health Harmony Summit" and item["sender_name"] == "Priya" and item["body"] == "This is a badword moment"
    assert item["flagging_reason"] == "AI flagged: harassment" and item["status"] == "pending_review" and item["submitted_at"]

    approved = as_user(env, p.admin).post(f"{base(env)}/messages/{mid}/decision", json={"action": "approve", "note": "Fine in context"})
    assert approved.status_code == 200 and approved.json()["message"]["status"] == "published"
    assert [m["body"] for m in as_user(env, p.ravi).get(f"{base(env)}/messages").json()["items"]] == ["This is a badword moment"]
    [ok] = kinds(env, "event_discussion_message_approved")
    assert ok["message"] == "Your discussion message has been approved." and str(ok["user_ids"][0]) == p.priya["id"]
    assert as_user(env, p.admin).get(f"{base(env)}/moderation/queue").json()["pending_count"] == 0
    assert as_user(env, p.admin).post(f"{base(env)}/messages/{mid}/decision", json={"action": "delete"}).json()["code"] == "INVALID_STATUS"


def test_deleting_a_flagged_message_means_it_is_never_published(env):
    p = env.people
    opened(env, p.admin)
    mid = say(env, p.priya, "badword again").json()["message"]["id"]

    deleted = as_user(env, p.admin).post(f"{base(env)}/messages/{mid}/decision", json={"action": "delete", "note": "Spam"})
    assert deleted.status_code == 200 and deleted.json()["message"]["status"] == "deleted"
    assert as_user(env, p.ravi).get(f"{base(env)}/messages").json()["items"] == []
    gone = as_user(env, p.ravi).get(f"{base(env)}/messages/{mid}")
    assert gone.status_code == 404 and gone.json()["detail"] == "This message is no longer available."
    [note] = kinds(env, "event_discussion_message_deleted")
    assert note["message"] == "Your discussion message was not approved." and note["metadata"]["status"] == "deleted"
    mine = as_user(env, p.priya).get(f"{base(env)}/messages/{mid}").json()
    assert mine["status"] == "deleted" and mine["status_message"] == "Your message was not approved."          # the sender still sees what happened
    history = as_user(env, p.admin).get(f"{base(env)}/moderation/history", params={"message_id": mid}).json()["items"]
    assert [h["action"] for h in history] == ["deleted", "flagged", "submitted"]
    assert history[0]["actor_name"] == "Anita Admin" and history[0]["actor_role"] == "enterprise_admin" and history[0]["reason"] == "Spam"
    assert history[1]["actor_role"] == "system" and history[1]["label"] == "flagged"


def test_only_the_events_enterprise_admin_decides_and_nobody_approves_their_own_message(env):
    p = env.people
    opened(env, p.admin)
    opened(env, p.admin2)
    mid = say(env, p.priya, "badword").json()["message"]["id"]
    for user in (p.priya, p.ravi, p.stranger_admin):
        assert as_user(env, user).post(f"{base(env)}/messages/{mid}/decision", json={"action": "approve"}).status_code == 403
        assert as_user(env, user).get(f"{base(env)}/moderation/queue").status_code == 403
    assert as_user(env, p.priya).get(f"{base(env)}/moderation/history").status_code == 403

    own = say(env, p.admin, "my badword post").json()                      # an Enterprise Admin's own message is judged too
    assert own["message"]["status"] == "pending_review"
    refused = as_user(env, p.admin).post(f"{base(env)}/messages/{own['message']['id']}/decision", json={"action": "approve"})
    assert refused.status_code == 403 and refused.json()["code"] == "SELF_REVIEW"
    other_flag = [n for n in kinds(env, "event_discussion_flagged") if n["metadata"]["message_id"] == own["message"]["id"]]
    assert [str(u) for u in other_flag[0]["user_ids"]] == [p.admin2["id"]]
    assert as_user(env, p.admin2).post(f"{base(env)}/messages/{own['message']['id']}/decision", json={"action": "approve"}).status_code == 200


# --- extreme messages, editing and appeal -------------------------------------------------------------------------------------------

def test_an_extreme_message_is_blocked_and_can_be_edited_and_sent_again(env):
    p = env.people
    sent = say(env, p.priya, "i will killyou")
    body = sent.json()
    assert sent.status_code == 201 and body["message"]["status"] == "blocked" and body["user_message"] == BLOCKED
    assert as_user(env, p.ravi).get(f"{base(env)}/messages").json()["items"] == []
    assert not kinds(env, "event_discussion_flagged") and not kinds(env, "event_discussion_reply")          # never broadcast

    mid = body["message"]["id"]
    assert as_user(env, p.ravi).put(f"{base(env)}/messages/{mid}", json={"body": "x"}).status_code == 404
    again = as_user(env, p.priya).put(f"{base(env)}/messages/{mid}", json={"body": "See you all there"})
    assert again.status_code == 200 and again.json()["message"]["status"] == "published" and again.json()["user_message"] is None
    assert as_user(env, p.priya).put(f"{base(env)}/messages/{mid}", json={"body": "edit a published one"}).json()["code"] == "INVALID_STATUS"
    actions = [h.action for h in env.db.query(EventDiscussionModeration).filter_by(message_id=__import__("uuid").UUID(mid)).order_by(EventDiscussionModeration.created_at).all()]
    assert actions == ["submitted", "blocked", "edited_resubmitted", "auto_published"]


def test_a_blocked_message_can_be_appealed_once_and_goes_to_the_enterprise_admin(env):
    p = env.people
    opened(env, p.admin)
    mid = say(env, p.priya, "killyou").json()["message"]["id"]
    appeal = as_user(env, p.priya).post(f"{base(env)}/messages/{mid}/appeal", json={"note": "It was a joke about a game"})
    assert appeal.status_code == 200 and appeal.json()["message"]["status"] == "pending_review" and appeal.json()["message"]["is_appeal"] is True
    [flag] = kinds(env, "event_discussion_flagged")
    assert flag["message"] == "A discussion message appeal requires your review." and flag["metadata"]["is_appeal"] is True
    assert as_user(env, p.priya).post(f"{base(env)}/messages/{mid}/appeal", json={}).json()["code"] == "INVALID_STATUS"
    assert as_user(env, p.ravi).post(f"{base(env)}/messages/{mid}/appeal", json={}).status_code == 404
    assert as_user(env, p.admin).post(f"{base(env)}/messages/{mid}/decision", json={"action": "delete"}).status_code == 200


def test_if_the_ai_is_unavailable_the_message_stays_unpublished_until_it_is_checked(env):
    p = env.people
    sent = say(env, p.priya, "ai-down hello everyone").json()
    assert sent["message"]["status"] == "pending_moderation" and "being checked" in sent["user_message"]
    assert as_user(env, p.ravi).get(f"{base(env)}/messages").json()["items"] == []
    assert env.db.query(EventDiscussionModeration).filter_by(action="ai_unavailable").count() == 1

    env.monkeypatch.setattr(moderation, "classify", lambda text: moderation.Verdict("safe", None, [], "test"))
    assert jobs.retry_moderation(env.db, datetime.utcnow() + timedelta(minutes=5)) == 1
    assert [m["body"] for m in as_user(env, p.ravi).get(f"{base(env)}/messages").json()["items"]] == ["ai-down hello everyone"]


def test_the_moderation_layers_never_publish_by_default(monkeypatch):
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_MODERATION", "off")
    with pytest.raises(moderation.ModerationUnavailable):
        moderation.classify("hello")
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_MODERATION", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    with pytest.raises(moderation.ModerationUnavailable):
        moderation.classify("hello")
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_MODERATION", "rules")
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_BLOCK_WORDS", "paduvaa, bad word")
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_FLAG_WORDS", "loosu")
    assert moderation.classify("hello friends").label == "safe"
    assert moderation.classify("You PADUVAA!").label == "extreme"
    assert moderation.classify("that is a bad  word").label == "safe"                 # whole phrase only
    assert moderation.classify("he is a loosu").label == "flagged"
    assert moderation.classify("paduvaaaa").label == "safe"                            # a whole word, not a part of one


def test_openai_scores_become_safe_flagged_or_extreme(monkeypatch):
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_MODERATION", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "key")

    class Reply:
        def __init__(self, result):
            self.result = result

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [self.result]}

    def answer(result):
        monkeypatch.setattr(moderation.requests, "post", lambda *a, **k: Reply(result))
        return moderation.classify("some text")

    assert answer({"categories": {}, "category_scores": {"harassment": 0.01}}).label == "safe"
    flagged = answer({"categories": {"harassment": True}, "category_scores": {"harassment": 0.7}})
    assert flagged.label == "flagged" and "harassment" in flagged.reason
    assert answer({"categories": {}, "category_scores": {"hate": 0.5}}).label == "flagged"
    assert answer({"categories": {"harassment/threatening": True}, "category_scores": {"harassment/threatening": 0.95}}).label == "extreme"

    def boom(*a, **k):
        raise TimeoutError()

    monkeypatch.setattr(moderation.requests, "post", boom)
    with pytest.raises(moderation.ModerationUnavailable):
        moderation.classify("some text")


# --- Super Admin: read only --------------------------------------------------------------------------------------------------------

def test_the_super_admin_can_watch_but_never_post_or_decide(env):
    p = env.people
    say(env, p.priya, "A normal question")
    flagged = say(env, p.priya, "badword!").json()["message"]["id"]
    root = p.root

    assert [m["body"] for m in as_user(env, root).get(f"{base(env)}/messages").json()["items"]] == ["A normal question"]
    assert as_user(env, root).get(f"{base(env)}/moderation/queue").json()["items"][0]["message_id"] == flagged
    assert len(as_user(env, root).get(f"{base(env)}/moderation/history").json()["items"]) >= 4
    assert as_user(env, root).get(f"{ADMIN_API}/flagged").json()["pagination"]["total"] == 1
    row = as_user(env, root).get(f"{ADMIN_API}/activity").json()["items"][0]
    assert row["event_name"] == "Health Harmony Summit" and row["published"] == 1 and row["pending_review"] == 1

    assert say(env, root, "hello").json()["code"] == "SUPER_ADMIN_READ_ONLY"
    assert as_user(env, root).post(f"{base(env)}/messages/{flagged}/decision", json={"action": "approve"}).json()["code"] == "SUPER_ADMIN_READ_ONLY"
    assert as_user(env, root).post(f"{base(env)}/messages/{flagged}/appeal", json={}).status_code == 403
    assert as_user(env, root).put(f"{base(env)}/messages/{flagged}", json={"body": "edit"}).status_code == 403


def test_lists_across_events_are_limited_to_the_admins_own_enterprise(env):
    p = env.people
    other_tenant = uuid4()
    other_event = make_event(env.db, other_tenant, make_enterprise(env.db, other_tenant, "Other Events"), title="Other summit", status="published")
    make_registration(env.db, other_event, "omar@example.com")
    say(env, p.priya, "badword in apollo")
    as_user(env, p.outsider).post(f"{API}/{other_event.id}/discussion/messages", json={"body": "badword in other"})

    mine = as_user(env, p.admin).get(f"{ADMIN_API}/flagged").json()
    assert [i["body"] for i in mine["items"]] == ["badword in apollo"] and mine["pending_count"] == 1
    theirs = as_user(env, p.stranger_admin).get(f"{ADMIN_API}/flagged").json()
    assert theirs["items"] == [] and as_user(env, p.admin).get(f"{ADMIN_API}/activity").json()["pagination"]["total"] == 1
    assert len(as_user(env, p.root).get(f"{ADMIN_API}/flagged").json()["items"]) == 2
    assert as_user(env, p.priya).get(f"{ADMIN_API}/flagged").status_code == 403


# --- announcements and read state ----------------------------------------------------------------------------------------------------

def test_an_announcement_notifies_the_participants_who_joined_and_is_pinned_first(env):
    p = env.people
    opened(env, p.priya)
    opened(env, p.ravi)
    opened(env, p.admin)
    say(env, p.priya, "A question")
    note = say(env, p.admin, "Doors open at 9 am", is_announcement=True).json()["message"]
    assert note["is_announcement"] is True
    [sent] = kinds(env, "event_discussion_announcement")
    assert sorted(str(u) for u in sent["user_ids"]) == sorted([p.priya["id"], p.ravi["id"]]) and sent["message"] == "New announcement in your event discussion."
    assert [m["body"] for m in as_user(env, p.ravi).get(f"{base(env)}/messages", params={"order": "newest"}).json()["items"]] == ["Doors open at 9 am", "A question"]


def test_opening_a_message_marks_the_reply_and_its_notifications_read(env):
    p = env.people
    top = say(env, p.priya, "Any tips for parking?").json()["message"]
    reply = say(env, p.ravi, "Use gate 2", parent_id=top["id"]).json()["message"]
    listing = as_user(env, p.priya).get(f"{base(env)}/messages/{top['id']}").json()
    assert listing["replies"][0]["is_unread_reply"] is False                                                   # shown once, then read
    row = env.db.query(EventDiscussionMessage).filter_by(id=__import__("uuid").UUID(reply["id"])).one()
    assert row.recipient_read_at is not None

    notification = Notification(title="t", message="m", notification_type="automatic", category="event_discussion_reply", status="sent",
                                metadata_json={"message_id": top["id"]})
    env.db.add(notification)
    env.db.flush()
    env.db.add(UserNotification(notification_id=notification.id, user_id=__import__("uuid").UUID(p.priya["id"]), is_read=False))
    env.db.commit()
    as_user(env, p.priya).get(f"{base(env)}/messages/{top['id']}")
    assert env.db.query(UserNotification).one().is_read is True


# --- nudges -------------------------------------------------------------------------------------------------------------------------

def test_an_unread_reply_is_nudged_once_and_never_after_it_was_opened(env):
    p = env.people
    top = say(env, p.priya, "Question?").json()["message"]
    unread = say(env, p.ravi, "Answer one", parent_id=top["id"]).json()["message"]
    now = datetime.utcnow()

    assert jobs.nudge_unread_replies(env.db, now + timedelta(hours=1)) == 0                       # too early
    assert jobs.nudge_unread_replies(env.db, now + timedelta(hours=13)) == 1
    [nudge] = kinds(env, "event_discussion_unread_reply")
    assert nudge["message"] == "You have an unread reply in your event discussion." and nudge["metadata"]["message_id"] == unread["id"] and nudge["metadata"]["nudge"] is True
    assert jobs.nudge_unread_replies(env.db, now + timedelta(days=3)) == 0                        # once only

    other = say(env, p.ravi, "Answer two", parent_id=top["id"]).json()["message"]
    as_user(env, p.priya).get(f"{base(env)}/messages/{top['id']}")                                  # Priya reads the thread
    assert jobs.nudge_unread_replies(env.db, now + timedelta(days=4)) == 0 and other["id"]


def test_quiet_hours_hold_nudges_back(env, monkeypatch):
    p = env.people
    top = say(env, p.priya, "Question?").json()["message"]
    say(env, p.ravi, "Answer", parent_id=top["id"])
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_QUIET_START_HOUR", 22)
    monkeypatch.setattr(settings, "EVENT_DISCUSSION_QUIET_END_HOUR", 7)
    late = datetime.utcnow().replace(hour=23, minute=0) + timedelta(days=2)
    assert jobs.in_quiet_hours(late) and jobs.nudge_unread_replies(env.db, late) == 0
    assert jobs.nudge_unread_replies(env.db, late.replace(hour=10)) == 1


def test_pending_flagged_messages_nudge_the_enterprise_admin_a_limited_number_of_times(env):
    p = env.people
    opened(env, p.admin)
    say(env, p.priya, "badword one")
    say(env, p.ravi, "badword two")
    now = datetime.utcnow()

    assert jobs.nudge_pending_reviews(env.db, now + timedelta(hours=2)) == 0
    assert jobs.nudge_pending_reviews(env.db, now + timedelta(hours=25)) == 1
    [first] = kinds(env, "event_discussion_pending_review")
    assert first["message"] == "You have 2 flagged discussion messages waiting for review." and str(first["user_ids"][0]) == p.admin["id"]
    assert first["metadata"]["deep_link"] == settings.EVENT_DISCUSSION_LINK_ADMIN_QUEUE.replace("{event_id}", str(env.event.id)) and first["metadata"]["pending_count"] == 2
    assert jobs.nudge_pending_reviews(env.db, now + timedelta(hours=30)) == 0                         # not again so soon
    assert jobs.nudge_pending_reviews(env.db, now + timedelta(hours=50)) == 1
    assert jobs.nudge_pending_reviews(env.db, now + timedelta(hours=75)) == 1
    assert jobs.nudge_pending_reviews(env.db, now + timedelta(hours=100)) == 0                        # the cap of 3

    queue = as_user(env, p.admin).get(f"{base(env)}/moderation/queue").json()["items"]
    for item in queue:
        as_user(env, p.admin).post(f"{base(env)}/messages/{item['message_id']}/decision", json={"action": "delete"})
    assert jobs.nudge_pending_reviews(env.db, now + timedelta(hours=200)) == 0                        # nothing waiting: the reminder stops
    assert env.db.query(EventDiscussionNudge).count() == 0


def test_run_all_runs_every_job(env):
    assert jobs.run_all(env.db) == {"moderated": 0, "unread_nudges": 0, "pending_nudges": 0}
