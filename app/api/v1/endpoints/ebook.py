from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import extract_access_token, get_current_user
from app.db.database import get_db
from app.schemas.ebook_schema import (
    BookmarkIn, BookmarkList, BookmarkOut, EbookSaveRequest, EbookView, HistoryResponse, LearnerEbook, NoteIn, NoteList, NoteOut, NoteUpdate, ProgressIn,
    ProgressOut, QueueResponse, ReorderRequest, ReviewRequest, ReviewView, SaveResponse, SubmitRequest, ValidateRequest, ValidationResponse,
)
from app.services import ebook_service as svc

router = APIRouter(tags=["eBooks"])
admin_router = APIRouter(tags=["eBooks (Super Admin review)"])

BASE = "/{training_id}/sections/{section_id}/lessons/{lesson_id}/ebook"
LEARNER = "/{training_id}/lessons/{lesson_id}/ebook"
TID = Path(..., description="Training id")


def _token(request: Request) -> str | None:
    return extract_access_token(request)


# ---------------------------------------------------------------- author (Enterprise Admin / provider of the training's business)

@router.get(BASE, response_model=EbookView, summary="Load an eBook for editing",
            description="Returns the **draft** with its pictures resolved (`url`, `natural_width`, `natural_height`), the approval `status`, the live `published_version` and the "
                        "`actions` you can take. When nothing is saved yet it returns an empty eBook (version 0), or the old JSON from the lesson's `content` field "
                        "(`imported_from_lesson_content: true`). Super Admins may read it too.")
def get_ebook_for_editing(request: Request, training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                          db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.get_for_authoring(db, training_id, section_id, lesson_id, user, _token(request))


@router.put(BASE, response_model=SaveResponse, summary="Save the whole eBook draft",
            description="Replaces the draft with the `ebook` sent (pages, text, pictures and their display settings, completion rule, bookmark / note switches). Text is cleaned of unsafe "
                        "markup (what was removed comes back in `warnings`). Send `base_version` (the version you loaded) so you do not overwrite someone else's save: a newer "
                        "version gives 409 `VERSION_CONFLICT`. Refused with 409 `IN_REVIEW` while the eBook waits for approval. A save after approval returns it to `draft`; what is "
                        "live does not change until the new draft is approved and published.")
def save_ebook(request: Request, payload: EbookSaveRequest = Body(...), training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
               db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.save_draft(db, training_id, section_id, lesson_id, user, _token(request), payload)


@router.post(BASE + "/validate", response_model=ValidationResponse, summary="Check the eBook before sending it for approval",
             description="Checks the saved draft (or the `ebook` in the body): at least one page, every page titled and not empty, every picture has alt text (unless `decorative`), "
                         "every picture exists in this business's library and its file is there, no unsupported markup, a valid page order. `valid: false` lists each problem with the `page_id`, "
                         "`image_id` and `field`.")
def validate_ebook(request: Request, payload: ValidateRequest | None = Body(None), training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                   db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.validate(db, training_id, section_id, lesson_id, user, _token(request), payload)


@router.patch(BASE + "/pages/reorder", summary="Change the order of the pages",
              description="`page_ids` must hold every page id exactly once. Makes a new draft version.")
def reorder_ebook_pages(request: Request, payload: ReorderRequest = Body(...), training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                        db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.reorder_pages(db, training_id, section_id, lesson_id, user, _token(request), payload)


@router.post(BASE + "/submit", response_model=EbookView, summary="Send the eBook for approval",
             description="Needs a valid draft (422 `EBOOK_INVALID` with the list of problems otherwise) and at least one change from what is live. Locks the draft until a Super Admin decides. "
                         "The Super Admins are notified.")
def submit_ebook(request: Request, payload: SubmitRequest = Body(default_factory=SubmitRequest), training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                 db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.submit(db, training_id, section_id, lesson_id, user, _token(request), payload)


@router.post(BASE + "/withdraw", response_model=EbookView, summary="Take the eBook back from review so it can be edited")
def withdraw_ebook(request: Request, training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                   db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.withdraw(db, training_id, section_id, lesson_id, user, _token(request))


@router.post(BASE + "/publish", response_model=EbookView, summary="Make an approved eBook live",
             description="Only an approved eBook can be published; nothing unapproved ever reaches learners. (Approving with `publish: true` does this in one step.)")
def publish_ebook(request: Request, training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                  db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.publish(db, training_id, section_id, lesson_id, user, _token(request))


@router.post(BASE + "/unpublish", response_model=EbookView, summary="Take the live eBook down",
             description="Learners get 404 until a new version is approved and published. The draft and its history are kept.")
def unpublish_ebook(request: Request, training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                    db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.unpublish(db, training_id, section_id, lesson_id, user, _token(request))


@router.get(BASE + "/history", response_model=HistoryResponse, summary="Who changed the eBook, and when",
            description="Every save, reorder, submission, decision and publication (newest first), and the versions kept (when it was sent, approved, published).")
def ebook_history(request: Request, training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                  db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.history(db, training_id, section_id, lesson_id, user, _token(request))


@router.get(BASE + "/versions/{version}", summary="One kept version of the eBook",
            description="`kind` (submitted, approved or published) picks which when the same version was kept more than once.")
def ebook_version(request: Request, version: int = Path(..., ge=1), kind: str | None = Query(None, pattern="^(submitted|approved|published)$"),
                  training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.get_version(db, training_id, section_id, lesson_id, version, kind, user, _token(request))


@router.post(BASE + "/versions/{version}/restore", response_model=EbookView, summary="Copy a kept version back into the draft")
def restore_ebook_version(request: Request, version: int = Path(..., ge=1), training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                          db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.restore_version(db, training_id, section_id, lesson_id, version, user, _token(request))


# ---------------------------------------------------------------- the learner: published content only

@router.get(LEARNER, response_model=LearnerEbook, summary="Read a published eBook (learner)",
            description="Only the **published** copy, after the course's own checks: an active enrolment, a training that is open, and a lesson that is not locked or in draft. "
                        "Pictures have a `url` that needs the same sign-in. 404 when nothing is published. The training's own staff and Super Admins can read the same copy to preview it.")
def read_ebook(request: Request, training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.learner_view(db, training_id, lesson_id, user, _token(request))


@router.get(LEARNER + "/progress", response_model=ProgressOut, summary="Where I stopped reading (resume)",
            description="`last_page_id` is null before reading. If a new published version removed the saved page, resume from page one.")
def get_reading_progress(request: Request, training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.get_progress(db, training_id, lesson_id, user, _token(request))


@router.put(LEARNER + "/progress", response_model=ProgressOut, summary="Save where I am",
            description="Send `page_id`. The server calculates the page number and percentage; legacy `progress_percent` and `page_number` are ignored. "
                        "The last page completes a read_to_end ebook; an opened ebook completes on any save.")
def save_reading_progress(request: Request, payload: ProgressIn = Body(...), training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db),
                          user: dict = Depends(get_current_user)):
    return svc.save_progress(db, training_id, lesson_id, user, _token(request), payload)


@router.post(LEARNER + "/progress/complete", response_model=ProgressOut, summary="Mark the eBook as read (completion_rule: manual)")
def complete_ebook(request: Request, training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.complete_manually(db, training_id, lesson_id, user, _token(request))


@router.get(LEARNER + "/bookmarks", response_model=BookmarkList, summary="My bookmarks in this eBook", description="In page order. `page_exists: false` when a later version removed the page.")
def list_bookmarks(request: Request, training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.list_bookmarks(db, training_id, lesson_id, user, _token(request))


@router.post(LEARNER + "/bookmarks", response_model=BookmarkOut, status_code=status.HTTP_201_CREATED, summary="Bookmark a page",
             description="Only when the eBook allows bookmarks (403 otherwise). The same page and position again returns the bookmark you already have (200).")
def add_bookmark(request: Request, response: Response, payload: BookmarkIn = Body(...), training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    item, created = svc.add_bookmark(db, training_id, lesson_id, user, _token(request), payload)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return item


@router.delete(LEARNER + "/bookmarks/{bookmark_id}", summary="Remove a bookmark")
def delete_bookmark(request: Request, bookmark_id: UUID, training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    svc.delete_bookmark(db, training_id, lesson_id, bookmark_id, user, _token(request))
    return {"message": "Bookmark removed"}


@router.get(LEARNER + "/notes", response_model=NoteList, summary="My notes in this eBook", description="Only your own. `page_id` narrows to one page.")
def list_notes(request: Request, page_id: str | None = Query(None), training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db),
               user: dict = Depends(get_current_user)):
    return svc.list_notes(db, training_id, lesson_id, user, _token(request), page_id)


@router.post(LEARNER + "/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED, summary="Add a note",
             description="Only when the eBook allows notes (403 otherwise). A note is plain text; any markup in it is removed.")
def add_note(request: Request, payload: NoteIn = Body(...), training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.add_note(db, training_id, lesson_id, user, _token(request), payload)


@router.patch(LEARNER + "/notes/{note_id}", response_model=NoteOut, summary="Change my note")
def update_note(request: Request, note_id: UUID, payload: NoteUpdate = Body(...), training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db),
                user: dict = Depends(get_current_user)):
    return svc.update_note(db, training_id, lesson_id, note_id, user, _token(request), payload)


@router.delete(LEARNER + "/notes/{note_id}", summary="Delete my note")
def delete_note(request: Request, note_id: UUID, training_id: UUID = TID, lesson_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    svc.delete_note(db, training_id, lesson_id, note_id, user, _token(request))
    return {"message": "Note deleted"}


# ---------------------------------------------------------------- Super Admin review

@admin_router.get("/trainings/{training_id}/sections/{section_id}/lessons/{lesson_id}/ebook", response_model=ReviewView, summary="Review an eBook (Super Admin)",
                  description="The draft and the published copy side by side with the validation errors, what changed, the last editor and the recent history. "
                              "Without `include_drafts=true` only the published copy comes back.")
def review_ebook(request: Request, include_drafts: bool = Query(False), training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                 db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.review_view(db, training_id, section_id, lesson_id, user, _token(request), include_drafts=include_drafts)


@admin_router.post("/trainings/{training_id}/sections/{section_id}/lessons/{lesson_id}/ebook/review", response_model=ReviewView, summary="Approve, or ask for changes (Super Admin)",
                   description="Only for an eBook waiting for approval. `request_changes` needs a `note` the author reads. `approve` re-checks the eBook and, with `publish: true` (default), makes it "
                               "live for learners at once; with `publish: false` it is approved and the owner publishes later. Only a Super Admin can do this.")
def decide_ebook(request: Request, payload: ReviewRequest = Body(...), training_id: UUID = TID, section_id: str = Path(...), lesson_id: str = Path(...),
                 db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.review(db, training_id, section_id, lesson_id, user, _token(request), payload)


@admin_router.get("/ebooks/pending", response_model=QueueResponse, summary="eBooks waiting for approval (Super Admin)", description="Oldest first.")
def pending_ebooks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.review_queue(db, user, page, page_size)
