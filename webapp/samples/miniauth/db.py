"""Tiny in-memory user store."""

_USERS = {
    "mehwish": {"password_hash": "hashed_secret", "role": "admin"},
    "guest": {"password_hash": "hashed_guest", "role": "viewer"},
}


def get_user(username):
    """Look up a user record by username. Returns None if missing."""
    return _USERS.get(username)


def save_user(username, password_hash, role="viewer"):
    """Create or update a user record in the store."""
    _USERS[username] = {"password_hash": password_hash, "role": role}
    return _USERS[username]


def hash_password(raw_password):
    """Pretend to hash a password (demo only, not real crypto)."""
    return "hashed_" + raw_password
