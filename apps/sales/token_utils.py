"""Token generation and hashing for client proposal response links."""

import hashlib
import secrets


def generate_raw_client_proposal_token():
    """Return a URL-safe opaque token (store only the hash in the database)."""
    return secrets.token_urlsafe(32)


def hash_client_proposal_token(raw_token: str) -> str:
    """SHA-256 hex digest of the raw token."""
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
