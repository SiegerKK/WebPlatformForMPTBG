import pytest


@pytest.fixture
def admin_user(test_client):
    """Register an admin user directly then set superuser flag via DB."""
    response = test_client.post("/api/auth/register", json={
        "username": "adminuser",
        "email": "admin@example.com",
        "password": "adminpass123",
    })
    return response.json()


@pytest.fixture
def admin_headers(admin_user, test_client, db_session):
    """Log in as admin and return auth headers with superuser flag set."""
    from app.core.auth.models import User
    # Grant superuser via the DB directly (simulates seed / manual grant)
    user = db_session.query(User).filter(User.username == "adminuser").first()
    user.is_superuser = True
    db_session.commit()

    response = test_client.post("/api/auth/login", data={
        "username": "adminuser",
        "password": "adminpass123",
    })
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def regular_user(test_client):
    response = test_client.post("/api/auth/register", json={
        "username": "regularuser",
        "email": "regular@example.com",
        "password": "regularpass123",
    })
    return response.json()


@pytest.fixture
def regular_headers(regular_user, test_client):
    response = test_client.post("/api/auth/login", data={
        "username": "regularuser",
        "password": "regularpass123",
    })
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── List users ────────────────────────────────────────────────────────────────

def test_admin_list_users(test_client, admin_headers, regular_user):
    response = test_client.get("/api/admin/users", headers=admin_headers)
    assert response.status_code == 200
    users = response.json()
    usernames = [u["username"] for u in users]
    assert "adminuser" in usernames
    assert "regularuser" in usernames


def test_admin_list_users_forbidden_for_regular(test_client, regular_headers):
    response = test_client.get("/api/admin/users", headers=regular_headers)
    assert response.status_code == 403


def test_admin_list_users_unauthenticated(test_client):
    response = test_client.get("/api/admin/users")
    assert response.status_code == 401


# ── User profile ──────────────────────────────────────────────────────────────

def test_admin_get_user_profile(test_client, admin_headers, regular_user):
    user_id = regular_user["id"]
    response = test_client.get(f"/api/admin/users/{user_id}", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "regularuser"
    assert "matches_created" in data
    assert "matches_played" in data


def test_public_profile_accessible_by_regular_user(test_client, regular_headers, admin_user):
    user_id = admin_user["id"]
    response = test_client.get(f"/api/admin/profile/{user_id}", headers=regular_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "adminuser"


# ── Update user ───────────────────────────────────────────────────────────────

def test_admin_deactivate_user(test_client, admin_headers, regular_user):
    user_id = regular_user["id"]
    response = test_client.patch(
        f"/api/admin/users/{user_id}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_admin_grant_superuser(test_client, admin_headers, regular_user):
    user_id = regular_user["id"]
    response = test_client.patch(
        f"/api/admin/users/{user_id}",
        json={"is_superuser": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_superuser"] is True


def test_admin_cannot_remove_own_superuser(test_client, admin_headers, admin_user):
    user_id = admin_user["id"]
    response = test_client.patch(
        f"/api/admin/users/{user_id}",
        json={"is_superuser": False},
        headers=admin_headers,
    )
    assert response.status_code == 400


# ── Delete user ───────────────────────────────────────────────────────────────

def test_admin_delete_user(test_client, admin_headers, regular_user):
    user_id = regular_user["id"]
    response = test_client.delete(f"/api/admin/users/{user_id}", headers=admin_headers)
    assert response.status_code == 204
    # Verify the user is gone
    response = test_client.get(f"/api/admin/users/{user_id}", headers=admin_headers)
    assert response.status_code == 404


def test_admin_cannot_delete_self(test_client, admin_headers, admin_user):
    user_id = admin_user["id"]
    response = test_client.delete(f"/api/admin/users/{user_id}", headers=admin_headers)
    assert response.status_code == 400


def test_regular_user_cannot_delete(test_client, regular_headers, admin_user):
    user_id = admin_user["id"]
    response = test_client.delete(f"/api/admin/users/{user_id}", headers=regular_headers)
    assert response.status_code == 403
