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
