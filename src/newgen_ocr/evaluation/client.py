"""In-process API clients for tests and evaluation scripts (never used by the service)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from newgen_ocr.auth.tokens import Role


def authorized(client: TestClient, user: str = "evaluation", role: Role = Role.ADMIN) -> TestClient:
    """Create a token in the app's own token store and send it with every request."""
    token = client.app.state.tokens.create(user, role)  # type: ignore[attr-defined]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
