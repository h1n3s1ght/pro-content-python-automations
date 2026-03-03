import os
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")

import app.ui as ui_module
from app.db import get_db_session
from app.main import app


class _ExecResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rows: dict):
        self.rows = rows
        self.delete_calls: list[tuple[str, str]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        args = params or {}
        if "DELETE FROM delivery_outbox" in sql:
            table = "pro"
        elif "DELETE FROM express_delivery_outbox" in sql:
            table = "express"
        else:
            raise AssertionError(f"Unexpected SQL in test session: {sql}")

        delivery_id = str(args["delivery_id"])
        self.delete_calls.append((table, delivery_id))
        if delivery_id in self.rows[table]:
            del self.rows[table][delivery_id]
            return _ExecResult(1)
        return _ExecResult(0)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture()
def remove_client(monkeypatch):
    pro_id = uuid4()
    express_id = uuid4()
    rows = {
        "pro": {
            str(pro_id): {"id": pro_id, "client_name": "Acme Pro"},
        },
        "express": {
            str(express_id): {"id": express_id, "client_name": "Acme Express"},
        },
    }
    fake_session = _FakeSession(rows)

    def _fake_fetch_delivery_row(_session, delivery_id, *, tier: str):
        table = "express" if tier == "express" else "pro"
        row = rows[table].get(str(delivery_id))
        if row is None:
            raise HTTPException(status_code=404, detail="delivery not found")
        return dict(row)

    monkeypatch.setattr(ui_module, "_fetch_delivery_row", _fake_fetch_delivery_row)

    def _override_db():
        yield fake_session

    app.dependency_overrides[get_db_session] = _override_db
    client = TestClient(app)
    try:
        yield client, rows, fake_session, pro_id, express_id
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def _qs(resp):
    return parse_qs(urlparse(resp.headers["location"]).query)


def test_remove_delivery_pro_wrong_confirm_name_does_not_delete(remove_client):
    client, rows, fake_session, pro_id, _express_id = remove_client

    resp = client.post(
        "/ui/deliveries/remove",
        data={
            "delivery_id": str(pro_id),
            "tier": "pro",
            "confirm_name": "Wrong Name",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert str(pro_id) in rows["pro"]
    assert fake_session.delete_calls == []
    assert "flash_error" in _qs(resp)


def test_remove_delivery_pro_exact_confirm_name_deletes(remove_client):
    client, rows, fake_session, pro_id, _express_id = remove_client

    resp = client.post(
        "/ui/deliveries/remove",
        data={
            "delivery_id": str(pro_id),
            "tier": "pro",
            "confirm_name": "Acme Pro",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert str(pro_id) not in rows["pro"]
    assert ("pro", str(pro_id)) in fake_session.delete_calls
    assert "flash_success" in _qs(resp)


def test_remove_delivery_express_wrong_confirm_name_does_not_delete(remove_client):
    client, rows, fake_session, _pro_id, express_id = remove_client

    resp = client.post(
        "/ui/deliveries/remove",
        data={
            "delivery_id": str(express_id),
            "tier": "express",
            "confirm_name": "wrong",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert str(express_id) in rows["express"]
    assert fake_session.delete_calls == []
    assert "flash_error" in _qs(resp)


def test_remove_delivery_express_exact_confirm_name_deletes(remove_client):
    client, rows, fake_session, _pro_id, express_id = remove_client

    resp = client.post(
        "/ui/deliveries/remove",
        data={
            "delivery_id": str(express_id),
            "tier": "express",
            "confirm_name": "Acme Express",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert str(express_id) not in rows["express"]
    assert ("express", str(express_id)) in fake_session.delete_calls
    assert "flash_success" in _qs(resp)
