from datetime import datetime, timezone

import app.s3_upload as s3_upload


def test_safe_client_form_name_handles_punctuation():
    out = s3_upload._safe_client_form_name("Elevate I.T., Inc.")
    assert out == "Elevate_IT_Inc"


def test_find_latest_client_form_payload_prefers_most_recent_last_modified(monkeypatch):
    items = [
        {
            "Key": "clientForm/Banks_Consulting_Northwest_2026-02-20T06-19-06-992Z.json",
            "LastModified": datetime(2026, 2, 20, 6, 19, 7, tzinfo=timezone.utc),
        },
        {
            "Key": "clientForm/Banks_Consulting_Northwest_2026-03-01T01-00-00-000Z.json",
            "LastModified": datetime(2026, 3, 1, 1, 0, 1, tzinfo=timezone.utc),
        },
    ]

    def _fake_list_keys_for_prefix(*, bucket: str, prefix: str, limit: int):
        if prefix == "clientForm/Banks_Consulting_Northwest_":
            return items
        return []

    monkeypatch.setattr(s3_upload, "_list_keys_for_prefix", _fake_list_keys_for_prefix)
    monkeypatch.setattr(s3_upload, "download_json", lambda key, **_kwargs: {"key": key})

    match = s3_upload.find_latest_client_form_payload(client_name="Banks Consulting Northwest")

    assert match is not None
    key, payload = match
    assert key == "clientForm/Banks_Consulting_Northwest_2026-03-01T01-00-00-000Z.json"
    assert payload["key"] == key


def test_find_latest_client_form_payload_uses_broad_fallback_and_signature_match(monkeypatch):
    broad_items = [
        {
            "Key": "clientForm/Other_Client_2026-03-01T01-00-00-000Z.json",
            "LastModified": datetime(2026, 3, 1, 1, 0, 1, tzinfo=timezone.utc),
        },
        {
            "Key": "clientForm/Banks_Consulting_Northwest_2026-02-20T06-19-06-992Z.json",
            "LastModified": datetime(2026, 2, 20, 6, 19, 7, tzinfo=timezone.utc),
        },
    ]

    def _fake_list_keys_for_prefix(*, bucket: str, prefix: str, limit: int):
        if prefix == "clientForm/Banks_Consulting_Northwest_":
            return []
        if prefix == "clientForm/":
            return broad_items
        return []

    monkeypatch.setattr(s3_upload, "_list_keys_for_prefix", _fake_list_keys_for_prefix)
    monkeypatch.setattr(s3_upload, "download_json", lambda key, **_kwargs: {"metadata": {"key": key}})

    match = s3_upload.find_latest_client_form_payload(client_name="Banks Consulting Northwest")

    assert match is not None
    key, payload = match
    assert key == "clientForm/Banks_Consulting_Northwest_2026-02-20T06-19-06-992Z.json"
    assert payload["metadata"]["key"] == key


def test_find_latest_client_form_payload_with_diagnostics_includes_list_errors(monkeypatch):
    def _fake_list_keys_for_prefix(*, bucket: str, prefix: str, limit: int):
        return [], "InvalidAccessKeyId"

    monkeypatch.setattr(s3_upload, "_list_keys_for_prefix", _fake_list_keys_for_prefix)

    match, diagnostics = s3_upload.find_latest_client_form_payload_with_diagnostics(
        client_name="Banks Consulting Northwest"
    )

    assert match is None
    assert "InvalidAccessKeyId" in diagnostics
