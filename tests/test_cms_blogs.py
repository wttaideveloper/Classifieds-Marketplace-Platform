from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.blog import router
from app.core.dependencies import get_current_super_admin


app = FastAPI()
app.include_router(router, prefix="/cms/blogs")
app.dependency_overrides[get_current_super_admin] = lambda: {
    "id": str(uuid4()),
    "role": "super_admin",
    "email": "super@example.com",
}
client = TestClient(app)

_BLOG_ID = str(uuid4())
_PAGINATION = {"total": 1, "page": 1, "page_size": 20, "total_pages": 1}


def _detail(**overrides):
    base = {
        "id": _BLOG_ID,
        "title": "Hello CMS",
        "slug": "hello-cms",
        "excerpt": "Short intro",
        "content": "<p>Body</p>",
        "cover_image_url": None,
        "author_name": "Editor",
        "author_id": None,
        "category": "Guides",
        "tags": ["health"],
        "status": "published",
        "seo_title": None,
        "seo_description": None,
        "tenant_id": None,
        "enterprise_id": None,
        "published_at": "2026-09-08T10:00:00",
        "view_count": 1,
        "created_by": "super",
        "updated_by": "super",
        "created_at": "2026-09-08T10:00:00",
        "updated_at": "2026-09-08T10:00:00",
    }
    base.update(overrides)
    return base


@patch("app.api.v1.endpoints.blog.list_blogs_service")
def test_list_public_blogs(mock_list):
    mock_list.return_value = {
        "items": [_detail()],
        "pagination": _PAGINATION,
    }
    response = client.get("/cms/blogs")
    assert response.status_code == 200
    assert mock_list.call_args.kwargs["public_only"] is True
    assert response.json()["items"][0]["slug"] == "hello-cms"


@patch("app.api.v1.endpoints.blog.get_blog_service")
def test_get_public_blog_by_slug(mock_get):
    mock_get.return_value = _detail()
    response = client.get("/cms/blogs/hello-cms")
    assert response.status_code == 200
    assert mock_get.call_args.kwargs["slug"] == "hello-cms"
    assert mock_get.call_args.kwargs["public_only"] is True


@patch("app.api.v1.endpoints.blog.create_blog_service")
def test_create_admin_blog(mock_create):
    mock_create.return_value = _detail(status="draft")
    response = client.post(
        "/cms/blogs/admin/posts",
        json={
            "title": "Hello CMS",
            "content": "<p>Body</p>",
            "status": "draft",
        },
    )
    assert response.status_code == 201
    assert response.json()["title"] == "Hello CMS"


@patch("app.api.v1.endpoints.blog.update_blog_status_service")
def test_publish_admin_blog(mock_status):
    mock_status.return_value = _detail(status="published")
    response = client.patch(
        f"/cms/blogs/admin/posts/{_BLOG_ID}/status",
        json={"status": "published"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "published"


@patch("app.api.v1.endpoints.blog.delete_blog_service")
def test_delete_admin_blog(mock_delete):
    mock_delete.return_value = None
    response = client.delete(f"/cms/blogs/admin/posts/{_BLOG_ID}")
    assert response.status_code == 200
    assert "archived" in response.json()["message"].lower()
