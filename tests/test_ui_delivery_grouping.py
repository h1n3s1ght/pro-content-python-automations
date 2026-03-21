import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")
os.environ.setdefault("API_BEARER_TOKEN", "test-token")

from app.ui import _dedupe_deliveries_by_client


def test_dedupe_deliveries_by_client_collapses_same_client_same_tier():
    items = [
        {
            "id": "d-new",
            "job_id": "job-new",
            "client_name": "Banks Consulting Northwest",
            "website_tier": "Pro",
            "created_at": "2026-03-20T23:45:54Z",
        },
        {
            "id": "d-old",
            "job_id": "job-old",
            "client_name": "Banks Consulting Northwest",
            "website_tier": "Pro",
            "created_at": "2026-03-20T23:41:05Z",
        },
    ]

    out = _dedupe_deliveries_by_client(
        items,
        job_client_key_by_job_id={
            "job-new": "banksconsultingnorthwest",
            "job-old": "banksconsultingnorthwest",
        },
    )

    assert len(out) == 1
    assert out[0]["id"] == "d-new"


def test_dedupe_deliveries_by_client_keeps_different_tiers_separate():
    items = [
        {
            "id": "d-pro",
            "job_id": "job-pro",
            "client_name": "Acme",
            "website_tier": "Pro",
        },
        {
            "id": "d-express",
            "job_id": "job-express",
            "client_name": "Acme",
            "website_tier": "Express",
        },
    ]

    out = _dedupe_deliveries_by_client(
        items,
        job_client_key_by_job_id={
            "job-pro": "acme.com",
            "job-express": "acme.com",
        },
    )

    assert len(out) == 2
    assert {row["id"] for row in out} == {"d-pro", "d-express"}
