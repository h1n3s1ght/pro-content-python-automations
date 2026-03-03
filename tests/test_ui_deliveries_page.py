import os

from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")

from app.main import app


def test_ui_deliveries_page_contains_website_tier_column():
    client = TestClient(app)

    resp = client.get("/ui/deliveries")

    assert resp.status_code == 200
    assert "Website Tier" in resp.text
