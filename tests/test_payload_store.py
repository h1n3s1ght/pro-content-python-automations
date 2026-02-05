import json


def test_payload_store_save_load_and_purge(tmp_path, monkeypatch):
    # Store payloads in a temp directory (avoids writing to /var/data on dev machines).
    monkeypatch.setenv("PAYLOAD_DISK_DIR", str(tmp_path))

    from app.payload_store import load_payload_json, purge_payload_file, save_payload_json

    job_id = "job-123"
    payload = {"data": {"content": {"home": {"title": "Hello"}}}}

    path = save_payload_json(job_id, payload)
    assert path.endswith(f"{job_id}.json")

    # Sanity check the file exists and is valid JSON.
    with open(path, "r", encoding="utf-8") as f:
        assert json.load(f) == payload

    assert load_payload_json(path) == payload
    assert load_payload_json(f"file:{path}") == payload

    assert purge_payload_file(job_id) is True
    assert load_payload_json(path) is None


def test_retention_seconds_default_and_override(monkeypatch):
    from app.payload_store import retention_seconds

    monkeypatch.delenv("PAYLOAD_RETENTION_DAYS", raising=False)
    assert retention_seconds() == 7 * 24 * 60 * 60

    monkeypatch.setenv("PAYLOAD_RETENTION_DAYS", "0")
    assert retention_seconds() == 0

    monkeypatch.setenv("PAYLOAD_RETENTION_DAYS", "2")
    assert retention_seconds() == 2 * 24 * 60 * 60

