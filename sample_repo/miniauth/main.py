"""HTTP-ish entrypoint that routes incoming requests to handlers."""

from auth import login
from db import save_user, hash_password


def handle_login_request(payload):
    """Handle a login request coming from the API layer."""
    username = payload.get("username")
    password = payload.get("password")
    result = login(username, password)
    if result["ok"]:
        return {"status": 200, "body": result}
    return {"status": 401, "body": result}


def handle_register_request(payload):
    """Handle a new-user registration request."""
    username = payload.get("username")
    password_hash = hash_password(payload.get("password"))
    user = save_user(username, password_hash, payload.get("role", "viewer"))
    return {"status": 201, "body": {"username": username, "role": user["role"]}}


def route(path, payload):
    """Dispatch an incoming request to the correct handler."""
    if path == "/login":
        return handle_login_request(payload)
    if path == "/register":
        return handle_register_request(payload)
    return {"status": 404, "body": {"error": "not found"}}
