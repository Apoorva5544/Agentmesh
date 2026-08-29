"""Small behavioral tests for auth helpers and the request schema surface."""

import pytest
from fastapi import HTTPException

from app.auth import AuthService, extract_bearer, generate_virtual_key, hash_secret
from app.schemas import ChatCompletionRequest, ChatMessage, ResponseFormat


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class _FakeDB:
    def __init__(self, known):
        self.known = known

    async def get_key(self, prefix: str, key_hash: str):
        return self.known.get((prefix, key_hash))


def test_extract_bearer_cases() -> None:
    assert extract_bearer(_FakeRequest({"Authorization": "Bearer sk-abc-123"})) == "sk-abc-123"
    assert extract_bearer(_FakeRequest({"Authorization": "bearer sk-x-y"})) == "sk-x-y"
    assert extract_bearer(_FakeRequest({"Authorization": "Basic abc"})) is None
    assert extract_bearer(_FakeRequest({})) is None


def test_virtual_key_round_trip_verifies() -> None:
    plaintext, prefix, key_hash = generate_virtual_key("support-bot")
    fake_db = _FakeDB({(prefix, key_hash): {"name": "support-bot"}})
    settings = type("S", (), {"allow_no_auth": False, "admin_api_key": None})()
    auth = AuthService(fake_db, settings)  # type: ignore[arg-type]
    assert extract_bearer(_FakeRequest({"Authorization": f"Bearer {plaintext}"})) == plaintext


async def test_verify_virtual_key_rejects_malformed() -> None:
    settings = type("S", (), {"allow_no_auth": False, "admin_api_key": None})()
    auth = AuthService(_FakeDB({}), settings)  # type: ignore[arg-type]
    result = await auth.verify_virtual_key("not-a-key")
    assert result is None


async def test_invalid_key_is_401() -> None:
    settings = type("S", (), {"allow_no_auth": False, "admin_api_key": None})()
    auth = AuthService(_FakeDB({}), settings)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        await auth.require_tenant(_FakeRequest({"Authorization": "Bearer sk-dead-beef"}))
    assert exc.value.status_code == 401


async def test_admin_key_not_tenant_virtual_key() -> None:
    settings = type("S", (), {"allow_no_auth": False, "admin_api_key": "sk-admin-secret"})()
    auth = AuthService(_FakeDB({}), settings)  # type: ignore[arg-type]
    identity = await auth.require_tenant(_FakeRequest({"Authorization": "Bearer sk-admin-secret"}))
    assert identity == "admin"


def test_chat_request_stream_defaults() -> None:
    request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
    assert request.stream is False
    assert request.response_format is None
    assert request.use_cache is True


def test_response_format_json_schema_alias() -> None:
    request = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="x")],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "chem", "schema": {"type": "object"}},
        },
    )
    assert request.response_format is not None
    assert request.response_format.type == "json_schema"
    assert request.response_format.json_schema is not None
    assert request.response_format.json_schema.value == {"type": "object"}


def test_hash_is_deterministic_sha256() -> None:
    assert hash_secret("secret-1") == hash_secret("secret-1")
    assert hash_secret("secret-1") != hash_secret("secret-2")