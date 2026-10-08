"""Rules every review module shares (training/course, product, service, event).

One place for: what a valid rating is, how an average is taken, what a moderation action may be and how a
reviewer may be shown. Keeping them here is what makes the modules behave the same way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException, Query

MODERATION_STATUSES = ("pending", "approved", "rejected")
MAX_COMMENT_LENGTH = 2000


def parse_rating(raw) -> int | None:
    """A stored rating is text. Only a whole number from 1 to 5 counts; anything else is None."""
    text = str(raw).strip() if raw is not None else ""
    return int(text) if text.isdigit() and 1 <= int(text) <= 5 else None


def validate_rating(raw) -> int:
    """For input: a whole number 1 to 5 (an int, or text such as "4"), else 422."""
    if isinstance(raw, bool):
        raise HTTPException(status_code=422, detail="rating must be a whole number from 1 to 5")
    value = parse_rating(raw)
    if value is None:
        raise HTTPException(status_code=422, detail="rating must be a whole number from 1 to 5")
    return value


def clean_comment(raw) -> str | None:
    """Trim the comment; empty becomes None. Longer than the limit is a 422."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="comment must be text")
    text = raw.strip()
    if len(text) > MAX_COMMENT_LENGTH:
        raise HTTPException(status_code=422, detail=f"comment must be at most {MAX_COMMENT_LENGTH} characters")
    return text or None


def average_rating(ratings: Iterable[int | None]) -> float | None:
    """Mean of the usable ratings rounded to 2 decimals; None (never 0) when there are none."""
    usable = [r for r in ratings if r is not None]
    return round(sum(usable) / len(usable), 2) if usable else None


def rating_distribution(ratings: Iterable[int | None]) -> dict[str, int]:
    """How many reviews gave 5, 4, 3, 2 and 1 stars (always all five keys)."""
    counts = {str(star): 0 for star in range(5, 0, -1)}
    for rating in ratings:
        if rating is not None:
            counts[str(rating)] += 1
    return counts


def validate_action(action) -> str:
    if action not in MODERATION_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid moderation action. Allowed: {sorted(MODERATION_STATUSES)}")
    return action


def public_name(value) -> str | None:
    """A name that is safe to show to anyone. An email address is never a display name."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text if text and "@" not in text else None


# --- options for the public review lists ----------------------------------------------------------------
# sort (newest | oldest | highest | lowest), rating (only that many stars), with_comment, and optional paging.
# With no page / page_size the list still returns everything, as before. The average, the count and the star
# breakdown always describe ALL approved reviews, whatever filter or page is asked for.

SORTS = ("newest", "oldest", "highest", "lowest")


@dataclass(frozen=True)
class ListOptions:
    sort: str = "newest"
    rating: int | None = None
    with_comment: bool = False
    page: int | None = None
    page_size: int | None = None

    @property
    def paged(self) -> bool:
        return self.page is not None or self.page_size is not None


def review_list_params(
    sort: str = Query("newest", description="newest (default) | oldest | highest | lowest"),
    rating: int | None = Query(None, ge=1, le=5, description="Only reviews with this many stars"),
    with_comment: bool = Query(False, description="Only reviews that have a comment"),
    page: int | None = Query(None, ge=1, description="Page number. Leave page and page_size out to get every review"),
    page_size: int | None = Query(None, ge=1, le=100, description="Reviews per page (default 20 when paging)"),
) -> ListOptions:
    """FastAPI dependency for the public review lists."""
    if sort not in SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of: {', '.join(SORTS)}")
    return ListOptions(sort=sort, rating=rating, with_comment=with_comment, page=page, page_size=page_size)


def apply_list_options(rows, opts: ListOptions, *, rating_of, created_of, comment_of):
    """(rows for this request, pagination dict) after filtering, sorting and paging `rows`."""
    items = list(rows)
    if opts.rating is not None:
        items = [r for r in items if rating_of(r) == opts.rating]
    if opts.with_comment:
        items = [r for r in items if (comment_of(r) or "").strip()]

    items.sort(key=created_of, reverse=(opts.sort != "oldest"))
    if opts.sort == "highest":
        items.sort(key=lambda r: rating_of(r) or 0, reverse=True)   # stable: ties stay newest first
    elif opts.sort == "lowest":
        items.sort(key=lambda r: rating_of(r) or 0)

    total = len(items)
    if not opts.paged:
        return items, {"total": total, "page": 1, "page_size": total, "total_pages": 1 if total else 0}
    page, size = opts.page or 1, opts.page_size or 20
    start = (page - 1) * size
    return items[start:start + size], {"total": total, "page": page, "page_size": size, "total_pages": -(-total // size)}
