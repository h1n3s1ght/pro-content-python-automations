import base64
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")
os.environ.setdefault("ADMIN_PASSWORD", "admin-pass")
os.environ.setdefault("API_BEARER_TOKEN", "test-token")

import app.ui as ui_module
from app.db import get_db_session
from app.delivery_schemas import DeliveryVersionOption
from app.main import app


class _FakeSession:
    pass


class _FakeAsyncResult:
    def __init__(self, task_id: str):
        self.id = task_id


def _admin_headers() -> dict[str, str]:
    encoded = base64.b64encode(b"admin:admin-pass").decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


@pytest.fixture()
def admin_actions_client(monkeypatch):
    rows: dict[str, dict] = {}
    queued_calls: list[tuple] = []
    fake_session = _FakeSession()

    def _fake_fetch_delivery_row(_session, delivery_id, *, tier: str):
        row = rows.get(str(delivery_id))
        if row is None:
            raise HTTPException(status_code=404, detail="delivery not found")
        return dict(row)

    def _fake_delay(*args):
        queued_calls.append(args)
        return _FakeAsyncResult("fake-task-id")

    monkeypatch.setattr(ui_module, "_fetch_delivery_row", _fake_fetch_delivery_row)
    monkeypatch.setattr(ui_module.send_delivery, "delay", _fake_delay)
    monkeypatch.setattr(ui_module, "delivery_client_key", lambda *_args, **_kwargs: "acme.com")
    monkeypatch.setattr(
        ui_module,
        "list_version_options_for_client",
        lambda *_args, **_kwargs: [
            DeliveryVersionOption(job_id="job-v3", label="Version 3", is_latest=True),
            DeliveryVersionOption(job_id="job-v2", label="Version 2", is_latest=False),
        ],
    )

    def _override_db():
        yield fake_session

    app.dependency_overrides[get_db_session] = _override_db
    client = TestClient(app)
    try:
        yield client, rows, queued_calls
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_admin_versions_endpoint_returns_newest_default(admin_actions_client):
    client, rows, _queued_calls = admin_actions_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "job_id": "job-base", "client_name": "Acme", "status": "READY_TO_SEND"}

    resp = client.get(f"/ui/admin/deliveries/{delivery_id}/versions?tier=express", headers=_admin_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["default_job_id"] == "job-v3"
    assert [item["job_id"] for item in body["items"]] == ["job-v3", "job-v2"]


def test_admin_send_version_selected_job_queues_override(admin_actions_client, monkeypatch):
    client, rows, queued_calls = admin_actions_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "job_id": "job-base", "client_name": "Acme", "status": "READY_TO_SEND"}

    monkeypatch.setattr(
        ui_module,
        "resolve_requested_version_job_id",
        lambda *_args, **_kwargs: (True, True),
    )

    resp = client.post(
        f"/ui/admin/deliveries/{delivery_id}/send-version?tier=pro",
        headers=_admin_headers(),
        json={"version_job_id": "job-v2"},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert queued_calls[-1] == (str(delivery_id), "pro", False, "db:job-v2")


def test_admin_send_version_defaults_to_latest(admin_actions_client):
    client, rows, queued_calls = admin_actions_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "job_id": "job-base", "client_name": "Acme", "status": "READY_TO_SEND"}

    resp = client.post(
        f"/ui/admin/deliveries/{delivery_id}/send-version?tier=express",
        headers=_admin_headers(),
        json={},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert queued_calls[-1] == (str(delivery_id), "express", False, "db:job-v3")


def test_admin_send_version_rejects_cross_client_version(admin_actions_client, monkeypatch):
    client, rows, queued_calls = admin_actions_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "job_id": "job-base", "client_name": "Acme", "status": "READY_TO_SEND"}

    monkeypatch.setattr(
        ui_module,
        "resolve_requested_version_job_id",
        lambda *_args, **_kwargs: (True, False),
    )

    resp = client.post(
        f"/ui/admin/deliveries/{delivery_id}/send-version?tier=pro",
        headers=_admin_headers(),
        json={"version_job_id": "other-client-job"},
    )

    assert resp.status_code == 400
    assert queued_calls == []


def test_admin_rerun_queues_new_job(admin_actions_client, monkeypatch):
    client, rows, _queued_calls = admin_actions_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "job_id": "job-base", "client_name": "Acme", "status": "READY_TO_SEND"}

    monkeypatch.setattr(ui_module, "queue_rerun_from_job_id", lambda *_args, **_kwargs: "new-job-id")

    resp = client.post(
        f"/ui/admin/deliveries/{delivery_id}/rerun?tier=pro",
        headers=_admin_headers(),
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "new_job_id": "new-job-id", "task_queued": True}


def test_admin_rerun_add_changes_payload_forwarded(admin_actions_client, monkeypatch):
    client, rows, _queued_calls = admin_actions_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "job_id": "job-base", "client_name": "Acme", "status": "READY_TO_SEND"}

    captured = {}

    def _fake_queue(job_id, **kwargs):
        captured["job_id"] = job_id
        captured["kwargs"] = kwargs
        return "new-job-id"

    monkeypatch.setattr(ui_module, "queue_rerun_from_job_id", _fake_queue)

    resp = client.post(
        f"/ui/admin/deliveries/{delivery_id}/rerun?tier=pro",
        headers=_admin_headers(),
        json={
            "mode": "add_changes",
            "specific_instructions": "Update brand voice.",
            "new_pages": [
                {
                    "path": "/service/network-audits",
                    "title": "Network Audits",
                    "classification": "seo",
                    "seo_subtype": "service",
                }
            ],
        },
    )

    assert resp.status_code == 200
    assert captured["job_id"] == "job-base"
    assert captured["kwargs"]["source_delivery_id"] == str(delivery_id)
    assert captured["kwargs"]["rerun_request"] is not None
    assert captured["kwargs"]["rerun_request"].mode == "add_changes"
    assert len(captured["kwargs"]["rerun_request"].new_pages) == 1


def test_admin_rerun_missing_source_returns_404(admin_actions_client, monkeypatch):
    client, rows, _queued_calls = admin_actions_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "job_id": "job-base", "client_name": "Acme", "status": "READY_TO_SEND"}

    def _raise(*_args, **_kwargs):
        raise LookupError("missing source")

    monkeypatch.setattr(ui_module, "queue_rerun_from_job_id", _raise)

    resp = client.post(
        f"/ui/admin/deliveries/{delivery_id}/rerun?tier=pro",
        headers=_admin_headers(),
    )

    assert resp.status_code == 404
