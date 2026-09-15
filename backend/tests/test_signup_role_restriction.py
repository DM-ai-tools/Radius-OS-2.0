async def test_signup_accepts_head_of_department(api_client):
    resp = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "hod@example.com",
            "password": "somepassword123",
            "full_name": "Head Person",
            "role_name": "head_of_department",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_signup_accepts_self_service_role(api_client):
    resp = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "newbie@example.com",
            "password": "somepassword123",
            "full_name": "New Person",
            "role_name": "content_seo_specialist",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_roles_include_head_of_department(api_client):
    resp = await api_client.get("/api/v1/auth/roles")
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert "head_of_department" in names
    assert "client_success_manager" in names


async def test_second_head_of_department_signup_is_rejected(api_client):
    """First HoD signup bootstraps the org; every subsequent one must be
    invited by an existing admin, not self-registered (AUDIT-001)."""
    first = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "hod-one@example.com",
            "password": "somepassword123",
            "full_name": "First HoD",
            "role_name": "head_of_department",
        },
    )
    assert first.status_code == 200

    second = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "hod-two@example.com",
            "password": "somepassword123",
            "full_name": "Second HoD",
            "role_name": "head_of_department",
        },
    )
    assert second.status_code == 400
    assert "admin" in second.json()["detail"].lower()


async def test_roles_excludes_head_of_department_once_one_exists(api_client):
    bootstrap = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "hod-bootstrap@example.com",
            "password": "somepassword123",
            "full_name": "Bootstrap HoD",
            "role_name": "head_of_department",
        },
    )
    assert bootstrap.status_code == 200

    resp = await api_client.get("/api/v1/auth/roles")
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert "head_of_department" not in names
    assert "client_success_manager" in names
