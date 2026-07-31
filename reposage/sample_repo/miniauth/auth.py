"""Authentication logic: verifying credentials and issuing tokens."""

import time

from db import get_user, hash_password


def verify_password(raw_password, stored_hash):
    """Check whether a raw password matches the stored hash."""
    return hash_password(raw_password) == stored_hash


def generate_token(username, role):
    """Issue a signed session token for an authenticated user."""
    issued_at = int(time.time())
    return f"token::{username}::{role}::{issued_at}"


def login(username, raw_password):
    """Authenticate a user end to end.

    Looks up the user, verifies the password, and on success issues a
    session token carrying the user's role.
    """
    user = get_user(username)
    if user is None:
        return {"ok": False, "error": "no such user"}

    if not verify_password(raw_password, user["password_hash"]):
        return {"ok": False, "error": "bad password"}

    token = generate_token(username, user["role"])
    return {"ok": True, "token": token, "role": user["role"]}
