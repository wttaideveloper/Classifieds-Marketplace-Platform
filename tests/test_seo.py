from unittest.mock import patch
from uuid import uuid4


def test_create_seo_metadata_endpoint(client):
    entity_id = str(uuid4())
    mock_response = {
        "success": True,
        "message": "SEO metadata saved successfully",
        "data": {
            "seo_id": str(uuid4()),
            "entity_type": "Business",
            "entity_id": entity_id,
            "meta_title": "Best Local Business",
            "meta_description": "Find the best local business.",
            "meta_keywords": "local,business",
            "canonical_url": "https://example.com/businesses/best-local-business",
            "og_title": "Best Local Business",
            "og_description": "Find the best local business.",
            "og_image": "https://example.com/share.jpg",
            "slug": "best-local-business",
            "sitemap_url": "http://testserver/businesses/best-local-business",
            "created_at": None,
            "updated_at": None,
        },
    }

    with patch(
        "app.api.v1.endpoints.seo.create_seo_meta_service",
        return_value=mock_response,
    ):
        response = client.post(
            "/api/v1/seo/meta",
            json={
                "entity_type": "Business",
                "entity_id": entity_id,
                "meta_title": "Best Local Business",
                "meta_description": "Find the best local business.",
                "meta_keywords": "local,business",
                "canonical_url": "https://example.com/businesses/best-local-business",
                "og_title": "Best Local Business",
                "og_description": "Find the best local business.",
                "og_image": "https://example.com/share.jpg",
                "slug": "best-local-business",
            },
        )

    assert response.status_code == 201
    assert response.json()["data"]["sitemap_url"].endswith("/businesses/best-local-business")


def test_sitemap_xml_endpoint(client):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
    )

    with patch("app.api.v1.endpoints.seo.generate_sitemap_xml_service", return_value=xml):
        response = client.get("/api/v1/seo/sitemap.xml")

    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert "<urlset" in response.text


def test_robots_txt_endpoint(client):
    response = client.get("/api/v1/seo/robots.txt")

    assert response.status_code == 200
    assert "Sitemap:" in response.text
