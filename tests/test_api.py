import pytest

from .conftest import client


def test_login(client):
    response = client.post(
        "/users/token", data={"username": "test@gmail.com", "password": "testtest"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_failure(client):
    response = client.post(
        "/users/token", data={"username": "test@gmail.com", "password": "testtest1"}
    )
    assert response.status_code == 401
    assert "User unauthorized" in response.json()["detail"]


def get_access_token(client):
    response = client.post(
        "/users/token",
        data={"username": "test@gmail.com", "password": "testtest"},
        timeout=60.0,
    )
    return response.json()["access_token"]


def test_me(client):
    token = get_access_token(client=client)
    response = client.get(
        "/users/me",
        headers={"accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert response.status_code == 200
    assert "email" in response.json()["data"]


def test_get_my_providers(client):
    token = get_access_token(client=client)
    response = client.get(
        "/providers/get_my_providers",
        headers={"accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    assert response.status_code == 200
    assert "my_providers" in response.json()["data"]


def test_get_providers(client):
    token = get_access_token(client=client)
    response = client.get(
        "/providers/get_providers",
        headers={"accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    assert response.status_code == 200
    assert len(response.json()["data"]) > 0


def test_update_provider_info(client):
    token = get_access_token(client=client)
    response = client.post(
        "/providers/update_provider_info?provider_name=gmailprovider&identifier_name=test_identifier_test",
        headers={"accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    assert response.status_code == 200
