import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")
os.environ.setdefault("API_BEARER_TOKEN", "test-token")

import app.ui as ui_module
from app.db import get_db_session
from app.main import app


class _FakeSession:
    pass


class _FakeAsyncResult:
    def __init__(self, task_id: str):
        self.id = task_id


@pytest.fixture()
def send_now_client(monkeypatch):
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

    def _override_db():
        yield fake_session

    app.dependency_overrides[get_db_session] = _override_db
    client = TestClient(app)
    try:
        yield client, rows, queued_calls
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_send_now_ready_to_send_express_queues_normal_send(send_now_client):
    client, rows, queued_calls = send_now_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "status": "READY_TO_SEND"}

    resp = client.post(f"/ui/deliveries/{delivery_id}/send-now?tier=express")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["task_id"] == "fake-task-id"
    assert queued_calls == [(str(delivery_id), "express", False)]


def test_send_now_sent_with_replay_queues_replay(send_now_client):
    client, rows, queued_calls = send_now_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "status": "SENT"}

    resp = client.post(f"/ui/deliveries/{delivery_id}/send-now?tier=pro&replay=1")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["task_id"] == "fake-task-id"
    assert queued_calls == [(str(delivery_id), "pro", True)]


def test_send_now_sent_without_replay_returns_400(send_now_client):
    client, rows, queued_calls = send_now_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "status": "SENT"}

    resp = client.post(f"/ui/deliveries/{delivery_id}/send-now?tier=pro")

    assert resp.status_code == 400
    assert queued_calls == []


@pytest.mark.parametrize("status", ["WAITING_FOR_SITE", "SENDING"])
def test_send_now_non_sendable_status_returns_400(send_now_client, status):
    client, rows, queued_calls = send_now_client
    delivery_id = uuid4()
    rows[str(delivery_id)] = {"id": delivery_id, "status": status}

    resp = client.post(f"/ui/deliveries/{delivery_id}/send-now?tier=pro")

    assert resp.status_code == 400
    assert queued_calls == []
