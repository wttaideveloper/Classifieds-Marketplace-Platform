import re
import uuid
from fastapi import status
from sqlalchemy.orm import Session
from app.exceptions.custom_exception import CustomException
from app.models.blog_model import Blog, BlogCategory, BlogApprovalLog
from app.repository.blog_repo import (
    get_blog_by_id,
    get_blog_by_slug,
    create_blog,
    update_blog,
    delete_blog,
    list_merchant_blogs_repo,
    list_admin_blogs_repo,
    list_public_blogs_repo,
    get_category_by_id,
    get_category_by_name,
    create_category,
    update_category,
    delete_category,
    list_categories_repo,
    create_approval_log,
)

def _to_uuid(value: str, field_name: str):
    try:
        return uuid.UUID(str(value))
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")

def _to_int(value: str, field_name: str) -> int:
    try:
        return int(value)
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")

def _slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text or str(uuid.uuid4())

def _unique_blog_slug(db: Session, base: str) -> str:
    slug = base
    i = 2
    while True:
        existing = get_blog_by_slug(db, slug)
        if not existing:
            return slug
        slug = f"{base}-{i}"
        i += 1

def _unique_category_slug(db: Session, base: str) -> str:
    slug = base
    i = 2
    while True:
        existing = db.query(BlogCategory).filter(BlogCategory.slug == slug).first()
        if not existing:
            return slug
        slug = f"{base}-{i}"
        i += 1

def _blog_to_dict(blog: Blog):
    return {
        "id": str(blog.id),
        "merchantId": str(blog.merchant_id),
        "categoryId": str(blog.category_id) if blog.category_id else None,
        "title": blog.title,
        "slug": blog.slug,
        "shortDescription": blog.short_description,
        "content": blog.content,
        "featuredImage": blog.featured_image,
        "status": blog.status,
        "approvalStatus": blog.approval_status,
        "isPublished": blog.is_published,
        "createdAt": blog.created_at,
        "updatedAt": blog.updated_at,
    }

def _category_to_dict(cat: BlogCategory):
    return {
        "id": str(cat.id),
        "name": cat.name,
        "slug": cat.slug,
        "description": cat.description,
        "isActive": cat.is_active,
        "createdAt": cat.created_at,
    }

# MERCHANT: CREATE BLOG
def create_blog_service(db: Session, merchant_id: str, payload):
    try:
        merchant_uuid = _to_uuid(merchant_id, "merchantId")

        category_uuid = None
        if getattr(payload, "categoryId", None):
            category_uuid = _to_uuid(payload.categoryId, "categoryId")
            category = get_category_by_id(db, category_uuid)
            if not category or not category.is_active:
                raise CustomException(400, "Invalid category")

        base_slug = _slugify(payload.title)
        slug = _unique_blog_slug(db, base_slug)

        blog = Blog(
            merchant_id=merchant_uuid,
            category_id=category_uuid,
            title=payload.title,
            slug=slug,
            short_description=payload.shortDescription,
            content=payload.content,
            featured_image=payload.featuredImage,
            status="DRAFT",
            approval_status="PENDING",
            is_published=False,
        )

        blog = create_blog(db, blog)

        return {
            "success": True,
            "message": "Blog created successfully",
            "data": _blog_to_dict(blog),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# MERCHANT: UPDATE BLOG
def update_blog_service(db: Session, merchant_id: str, blog_id: str, payload):
    try:
        merchant_uuid = _to_uuid(merchant_id, "merchantId")
        blog_uuid = _to_uuid(blog_id, "blogId")

        blog = get_blog_by_id(db, blog_uuid)
        if not blog or str(blog.merchant_id) != str(merchant_uuid):
            raise CustomException(404, "Blog not found")

        if blog.approval_status == "APPROVED" and blog.status == "PUBLISHED":
            if getattr(payload, "content", None) or getattr(payload, "title", None):
                blog.approval_status = "PENDING"
                blog.is_published = False
                blog.status = "DRAFT"

        if getattr(payload, "title", None) is not None:
            blog.title = payload.title
            base_slug = _slugify(payload.title)
            slug = _unique_blog_slug(db, base_slug)
            blog.slug = slug

        if getattr(payload, "shortDescription", None) is not None:
            blog.short_description = payload.shortDescription

        if getattr(payload, "content", None) is not None:
            blog.content = payload.content

        if getattr(payload, "featuredImage", None) is not None:
            blog.featured_image = payload.featuredImage

        if getattr(payload, "categoryId", None) is not None:
            if payload.categoryId:
                category_uuid = _to_uuid(payload.categoryId, "categoryId")
                category = get_category_by_id(db, category_uuid)
                if not category or not category.is_active:
                    raise CustomException(400, "Invalid category")
                blog.category_id = category_uuid
            else:
                blog.category_id = None

        if getattr(payload, "status", None) is not None:
            blog.status = str(payload.status)

        if getattr(payload, "isPublished", None) is not None:
            blog.is_published = bool(payload.isPublished)

        blog = update_blog(db, blog)

        return {
            "success": True,
            "message": "Blog updated successfully",
            "data": _blog_to_dict(blog),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# MERCHANT: DELETE BLOG
def delete_blog_service(db: Session, merchant_id: str, blog_id: str):
    try:
        merchant_uuid = _to_uuid(merchant_id, "merchantId")
        blog_uuid = _to_uuid(blog_id, "blogId")

        blog = get_blog_by_id(db, blog_uuid)
        if not blog or str(blog.merchant_id) != str(merchant_uuid):
            raise CustomException(404, "Blog not found")

        delete_blog(db, blog)

        return {"success": True, "message": "Blog deleted successfully"}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# MERCHANT: LIST BLOGS
def get_merchant_blogs_service(
    db: Session,
    merchant_id: str,
    search: str = None,
    status_filter: str = None,
    page: int = 1,
    limit: int = 10,
):
    try:
        merchant_uuid = _to_uuid(merchant_id, "merchantId")
        total, blogs = list_merchant_blogs_repo(
            db=db,
            merchant_id=merchant_uuid,
            search=search,
            status_filter=status_filter,
            page=page,
            limit=limit,
        )
        return {
            "success": True,
            "message": "Blogs fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": [_blog_to_dict(b) for b in blogs],
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# MERCHANT: SUBMIT FOR APPROVAL
def submit_blog_for_approval_service(db: Session, merchant_id: str, blog_id: str):
    try:
        merchant_uuid = _to_uuid(merchant_id, "merchantId")
        blog_uuid = _to_uuid(blog_id, "blogId")

        blog = get_blog_by_id(db, blog_uuid)
        if not blog or str(blog.merchant_id) != str(merchant_uuid):
            raise CustomException(404, "Blog not found")

        blog.approval_status = "PENDING"
        blog.status = "DRAFT"
        blog.is_published = False

        update_blog(db, blog)

        return {"success": True, "message": "Blog submitted for approval"}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# ADMIN: PENDING BLOGS
def get_pending_blogs_service(db: Session, page: int = 1, limit: int = 10, search: str = None):
    try:
        total, blogs = list_admin_blogs_repo(
            db=db,
            approval_filter="PENDING",
            page=page,
            limit=limit,
            search=search,
        )
        return {
            "success": True,
            "message": "Pending blogs fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": [_blog_to_dict(b) for b in blogs],
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# ADMIN: APPROVE BLOG
def approve_blog_service(db: Session, admin_id: str, blog_id: str):
    try:
        admin_int = _to_int(admin_id, "adminId")
        blog_uuid = _to_uuid(blog_id, "blogId")

        blog = get_blog_by_id(db, blog_uuid)
        if not blog:
            raise CustomException(404, "Blog not found")

        blog.approval_status = "APPROVED"
        blog.status = "PUBLISHED"
        blog.is_published = True

        update_blog(db, blog)

        log = BlogApprovalLog(
            blog_id=blog_uuid,
            admin_id=admin_int,
            action="APPROVED",
            remarks=None,
        )
        try:
            create_approval_log(db, log)
        except Exception:
            db.rollback()

        return {"success": True, "message": "Blog approved successfully"}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# ADMIN: REJECT BLOG
def reject_blog_service(db: Session, admin_id: str, blog_id: str, remarks: str):
    try:
        admin_int = _to_int(admin_id, "adminId")
        blog_uuid = _to_uuid(blog_id, "blogId")

        blog = get_blog_by_id(db, blog_uuid)
        if not blog:
            raise CustomException(404, "Blog not found")

        blog.approval_status = "REJECTED"
        blog.status = "DRAFT"
        blog.is_published = False

        update_blog(db, blog)

        log = BlogApprovalLog(
            blog_id=blog_uuid,
            admin_id=admin_int,
            action="REJECTED",
            remarks=remarks,
        )
        try:
            create_approval_log(db, log)
        except Exception:
            db.rollback()

        return {"success": True, "message": "Blog rejected successfully"}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# ADMIN: LIST BLOGS
def admin_list_blogs_service(
    db: Session,
    merchant_id: str = None,
    status_filter: str = None,
    approval_filter: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 10,
):
    try:
        merchant_uuid = _to_uuid(merchant_id, "merchantId") if merchant_id else None

        total, blogs = list_admin_blogs_repo(
            db=db,
            merchant_id=merchant_uuid,
            status_filter=status_filter,
            approval_filter=approval_filter,
            search=search,
            page=page,
            limit=limit,
        )
        return {
            "success": True,
            "message": "Blogs fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": [_blog_to_dict(b) for b in blogs],
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# PUBLIC: LIST BLOGS
def public_list_blogs_service(
    db: Session,
    category_id: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 10,
):
    try:
        category_uuid = _to_uuid(category_id, "categoryId") if category_id else None
        total, blogs = list_public_blogs_repo(
            db=db,
            category_id=category_uuid,
            search=search,
            page=page,
            limit=limit,
        )
        return {
            "success": True,
            "message": "Blogs fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": [_blog_to_dict(b) for b in blogs],
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# PUBLIC: BLOG DETAILS
def public_blog_details_service(db: Session, slug: str):
    try:
        blog = get_blog_by_slug(db, slug)
        if (
            not blog
            or blog.approval_status != "APPROVED"
            or blog.status != "PUBLISHED"
            or not blog.is_published
        ):
            raise CustomException(404, "Blog not found")

        return {
            "success": True,
            "message": "Blog details fetched successfully",
            "data": _blog_to_dict(blog),
        }
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

# BLOG CATEGORIES: ADMIN CRUD + PUBLIC LIST
def create_blog_category_service(db: Session, payload):
    try:
        existing = get_category_by_name(db, payload.name)
        if existing:
            raise CustomException(400, "Category name already exists")

        base_slug = _slugify(payload.name)
        slug = _unique_category_slug(db, base_slug)

        cat = BlogCategory(
            name=payload.name,
            slug=slug,
            description=payload.description,
            is_active=True,
        )
        cat = create_category(db, cat)
        return {"success": True, "message": "Category created successfully", "data": _category_to_dict(cat)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

def update_blog_category_service(db: Session, category_id: str, payload):
    try:
        cat_uuid = _to_uuid(category_id, "categoryId")
        cat = get_category_by_id(db, cat_uuid)
        if not cat:
            raise CustomException(404, "Category not found")

        if getattr(payload, "name", None) is not None:
            existing = get_category_by_name(db, payload.name)
            if existing and str(existing.id) != str(cat.id):
                raise CustomException(400, "Category name already exists")
            cat.name = payload.name
            base_slug = _slugify(payload.name)
            cat.slug = _unique_category_slug(db, base_slug)

        if getattr(payload, "description", None) is not None:
            cat.description = payload.description

        if getattr(payload, "isActive", None) is not None:
            cat.is_active = bool(payload.isActive)

        cat = update_category(db, cat)

        return {"success": True, "message": "Category updated successfully", "data": _category_to_dict(cat)}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

def delete_blog_category_service(db: Session, category_id: str):
    try:
        cat_uuid = _to_uuid(category_id, "categoryId")
        cat = get_category_by_id(db, cat_uuid)
        if not cat:
            raise CustomException(404, "Category not found")
        delete_category(db, cat)
        return {"success": True, "message": "Category deleted successfully"}
    except CustomException as e:
        raise e
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))

def list_blog_categories_service(db: Session):
    try:
        total, cats = list_categories_repo(db)
        return {
            "success": True,
            "message": "Categories fetched successfully",
            "total": total,
            "data": [_category_to_dict(c) for c in cats],
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))