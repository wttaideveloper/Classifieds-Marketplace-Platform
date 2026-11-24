"""Pagination metadata (copied from app/repository/query_utils.py)."""
from math import ceil


def build_pagination_meta(total: int, page: int, page_size: int) -> dict:
    total_pages = ceil(total / page_size) if page_size else 0
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }

