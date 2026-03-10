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
    assert "Remove Delivery" in resp.text
    assert "Type the client name to confirm" in resp.text
    assert "Cancel Action" in resp.text
    assert "class=\"admin-action\"" in resp.text
    assert "adminActions" in resp.text
    assert "admin-actions-enabled" in resp.text
    assert "All tiers" in resp.text
    assert "daysBackFilter" in resp.text
    assert "datetime-local" in resp.text
    assert "sort-trigger" in resp.text
    assert "data-sort-key=\"created_at\"" in resp.text
    assert "PENDING SEND" in resp.text
    assert "value=\"COMPLETED_PENDING_SEND\"" in resp.text
    assert "statusFilterButton" in resp.text
    assert "statusSelectAllToggle" in resp.text
    assert "Select All" in resp.text
    assert "Delivery submitted" in resp.text
    assert "Re-send Delivery" in resp.text
    assert "id=\"resendDeliveryModal\"" in resp.text
    assert "resendConfirmName" in resp.text
    assert "openResendModal" in resp.text
    assert "Re-send" in resp.text
