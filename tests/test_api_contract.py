def test_health_api(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_documentation_loads(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert len(response.json()["paths"]) >= 100


def test_api_paths_and_params_use_snake_case(client):
    openapi = client.get("/openapi.json").json()

    for path, methods in openapi["paths"].items():
        assert not any(char.isupper() for char in path), path

        for operation in methods.values():
            for parameter in operation.get("parameters", []):
                assert not any(char.isupper() for char in parameter["name"]), (
                    path,
                    parameter["name"],
                )


def test_api_contract_has_response_security_and_rate_limit_docs(client):
    openapi = client.get("/openapi.json").json()

    for path, methods in openapi["paths"].items():
        for method, operation in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue

            success_response = operation.get("responses", {}).get("200") or operation.get(
                "responses", {}
            ).get("201")
            assert success_response and "content" in success_response, path
            assert "x-rate-limit" in operation, path

            is_protected_area = path.startswith((
                "/api/v1/admin",
                "/api/v1/merchant",
                "/api/v1/customer",
            ))
            is_public_auth_or_content = any(
                public_path in path
                for public_path in (
                    "/login",
                    "/register",
                    "/google",
                    "/blog-categories",
                    "/blogs",
                    "/categories",
                )
            )
            if is_protected_area and not is_public_auth_or_content:
                assert operation.get("security"), path
