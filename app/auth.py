import hashlib
import secrets

from fastapi import HTTPException, Request

from app.config import Settings
from app.db import Database

ADMIN_IDENT = "admin"
LOCAL_IDENT = "local"


def generate_virtual_key(name: str) -> tuple[str, str, str]:
    """Returns (plaintext_key, prefix, key_hash). Prefix is used for fast lookup."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_hex(20)
    plaintext = f"sk-{prefix}-{secret}"
    return plaintext, prefix, hash_secret(secret)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


class AuthService:
    """Virtual-key auth for tenant traffic + admin key for control-plane calls."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def require_tenant(self, request: Request) -> str:
        """Returns an identity string ('admin', a virtual key name, or 'local')."""
        if self.settings.allow_no_auth:
            return LOCAL_IDENT
        token = extract_bearer(request)
        if not token:
            raise HTTPException(status_code=401, detail="missing Authorization: Bearer <key>")
        if self.settings.admin_api_key and token == self.settings.admin_api_key:
            return ADMIN_IDENT
        identity = await self.verify_virtual_key(token)
        if identity is None:
            raise HTTPException(status_code=401, detail="invalid API key")
        return identity

    async def require_admin(self, request: Request) -> str:
        if self.settings.allow_no_auth:
            return LOCAL_IDENT
        token = extract_bearer(request)
        if token and self.settings.admin_api_key and token == self.settings.admin_api_key:
            return ADMIN_IDENT
        if token:
            identity = await self.verify_virtual_key(token)
            if identity is not None:
                raise HTTPException(status_code=403, detail="admin access required")
        raise HTTPException(status_code=401, detail="missing or invalid admin key")

    async def verify_virtual_key(self, token: str) -> str | None:
        parts = token.split("-")
        if len(parts) != 3 or parts[0] != "sk":
            return None
        prefix = parts[1]
        secret_hash = hash_secret(parts[2])
        row = await self.db.get_key(prefix, secret_hash)
        if row is None:
            return None
        return row["name"]

    async def issue_key(self, name: str) -> dict:
        plaintext, prefix, key_hash = generate_virtual_key(name)
        row = await self.db.create_key(name, prefix, key_hash)
        return {
            "id": row["id"],
            "name": row["name"],
            "key": plaintext,
            "key_prefix": row["key_prefix"],
            "created_at": row["created_at"].isoformat(),
        }