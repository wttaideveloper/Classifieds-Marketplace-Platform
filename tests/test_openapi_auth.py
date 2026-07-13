from app.main import app


def test_openapi_bearer_auth_scheme():
    schema = app.openapi()

    schemes = schema["components"]["securitySchemes"]
    assert "BearerAuth" in schemes
    assert schemes["BearerAuth"]["type"] == "http"
    assert schemes["BearerAuth"]["scheme"] == "bearer"
    assert schemes["BearerAuth"]["bearerFormat"] == "JWT"

    assert schema["security"] == [{"BearerAuth": []}]


def test_openapi_public_auth_routes_have_no_security():
    schema = app.openapi()
    paths = schema["paths"]

    assert paths["/api/v1/auth/dev-token"]["get"]["security"] == []
    assert paths["/api/v1/auth/dev-token"]["post"]["security"] == []
    assert paths["/api/v1/auth/test-users"]["get"]["security"] == []


def test_openapi_protected_routes_require_bearer():
    schema = app.openapi()
    conv = schema["paths"]["/api/v1/conversations/provider"]["get"]
    assert conv["security"] == [{"BearerAuth": []}]


def test_openapi_tags_have_descriptions():
    schema = app.openapi()
    tags = schema.get("tags", [])
    assert len(tags) >= 10
    names = {t["name"] for t in tags}
    assert "Authentication" in names
    assert "Chat Notifications" in names
    assert all(t.get("description") for t in tags)


def test_swagger_docs_routes_registered():
    from starlette.routing import Route

    paths = set()
    for route in app.routes:
        if isinstance(route, Route):
            paths.add(route.path)
    assert "/docs" in paths
    assert "/api/docs" in paths
    assert "/openapi.json" in paths
    assert "/api/openapi.json" in paths
