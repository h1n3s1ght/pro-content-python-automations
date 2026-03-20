from app.predeploy_checks import run_client_form_probe


def _getenv_factory(values: dict[str, str]):
    def _getenv(name: str, default: str | None = None):
        return values.get(name, default)

    return _getenv


def test_run_client_form_probe_skips_when_disabled():
    called = {"count": 0}

    def _finder(**_kwargs):
        called["count"] += 1
        return None

    ok, message = run_client_form_probe(
        getenv=_getenv_factory({"PREDEPLOY_S3_PROBE_ENABLED": "0"}),
        finder=_finder,
    )

    assert ok is True
    assert "predeploy_s3_probe_skipped" in message
    assert called["count"] == 0


def test_run_client_form_probe_succeeds_with_default_expected_key():
    captured = {}

    def _finder(**kwargs):
        captured.update(kwargs)
        return (
            "clientForm/ISAAC_TESTING_2026-03-19T20-46-06-406Z.json",
            {"metadata": {"business_name": "ISAAC TESTING"}},
        )

    ok, message = run_client_form_probe(
        getenv=_getenv_factory({}),
        finder=_finder,
    )

    assert ok is True
    assert "predeploy_s3_probe_ok" in message
    assert captured["client_name"] == "ISAAC TESTING"
    assert captured["bucket"] == "pro-tier-bucket"
    assert captured["prefix"] == "clientForm/"


def test_run_client_form_probe_fails_on_unexpected_key():
    def _finder(**_kwargs):
        return ("clientForm/ISAAC_TESTING_2026-03-20T00-00-00-000Z.json", {"ok": True})

    ok, message = run_client_form_probe(
        getenv=_getenv_factory(
            {
                "PREDEPLOY_S3_PROBE_EXPECTED_KEY": "clientForm/ISAAC_TESTING_2026-03-19T20-46-06-406Z.json"
            }
        ),
        finder=_finder,
    )

    assert ok is False
    assert "reason=unexpected_key" in message


def test_run_client_form_probe_accepts_expected_key_as_https_url():
    def _finder(**_kwargs):
        return ("clientForm/ISAAC_TESTING_2026-03-19T20-46-06-406Z.json", {"ok": True})

    ok, message = run_client_form_probe(
        getenv=_getenv_factory(
            {
                "PREDEPLOY_S3_PROBE_EXPECTED_KEY": "https://pro-tier-bucket.s3.us-east-2.amazonaws.com/clientForm/ISAAC_TESTING_2026-03-19T20-46-06-406Z.json"
            }
        ),
        finder=_finder,
    )

    assert ok is True
    assert "predeploy_s3_probe_ok" in message


def test_run_client_form_probe_fails_on_no_match():
    def _finder(**_kwargs):
        return None

    ok, message = run_client_form_probe(
        getenv=_getenv_factory({"PREDEPLOY_S3_PROBE_CLIENT_NAME": "ISAAC TESTING"}),
        finder=_finder,
    )

    assert ok is False
    assert "reason=no_match" in message
