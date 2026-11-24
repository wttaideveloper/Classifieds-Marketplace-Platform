from math import ceil

from sqlalchemy.orm import Query


def apply_soft_delete_filter(query: Query, model, include_deleted: bool = False) -> Query:
    if include_deleted or not hasattr(model, "is_deleted"):
        return query
    return query.filter(model.is_deleted.is_(False))


def apply_pagination(query: Query, page: int, page_size: int) -> Query:
    offset = (page - 1) * page_size
    return query.offset(offset).limit(page_size)


def paginate_query(query: Query, page: int, page_size: int) -> tuple[list, int]:
    total = query.count()
    items = apply_pagination(query, page, page_size).all()
    return items, total


def build_pagination_meta(total: int, page: int, page_size: int) -> dict:
    total_pages = ceil(total / page_size) if page_size else 0
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def apply_ilike_search(query: Query, columns: list, search: str | None) -> Query:
    if not search or not search.strip():
        return query

    term = f"%{search.strip()}%"
    from sqlalchemy import or_

    return query.filter(or_(*[column.ilike(term) for column in columns]))
