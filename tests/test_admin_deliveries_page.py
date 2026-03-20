import base64
import os

from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")
os.environ.setdefault("ADMIN_PASSWORD", "admin-pass")
os.environ.setdefault("API_BEARER_TOKEN", "test-token")

import app.admin as admin_module
from app.db import get_db_session
from app.main import app


def _admin_headers() -> dict[str, str]:
    encoded = base64.b64encode(b"admin:admin-pass").decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def test_admin_deliveries_page_contains_rerun_modals(monkeypatch):
    fake_session = object()

    def _override_db():
        yield fake_session

    monkeypatch.setattr(admin_module, "_list_deliveries", lambda *_args, **_kwargs: ([], 0))
    app.dependency_overrides[get_db_session] = _override_db
    client = TestClient(app)
    try:
        resp = client.get("/admin/deliveries", headers=_admin_headers())
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert resp.status_code == 200
    assert "id=\"adminRerunModeModal\"" in resp.text
    assert "id=\"adminRerunChangesModal\"" in resp.text
    assert "id=\"adminRerunSourceModal\"" in resp.text
    assert "Without Changes" in resp.text
    assert "Add Changes" in resp.text
    assert "Specific Instructions (Optional)" in resp.text
    assert "Add New Page" in resp.text
    assert "Paste Source Form JSON" in resp.text
    assert "Queue Re-run With JSON" in resp.text
    assert "openAdminRerunModal" in resp.text
