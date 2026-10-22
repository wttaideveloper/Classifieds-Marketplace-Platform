import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CompletionRule = Literal["read_to_end", "opened", "manual"]
ImageWidth = Literal["small", "medium", "large", "full"]
ImageFit = Literal["cover", "contain"]
ImagePosition = Literal["center", "top", "bottom", "left", "right", "top left", "top right", "bottom left", "bottom right"]
ImagePlacement = Literal["before_content", "after_content", "inline"]
EbookStatus = Literal["draft", "pending_approval", "changes_requested", "approved"]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RATIO_RE = re.compile(r"^(original|\d{1,2}:\d{1,2})$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean(value: str | None) -> str | None:
    return None if value is None else " ".join(value.split())


class EbookImageIn(_Strict):
    """A picture on a page. `asset_id` comes from the image upload; width, fit, aspect_ratio and position are display settings that
    the apps apply (nothing is cropped on the server)."""
    id: str | None = Field(None, description="Your id for this picture block. Generated when left out")
    asset_id: str = Field(..., min_length=1, max_length=64)
    alt_text: str | None = Field(None, max_length=500, description="Required before the eBook can be sent for approval, unless `decorative` is true")
    caption: str | None = Field(None, max_length=500)
    width: ImageWidth = "full"
    fit: ImageFit = "cover"
    aspect_ratio: str = Field("original", description="original, or w:h such as 16:9, 4:3, 1:1")
    position: ImagePosition = Field("center", description="Which part of the picture stays in view when it is cropped to the aspect ratio")
    decorative: bool = Field(False, description="A picture that adds no meaning; its alt text may then be empty")
    placement: ImagePlacement = Field("after_content", description="before_content, after_content, or inline (where the page's HTML has a data-image-id placeholder with this picture's id)")
    # Added by the server when the eBook is read. Sending them back is allowed and they are ignored.
    url: str | None = Field(None, exclude=True)
    natural_width: int | None = Field(None, exclude=True)
    natural_height: int | None = Field(None, exclude=True)
    available: bool | None = Field(None, exclude=True)

    @field_validator("id")
    @classmethod
    def _id(cls, value):
        if value is not None and not _ID_RE.match(value):
            raise ValueError("an id has 1 to 64 letters, digits, - or _")
        return value

    @field_validator("alt_text", "caption")
    @classmethod
    def _text(cls, value):
        return _clean(value)

    @field_validator("aspect_ratio")
    @classmethod
    def _ratio(cls, value):
        value = (value or "original").strip()
        if not _RATIO_RE.match(value) or (value != "original" and (int(value.split(":")[0]) == 0 or int(value.split(":")[1]) == 0)):
            raise ValueError("original, or a ratio like 16:9")
        return value


class EbookPageIn(_Strict):
    id: str | None = Field(None, description="Your id for the page (page_1 ...). Generated when left out. Keep it the same between saves")
    order: int | None = Field(None, ge=1, description="Position of the page. The list order is used when this is left out")
    title: str | None = Field("", max_length=200)
    content_html: str | None = Field("", description="Rich text. Unsafe markup is removed on save")
    images: list[EbookImageIn] = Field(default_factory=list)
    html_notes: list[str] | None = Field(None, exclude=True, description="Added by the server (what was removed from the text). Ignored when sent back")

    @field_validator("id")
    @classmethod
    def _id(cls, value):
        if value is not None and not _ID_RE.match(value):
            raise ValueError("an id has 1 to 64 letters, digits, - or _")
        return value

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        return _clean(value) or ""


class EbookDocumentIn(_Strict):
    subtitle: str | None = Field(None, max_length=255)
    author: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=5000)
    cover_asset_id: str | None = Field(None, max_length=64)
    cover_url: str | None = Field(None, exclude=True, description="Added by the server. Ignored when sent back")
    completion_rule: CompletionRule = Field("read_to_end", description="read_to_end: the learner reaches the end of the last page. opened: opening it is enough. manual: the learner marks it done")
    allow_bookmarks: bool = True
    allow_notes: bool = True
    pages: list[EbookPageIn] = Field(default_factory=list)

    @field_validator("subtitle", "author")
    @classmethod
    def _short(cls, value):
        return _clean(value)


class EbookSaveRequest(_Strict):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "base_version": 3,
        "ebook": {"subtitle": "An introduction to nutrition", "author": "Invigorate Health", "completion_rule": "read_to_end", "allow_bookmarks": True, "allow_notes": True,
                  "pages": [{"id": "page_1", "order": 1, "title": "Introduction", "content_html": "<h2>Introduction</h2><p>Nutrition is...</p>",
                             "images": [{"id": "image_block_1", "asset_id": "5f0c0a52-6a0c-4f0e-9a52-0b7d0e2a9c11", "alt_text": "Healthy food diagram",
                                         "caption": "Examples of healthy food", "width": "full", "fit": "cover", "aspect_ratio": "16:9", "position": "center"}]}]}}})

    ebook: EbookDocumentIn
    base_version: int | None = Field(None, ge=0, description="The version you loaded. If someone saved since, the save is refused with 409 instead of overwriting their work")


class ReorderRequest(_Strict):
    page_ids: list[str] = Field(..., min_length=1, description="Every page id, in the new order")
    base_version: int | None = Field(None, ge=0)


class ValidateRequest(_Strict):
    ebook: EbookDocumentIn | None = Field(None, description="Check this unsaved content instead of the saved draft")


class SubmitRequest(_Strict):
    note: str | None = Field(None, max_length=1000, description="A note for the reviewer")
    base_version: int | None = Field(None, ge=0)


class ReviewRequest(_Strict):
    action: Literal["approve", "request_changes"]
    note: str | None = Field(None, max_length=1000, description="Required to request changes; the author reads it")
    publish: bool = Field(True, description="Approve and make it live for learners in one step. False = approve now, publish later")


class ValidationItem(BaseModel):
    page_id: str | None = None
    image_id: str | None = None
    field: str
    code: str
    message: str


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[ValidationItem] = Field(default_factory=list)
    warnings: list[ValidationItem] = Field(default_factory=list)
    page_count: int = 0
    version: int | None = None


class SaveResponse(BaseModel):
    id: UUID
    lesson_id: str
    version: int
    status: str
    updated_at: datetime
    published_version: int | None = None
    has_unpublished_changes: bool = True
    warnings: list[ValidationItem] = Field(default_factory=list)


class Person(BaseModel):
    id: str | None = None
    name: str | None = None


class EbookView(BaseModel):
    """The eBook as the author, or the reviewer, sees it: the draft, and where it is in the approval."""
    id: UUID | None = None
    training_id: UUID
    section_id: str
    lesson_id: str
    version: int
    status: str
    status_label: str
    published_version: int | None = None
    published_at: datetime | None = None
    has_unpublished_changes: bool = False
    reviewer_note: str | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: Person | None = None
    actions: list[str] = Field(default_factory=list)
    imported_from_lesson_content: bool = Field(False, description="True when nothing is saved yet and this was read from the old JSON in the lesson's content field")
    ebook: dict[str, Any]


class LearnerEbook(BaseModel):
    """What a learner may read: the published copy, never a draft."""
    id: UUID
    training_id: UUID
    lesson_id: str
    title: str | None = None
    version: int
    published_at: datetime | None = None
    page_count: int
    ebook: dict[str, Any]
    published_version: int
    subtitle: str | None = None
    author: str | None = None
    description: str | None = None
    cover_url: str | None = None
    completion_rule: str
    allow_bookmarks: bool
    allow_notes: bool
    current_page_id: str | None = None
    pages: list[dict[str, Any]] = Field(default_factory=list)


class HistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action: str
    version: int | None = None
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    created_at: datetime


class VersionEntry(BaseModel):
    version: int
    kind: str
    created_by: str | None = None
    created_at: datetime


class HistoryResponse(BaseModel):
    items: list[HistoryEntry]
    versions: list[VersionEntry]
    total: int


class ReviewView(BaseModel):
    """What a Super Admin needs to decide: the draft, what is live, the problems, and who changed it last."""
    training_id: UUID
    training_title: str | None = None
    section_id: str
    lesson_id: str
    lesson_title: str | None = None
    status: str
    status_label: str
    version: int
    published_version: int | None = None
    reviewer_note: str | None = None
    submitted_at: datetime | None = None
    submitted_by: Person | None = None
    last_editor: Person | None = None
    last_updated: datetime | None = None
    draft: dict[str, Any] | None = None
    published: dict[str, Any] | None = None
    validation: ValidationResponse | None = None
    changes: dict[str, Any] | None = Field(None, description="What differs between the draft and the published copy")
    history: list[HistoryEntry] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)


class QueueItem(BaseModel):
    training_id: UUID
    training_title: str | None = None
    section_id: str
    lesson_id: str
    lesson_title: str | None = None
    version: int
    page_count: int
    submitted_at: datetime | None = None
    submitted_by: Person | None = None


class QueueResponse(BaseModel):
    items: list[QueueItem]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------- learner reading state

class ProgressIn(_Strict):
    page_id: str = Field(..., min_length=1, max_length=64)
    page_number: int | None = Field(None, ge=1, description="Ignored if it does not match the page; the page decides")
    progress_percent: int | None = Field(None, ge=0, le=100, description="Legacy field; ignored. Progress is calculated by the server.")
    last_scroll_position: float | None = Field(None, ge=0)


class ProgressOut(BaseModel):
    published_version: int | None = None
    last_page_id: str | None = None
    last_page_number: int | None = None
    last_scroll_position: float | None = None
    progress_percent: int = 0
    completed: bool = False
    completed_at: datetime | None = None
    page_count: int = 0
    version_read: int | None = None
    lesson_completed: bool = Field(False, description="The lesson counts as done in the course progress")


class BookmarkIn(_Strict):
    page_id: str = Field(..., min_length=1, max_length=64)
    label: str | None = Field(None, max_length=200)
    scroll_position: float | None = Field(None, ge=0)

    @field_validator("label")
    @classmethod
    def _label(cls, value):
        return _clean(value)


class BookmarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    page_id: str
    page_number: int | None = None
    page_exists: bool = True
    label: str | None = None
    scroll_position: float | None = None
    created_at: datetime


class BookmarkList(BaseModel):
    items: list[BookmarkOut]
    total: int


class NoteIn(_Strict):
    page_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=5000)
    quote: str | None = Field(None, max_length=1000, description="The passage the note is about")
    color: Literal["yellow", "green", "blue", "pink", "orange"] | None = None
    scroll_position: float | None = Field(None, ge=0)


class NoteUpdate(_Strict):
    text: str | None = Field(None, min_length=1, max_length=5000)
    color: Literal["yellow", "green", "blue", "pink", "orange"] | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    page_id: str
    page_number: int | None = None
    page_exists: bool = True
    text: str
    quote: str | None = None
    color: str | None = None
    scroll_position: float | None = None
    created_at: datetime
    updated_at: datetime


class NoteList(BaseModel):
    items: list[NoteOut]
    total: int


class MediaAssetOut(BaseModel):
    asset_id: str
    url: str
    name: str | None = None
    size: int
    type: str
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    scan_status: str
    created_at: datetime
    used_by: list[dict] = Field(default_factory=list)
