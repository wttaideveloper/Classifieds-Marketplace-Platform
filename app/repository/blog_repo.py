from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.blog_model import Blog, BlogCategory, BlogApprovalLog


def get_blog_by_id(db: Session, blog_id):
    return db.query(Blog).filter(Blog.id == blog_id).first()


def get_blog_by_slug(db: Session, slug: str):
    return db.query(Blog).filter(Blog.slug == slug).first()


def create_blog(db: Session, blog: Blog):
    db.add(blog)
    db.commit()
    db.refresh(blog)
    return blog


def update_blog(db: Session, blog: Blog):
    try:
        db.commit()
        db.refresh(blog)
        return blog
    except Exception:
        db.rollback()
        raise


def delete_blog(db: Session, blog: Blog):
    db.delete(blog)
    db.commit()


def list_merchant_blogs_repo(
    db: Session,
    merchant_id,
    search: str = None,
    status_filter: str = None,
    page: int = 1,
    limit: int = 10,
):
    query = db.query(Blog).filter(Blog.merchant_id == merchant_id)

    if search:
        query = query.filter(
            or_(
                Blog.title.ilike(f"%{search}%"),
                Blog.short_description.ilike(f"%{search}%"),
            )
        )

    if status_filter:
        query = query.filter(Blog.status == status_filter)

    query = query.order_by(Blog.created_at.desc())

    total = query.count()
    skip = (page - 1) * limit
    blogs = query.offset(skip).limit(limit).all()
    return total, blogs


def list_admin_blogs_repo(
    db: Session,
    merchant_id=None,
    status_filter: str = None,
    approval_filter: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 10,
):
    query = db.query(Blog)

    if merchant_id:
        query = query.filter(Blog.merchant_id == merchant_id)

    if status_filter:
        query = query.filter(Blog.status == status_filter)

    if approval_filter:
        query = query.filter(Blog.approval_status == approval_filter)

    if search:
        query = query.filter(
            or_(
                Blog.title.ilike(f"%{search}%"),
                Blog.short_description.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Blog.created_at.desc())

    total = query.count()
    skip = (page - 1) * limit
    blogs = query.offset(skip).limit(limit).all()
    return total, blogs


def list_public_blogs_repo(
    db: Session,
    category_id=None,
    search: str = None,
    page: int = 1,
    limit: int = 10,
):
    query = db.query(Blog).filter(
        Blog.approval_status == "APPROVED",
        Blog.status == "PUBLISHED",
        Blog.is_published == True,
    )

    if category_id:
        query = query.filter(Blog.category_id == category_id)

    if search:
        query = query.filter(
            or_(
                Blog.title.ilike(f"%{search}%"),
                Blog.short_description.ilike(f"%{search}%"),
                Blog.content.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Blog.created_at.desc())

    total = query.count()
    skip = (page - 1) * limit
    blogs = query.offset(skip).limit(limit).all()
    return total, blogs


def get_category_by_id(db: Session, category_id):
    return db.query(BlogCategory).filter(BlogCategory.id == category_id).first()


def get_category_by_slug(db: Session, slug: str):
    return db.query(BlogCategory).filter(BlogCategory.slug == slug).first()


def get_category_by_name(db: Session, name: str):
    return db.query(BlogCategory).filter(BlogCategory.name == name).first()


def create_category(db: Session, category: BlogCategory):
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def update_category(db: Session, category: BlogCategory):
    try:
        db.commit()
        db.refresh(category)
        return category
    except Exception:
        db.rollback()
        raise


def delete_category(db: Session, category: BlogCategory):
    db.delete(category)
    db.commit()


def list_categories_repo(db: Session):
    categories = (
        db.query(BlogCategory)
        .filter(BlogCategory.is_active == True)
        .order_by(BlogCategory.created_at.desc())
        .all()
    )
    return len(categories), categories


def create_approval_log(db: Session, log: BlogApprovalLog):
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
