async def test_signup_rejects_head_of_department(api_client):
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
