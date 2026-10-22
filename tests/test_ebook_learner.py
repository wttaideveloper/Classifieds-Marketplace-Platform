"""What a learner gets from an eBook lesson: only the published copy, behind the course's own access rules; reading progress and
resume; bookmarks; notes."""
import json

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.models.ebook_model import TrainingEbook
from app.models.training_model import TrainingEnrolment, TrainingProgress
from tests.ebook_support import LESSON, SECTION, decide, doc, go_live, image_block, page, save, saved, submit, upload, world  # noqa: F401

THREE = [page("page_1", "One"), page("page_2", "Two"), page("page_3", "Three")]


def lesson_of(world):
    return world.training.sections[0]["lessons"][0]


def touch(world):
    flag_modified(world.training, "sections")
    world.db.commit()


# --- reading ---------------------------------------------------------------------------------------------------------------------------------------

def test_a_learner_reads_the_published_copy_with_picture_addresses(world):
    asset = upload(world, size=(1200, 800))
    go_live(world, doc([page("page_1", "Intro", images=[image_block(asset, width="medium")]), page("page_2", "Two")], cover_asset_id=asset))

    resp = world.as_user(world.people.learner).get(world.learner_base)

    assert resp.status_code == 200
    body = resp.json()
    assert body["lesson_id"] == LESSON and body["title"] == "Nutrition basics" and body["version"] == 1 and body["page_count"] == 2 and body["published_at"]
    first = body["ebook"]["pages"][0]
    assert first["id"] == "page_1" and first["order"] == 1 and first["title"] == "Intro" and first["content_html"].startswith("<h2>Intro</h2>")
    image = first["images"][0]
    assert image["width"] == "medium" and image["aspect_ratio"] == "16:9" and image["alt_text"] and image["caption"] and image["natural_width"] == 1200
    assert image["url"].endswith(f"/api/v1/media/{asset}/file") and body["ebook"]["cover_url"].endswith(f"/api/v1/media/{asset}/file")
    assert "html_notes" not in first and body["ebook"]["completion_rule"] == "read_to_end"


def test_a_learner_never_receives_unfinished_content(world):
    go_live(world, doc([page("page_1", "Live title", "<p>live text</p>")]))
    saved(world, doc([page("page_1", "DRAFT TITLE", "<p>unfinished draft</p>"), page("page_2", "DRAFT PAGE")]))                      # editing goes on
    submit(world)                                                                                                               # even waiting for approval

    text = world.as_user(world.people.learner).get(world.learner_base).text

    assert "Live title" in text and "DRAFT" not in text and "unfinished" not in text
    assert world.as_user(world.people.learner).get(world.learner_base).json()["version"] == 1


def test_nothing_is_available_until_it_is_published(world):
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 404
    saved(world)
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 404
    submit(world)
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 404
    decide(world, publish=False)
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 404            # approved is not live
    world.as_user(world.people.author).post(world.base + "/publish")
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 200
    world.as_user(world.people.author).post(world.base + "/unpublish")
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 404


def test_a_new_version_reaches_learners_only_when_it_is_published(world):
    go_live(world, doc([page("page_1", "Old")]))
    saved(world, doc([page("page_1", "New")]))
    submit(world)
    assert world.as_user(world.people.learner).get(world.learner_base).json()["ebook"]["pages"][0]["title"] == "Old"
    decide(world)

    assert world.as_user(world.people.learner).get(world.learner_base).json()["ebook"]["pages"][0]["title"] == "New"


# --- who may read ---------------------------------------------------------------------------------------------------------------------------------

def test_only_enrolled_learners_read_it(world):
    go_live(world)

    assert world.as_user(world.people.stranger).get(world.learner_base).status_code == 403
    assert world.as_user(None).get(world.learner_base).status_code == 401
    assert world.as_user(world.people.learner2).get(world.learner_base).status_code == 200


@pytest.mark.parametrize("status,expected", [("pending_approval", "ENROLMENT_PENDING_APPROVAL"), ("rejected", "ENROLMENT_NOT_ACTIVE"), ("cancelled", "ENROLMENT_NOT_ACTIVE")])
def test_an_enrolment_that_is_not_active_does_not_read(world, status, expected):
    go_live(world)
    world.db.query(TrainingEnrolment).filter_by(participant_email=world.people.learner["email"]).update({"status": status})
    world.db.commit()

    resp = world.as_user(world.people.learner).get(world.learner_base)

    assert resp.status_code == 403 and resp.json()["detail"]["code"] == expected


def test_a_training_that_is_not_open_is_not_readable(world):
    go_live(world)
    world.training.status = "draft"
    world.db.commit()

    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 403


def test_a_locked_or_draft_lesson_is_not_readable(world):
    go_live(world)
    lesson_of(world)["prerequisites"] = ["les-video"]
    touch(world)

    locked = world.as_user(world.people.learner).get(world.learner_base)
    assert locked.status_code == 403 and locked.json()["detail"]["code"] == "LESSON_LOCKED"
    world.db.add(TrainingProgress(training_id=world.training.id, participant_email=world.people.learner["email"], lessons_completed=["les-video"]))
    world.db.commit()
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 200

    lesson_of(world)["is_draft"] = True
    touch(world)
    assert world.as_user(world.people.learner).get(world.learner_base).status_code == 404


def test_the_trainings_own_staff_and_a_super_admin_may_preview_what_learners_see(world):
    go_live(world, doc([page("page_1", "Live")]))
    saved(world, doc([page("page_1", "Draft")]))

    for user in (world.people.author, world.people.provider, world.people.root):
        body = world.as_user(user).get(world.learner_base)
        assert body.status_code == 200 and body.json()["ebook"]["pages"][0]["title"] == "Live", user["name"]
    assert world.as_user(world.people.outsider).get(world.learner_base).status_code == 403


def test_a_lesson_that_is_not_an_ebook_is_refused(world):
    go_live(world)

    assert world.as_user(world.people.learner).get(world.learner_base.replace(LESSON, "les-video")).status_code == 409
    assert world.as_user(world.people.learner).get(world.learner_base.replace(LESSON, "nope")).status_code == 404


# --- reading progress and resume ------------------------------------------------------------------------------------------------------------------

def progress(world, user=None, **body):
    return world.as_user(user or world.people.learner).put(world.learner_base + "/progress", json=body)


def test_nothing_is_read_at_first(world):
    go_live(world, doc(THREE))

    assert world.as_user(world.people.learner).get(world.learner_base + "/progress").json() == {
        "last_page_id": None, "last_page_number": None, "last_scroll_position": None, "progress_percent": 0, "completed": False, "completed_at": None,
        "page_count": 3, "version_read": None, "lesson_completed": False}


def test_progress_is_saved_and_can_be_resumed(world):
    go_live(world, doc(THREE))

    saved_progress = progress(world, page_id="page_2", page_number=2, progress_percent=42, last_scroll_position=860).json()

    assert saved_progress["last_page_id"] == "page_2" and saved_progress["last_page_number"] == 2 and saved_progress["progress_percent"] == 42
    assert saved_progress["last_scroll_position"] == 860 and saved_progress["completed"] is False and saved_progress["version_read"] == 1
    resumed = world.as_user(world.people.learner).get(world.learner_base + "/progress").json()
    assert resumed["last_page_id"] == "page_2" and resumed["last_scroll_position"] == 860
    assert world.as_user(world.people.learner2).get(world.learner_base + "/progress").json()["last_page_id"] is None        # each learner has their own


def test_the_furthest_percent_never_goes_down_but_the_position_does(world):
    go_live(world, doc(THREE))
    progress(world, page_id="page_3", progress_percent=70, last_scroll_position=10)

    back = progress(world, page_id="page_1", progress_percent=10, last_scroll_position=5).json()

    assert back["progress_percent"] == 70 and back["last_page_id"] == "page_1" and back["last_scroll_position"] == 5


def test_the_page_decides_the_page_number_and_an_unknown_page_is_refused(world):
    go_live(world, doc(THREE))

    wrong_number = progress(world, page_id="page_3", page_number=1, progress_percent=50).json()

    assert wrong_number["last_page_number"] == 3
    assert progress(world, page_id="nope", progress_percent=50).status_code == 422
    assert progress(world, page_id="page_1", progress_percent=101).status_code == 422
    assert progress(world, page_id="page_1", progress_percent=-1).status_code == 422
    assert progress(world, page_id="page_1", progress_percent=5, last_scroll_position=-3).status_code == 422


def test_reading_to_the_end_completes_the_lesson_in_the_course(world):
    go_live(world, doc(THREE))
    progress(world, page_id="page_3", progress_percent=60)
    assert world.as_user(world.people.learner).get(world.learner_base + "/progress").json()["completed"] is False
    progress(world, page_id="page_2", progress_percent=100)                                         # 100% on a page that is not the last: not the end
    assert world.as_user(world.people.learner).get(world.learner_base + "/progress").json()["completed"] is False

    done = progress(world, page_id="page_3", progress_percent=100).json()

    assert done["completed"] is True and done["completed_at"] and done["lesson_completed"] is True
    world.db.expire_all()
    course = world.db.query(TrainingProgress).filter_by(participant_email=world.people.learner["email"]).one()
    assert LESSON in course.lessons_completed
    assert progress(world, page_id="page_1", progress_percent=5).json()["completed"] is True            # stays done


def test_the_opened_rule_completes_on_the_first_save(world):
    go_live(world, doc(THREE, completion_rule="opened"))

    assert progress(world, page_id="page_1", progress_percent=1).json()["completed"] is True


def test_the_manual_rule_waits_for_the_learner_to_say_so(world):
    go_live(world, doc(THREE, completion_rule="manual"))
    assert progress(world, page_id="page_3", progress_percent=100).json()["completed"] is False

    done = world.as_user(world.people.learner).post(world.learner_base + "/progress/complete").json()

    assert done["completed"] is True and done["progress_percent"] == 100 and done["lesson_completed"] is True
    assert world.as_user(world.people.learner).post(world.learner_base + "/progress/complete").status_code == 200       # again: nothing changes


def test_marking_done_is_only_for_the_manual_rule(world):
    go_live(world, doc(THREE))

    assert world.as_user(world.people.learner).post(world.learner_base + "/progress/complete").status_code == 409


def test_resume_survives_a_new_version_unless_the_page_was_removed(world):
    go_live(world, doc(THREE))
    progress(world, page_id="page_3", progress_percent=80, last_scroll_position=40)
    saved(world, doc([page("page_1", "One"), page("page_3", "Three, moved"), page("page_2", "Two")]))
    submit(world)
    decide(world)

    moved = world.as_user(world.people.learner).get(world.learner_base + "/progress").json()
    assert moved["last_page_id"] == "page_3" and moved["last_page_number"] == 2 and moved["progress_percent"] == 80          # renumbered by the new order

    saved(world, doc([page("page_1", "One"), page("page_2", "Two")]))
    submit(world)
    decide(world)
    gone = world.as_user(world.people.learner).get(world.learner_base + "/progress").json()
    assert gone["last_page_id"] is None and gone["last_page_number"] is None and gone["last_scroll_position"] is None and gone["progress_percent"] == 80


def test_progress_is_for_enrolled_learners_who_can_open_the_lesson(world):
    go_live(world, doc(THREE))

    assert world.as_user(world.people.stranger).get(world.learner_base + "/progress").status_code == 403
    assert progress(world, user=world.people.stranger, page_id="page_1", progress_percent=5).status_code == 403
    assert progress(world, user=world.people.author, page_id="page_1", progress_percent=5).status_code == 403            # staff preview; they do not read for credit
    lesson_of(world)["prerequisites"] = ["les-video"]
    touch(world)
    assert progress(world, page_id="page_3", progress_percent=100).status_code == 403                                     # a locked lesson cannot be completed by guessing a page id
    world.db.expire_all()
    assert world.db.query(TrainingProgress).count() == 0


# --- bookmarks ------------------------------------------------------------------------------------------------------------------------------------------

def bookmarks(world, user=None):
    return world.as_user(user or world.people.learner).get(world.learner_base + "/bookmarks")


def bookmark(world, user=None, **body):
    return world.as_user(user or world.people.learner).post(world.learner_base + "/bookmarks", json=body)


def test_bookmarks_are_added_listed_in_page_order_and_removed(world):
    go_live(world, doc(THREE))
    later = bookmark(world, page_id="page_3", label="Recipes")
    earlier = bookmark(world, page_id="page_1", scroll_position=120)

    assert later.status_code == 201 and later.json()["page_number"] == 3 and later.json()["label"] == "Recipes" and earlier.json()["scroll_position"] == 120
    listing = bookmarks(world).json()
    assert listing["total"] == 2 and [b["page_id"] for b in listing["items"]] == ["page_1", "page_3"]
    assert bookmarks(world, world.people.learner2).json()["total"] == 0
    assert world.as_user(world.people.learner).delete(f"{world.learner_base}/bookmarks/{later.json()['id']}").status_code == 200
    assert bookmarks(world).json()["total"] == 1
    assert world.as_user(world.people.learner).delete(f"{world.learner_base}/bookmarks/{later.json()['id']}").status_code == 404


def test_the_same_place_bookmarked_twice_returns_the_first_one(world):
    go_live(world, doc(THREE))
    first = bookmark(world, page_id="page_2", scroll_position=10)

    again = bookmark(world, page_id="page_2", scroll_position=10)

    assert again.status_code == 200 and again.json()["id"] == first.json()["id"] and bookmarks(world).json()["total"] == 1
    assert bookmark(world, page_id="page_2", scroll_position=99).status_code == 201


def test_nobody_removes_someone_elses_bookmark(world):
    go_live(world, doc(THREE))
    mine = bookmark(world, page_id="page_1").json()

    assert world.as_user(world.people.learner2).delete(f"{world.learner_base}/bookmarks/{mine['id']}").status_code == 404
    assert bookmarks(world).json()["total"] == 1


def test_bookmarks_follow_the_ebooks_switch_and_the_pages(world, monkeypatch):
    go_live(world, doc(THREE, allow_bookmarks=False))
    assert bookmark(world, page_id="page_1").status_code == 403
    saved(world, doc(THREE, allow_bookmarks=True))
    submit(world)
    decide(world)
    assert bookmark(world, page_id="nope").status_code == 422
    assert bookmark(world, page_id="page_3").status_code == 201
    from app.services import ebook_service

    monkeypatch.setattr(ebook_service, "MAX_BOOKMARKS", 1)
    assert bookmark(world, page_id="page_1").status_code == 409


def test_a_bookmark_on_a_page_that_was_removed_is_kept_and_marked(world):
    go_live(world, doc(THREE))
    bookmark(world, page_id="page_3")
    saved(world, doc([page("page_1", "One"), page("page_2", "Two")]))
    submit(world)
    decide(world)

    [item] = bookmarks(world).json()["items"]

    assert item["page_id"] == "page_3" and item["page_exists"] is False and item["page_number"] is None


def test_bookmarks_are_for_enrolled_learners_only(world):
    go_live(world, doc(THREE))

    assert bookmarks(world, world.people.stranger).status_code == 403
    assert bookmark(world, world.people.author, page_id="page_1").status_code == 403
    assert world.as_user(None).get(world.learner_base + "/bookmarks").status_code == 401


# --- notes -----------------------------------------------------------------------------------------------------------------------------------------------

def note(world, user=None, **body):
    return world.as_user(user or world.people.learner).post(world.learner_base + "/notes", json=body)


def test_notes_are_added_listed_changed_and_deleted(world):
    go_live(world, doc(THREE))
    made = note(world, page_id="page_2", text="Remember this", quote="Nutrition is the study of food", color="yellow", scroll_position=300)

    assert made.status_code == 201
    body = made.json()
    assert body["page_number"] == 2 and body["text"] == "Remember this" and body["quote"] == "Nutrition is the study of food" and body["color"] == "yellow" and body["scroll_position"] == 300
    note(world, page_id="page_1", text="First page note")
    everything = world.as_user(world.people.learner).get(world.learner_base + "/notes").json()
    assert everything["total"] == 2 and [n["page_id"] for n in everything["items"]] == ["page_1", "page_2"]
    only = world.as_user(world.people.learner).get(world.learner_base + "/notes", params={"page_id": "page_2"}).json()
    assert only["total"] == 1 and only["items"][0]["id"] == body["id"]

    changed = world.as_user(world.people.learner).patch(f"{world.learner_base}/notes/{body['id']}", json={"text": "Changed", "color": "green"}).json()
    assert changed["text"] == "Changed" and changed["color"] == "green" and changed["quote"] == "Nutrition is the study of food"
    assert world.as_user(world.people.learner).delete(f"{world.learner_base}/notes/{body['id']}").status_code == 200
    assert world.as_user(world.people.learner).get(world.learner_base + "/notes").json()["total"] == 1


def test_notes_are_private_to_their_owner(world):
    go_live(world, doc(THREE))
    mine = note(world, page_id="page_1", text="Private").json()

    assert world.as_user(world.people.learner2).get(world.learner_base + "/notes").json()["total"] == 0
    assert world.as_user(world.people.learner2).patch(f"{world.learner_base}/notes/{mine['id']}", json={"text": "Hijack"}).status_code == 404
    assert world.as_user(world.people.learner2).delete(f"{world.learner_base}/notes/{mine['id']}").status_code == 404
    assert world.as_user(world.people.learner).get(world.learner_base + "/notes").json()["items"][0]["text"] == "Private"


def test_a_note_is_plain_text(world):
    go_live(world, doc(THREE))

    made = note(world, page_id="page_1", text="<script>alert(1)</script><b>bold</b> text", quote="<img src=x onerror=y>quoted").json()

    assert "<" not in made["text"] and "<" not in made["quote"] and "bold text" in made["text"] and "quoted" in made["quote"]
    assert note(world, page_id="page_1", text="<i></i>").status_code == 422                       # nothing left once the markup is gone
    assert world.as_user(world.people.learner).patch(f"{world.learner_base}/notes/{made['id']}", json={"text": "<p></p>"}).status_code == 422


def test_notes_follow_the_ebooks_switch_pages_and_limits(world, monkeypatch):
    go_live(world, doc(THREE, allow_notes=False))
    assert note(world, page_id="page_1", text="x").status_code == 403
    saved(world, doc(THREE, allow_notes=True))
    submit(world)
    decide(world)
    assert note(world, page_id="nope", text="x").status_code == 422
    assert note(world, page_id="page_1", text="").status_code == 422
    assert note(world, page_id="page_1", text="x" * 5001).status_code == 422
    assert note(world, page_id="page_1", text="x", color="purple").status_code == 422
    from app.services import ebook_service

    monkeypatch.setattr(ebook_service, "MAX_NOTES", 1)
    assert note(world, page_id="page_1", text="one").status_code == 201
    assert note(world, page_id="page_1", text="two").status_code == 409


def test_notes_are_for_enrolled_learners_only(world):
    go_live(world, doc(THREE))

    assert note(world, world.people.stranger, page_id="page_1", text="x").status_code == 403
    assert note(world, world.people.root, page_id="page_1", text="x").status_code == 403
    assert world.as_user(None).get(world.learner_base + "/notes").status_code == 401


def test_a_note_on_a_page_that_was_removed_is_kept_and_marked(world):
    go_live(world, doc(THREE))
    note(world, page_id="page_3", text="Keep me")
    saved(world, doc([page("page_1", "One")]))
    submit(world)
    decide(world)

    [item] = world.as_user(world.people.learner).get(world.learner_base + "/notes").json()["items"]

    assert item["text"] == "Keep me" and item["page_exists"] is False and item["page_number"] is None
