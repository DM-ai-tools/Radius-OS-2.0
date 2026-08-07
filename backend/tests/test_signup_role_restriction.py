async def test_signup_allows_head_of_department_when_empty(api_client):
    resp = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "first-hod@example.com",
            "password": "somepassword123",
            "full_name": "First Admin",
            "role_name": "head_of_department",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_signup_rejects_head_of_department_after_bootstrap(api_client):
    first = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "first-hod@example.com",
            "password": "somepassword123",
            "full_name": "First Admin",
            "role_name": "head_of_department",
        },
    )
    assert first.status_code == 200

    resp = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "attacker@example.com",
            "password": "somepassword123",
            "full_name": "Attacker",
            "role_name": "head_of_department",
        },
    )
    assert resp.status_code == 400


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


async def test_roles_include_hod_only_when_empty(api_client):
    empty = await api_client.get("/api/v1/auth/roles")
    assert empty.status_code == 200
    names = {r["name"] for r in empty.json()}
    assert "head_of_department" in names

    boot = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "boot-hod@example.com",
            "password": "somepassword123",
            "full_name": "Boot Admin",
            "role_name": "head_of_department",
        },
    )
    assert boot.status_code == 200

    locked = await api_client.get("/api/v1/auth/roles")
    assert locked.status_code == 200
    names2 = {r["name"] for r in locked.json()}
    assert "head_of_department" not in names2
