import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ["API_BEARER_TOKEN"] = "test-token"
os.environ["OPENAI_API_KEY"] = "test-key"
os.environ["PRO_COPY_ASSISTANT_ID"] = "test-assistant"
os.environ["PRO_SITEMAP_ASSISTANT_ID"] = "test-sitemap"

from app.main import app


@pytest.fixture()
def client(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.main.register_job", _noop)
    monkeypatch.setattr("app.main.set_status", _noop)
    monkeypatch.setattr("app.main.set_payload", _noop)
    monkeypatch.setattr(
        "app.main.run_full_job",
        SimpleNamespace(delay=lambda *args, **kwargs: None),
    )
    return TestClient(app)


def _headers():
    return {"Authorization": "Bearer test-token"}


def test_webhook_snake_case_payload_ok(client):
    payload = {
        "metadata": {"business_domain": "example.com", "business_name": "Acme"},
        "user_data": {"service_offerings": ["A"]},
        "query_string": {"utm_source": "x"},
    }
    resp = client.post("/webhook/pro-form", headers=_headers(), json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert "job_id" in body


def test_webhook_camel_case_payload_ok(client):
    payload = {
        "metadata": {"businessDomain": "example.com", "businessName": "Acme"},
        "userData": {"serviceOfferings": ["A"], "serviceGuarantee": True},
        "queryString": {"utmSource": "x"},
    }
    resp = client.post("/webhook/pro-form", headers=_headers(), json=payload)
    assert resp.status_code == 200


def test_webhook_mixed_case_payload_ok(client):
    payload = {
        "metadata": {"businessDomain": "example.com"},
        "querystring": {"foo": "bar"},
        "userdata": {"service_offerings": ["A"]},
    }
    resp = client.post("/webhook/pro-form", headers=_headers(), json=payload)
    assert resp.status_code == 200


def test_webhook_allows_unknown_fields(client):
    payload = {
        "metadata": {"business_domain": "example.com", "unknown_meta": "x"},
        "user_data": {"service_offerings": ["A"], "unknown_nested": {"a": 1}},
        "extra_top": "value",
    }
    resp = client.post("/webhook/pro-form", headers=_headers(), json=payload)
    assert resp.status_code == 200


def test_webhook_invalid_type_rejected(client):
    payload = {
        "user_data": {"service_offerings": {"not": "a-list"}},
    }
    resp = client.post("/webhook/pro-form", headers=_headers(), json=payload)
    assert resp.status_code == 422
