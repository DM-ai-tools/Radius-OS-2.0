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


async def _signup_hod(api_client, n: int):
    return await api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": f"hod-{n}@example.com",
            "password": "somepassword123",
            "full_name": f"HoD {n}",
            "role_name": "head_of_department",
        },
    )


async def test_head_of_department_signup_is_capped(api_client):
    """Self-registration as HoD is limited, not unlimited (AUDIT-001).

    The cap was 1 when this test was written; commit 2bb5dba deliberately raised
    it to five. Read the limit from config so the test tracks the product
    instead of pinning a number that a later change silently invalidates.
    """
    from app.services.role_skills import HOD_SIGNUP_LIMIT

    for n in range(HOD_SIGNUP_LIMIT):
        resp = await _signup_hod(api_client, n)
        assert resp.status_code == 200, f"HoD {n + 1} of {HOD_SIGNUP_LIMIT}: {resp.text}"

    over_limit = await _signup_hod(api_client, HOD_SIGNUP_LIMIT)
    assert over_limit.status_code == 400
    detail = over_limit.json()["detail"].lower()
    assert "head of department" in detail
    assert str(HOD_SIGNUP_LIMIT) in detail


async def test_roles_excludes_head_of_department_once_the_cap_is_reached(api_client):
    """The role stays offerable until the cap is full, then disappears."""
    from app.services.role_skills import HOD_SIGNUP_LIMIT

    for n in range(HOD_SIGNUP_LIMIT - 1):
        assert (await _signup_hod(api_client, n)).status_code == 200

    # One seat left — still on the list.
    names = {r["name"] for r in (await api_client.get("/api/v1/auth/roles")).json()}
    assert "head_of_department" in names

    assert (await _signup_hod(api_client, HOD_SIGNUP_LIMIT - 1)).status_code == 200

    resp = await api_client.get("/api/v1/auth/roles")
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert "head_of_department" not in names
    assert "client_success_manager" in names
