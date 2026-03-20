import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")

from app import workflow


def _base_sitemap():
    return {
        "version": "2025-12-01",
        "headers": list(workflow.SITEMAP_REQUIRED_HEADERS),
        "meta": {
            "locale": "en-US",
            "counts": {"total_rows": 1, "counted_pages": 1, "excluded_pages": 0},
            "validation_passed": True,
        },
        "rows": [
            {
                "path": "/",
                "page_type": "core",
                "page_title": "Home",
                "html_title": "Home",
                "meta_description": "",
                "index": True,
                "follow": True,
                "canonical": "/",
                "sort_order": 1,
                "locale": "en-US",
                "notes": "",
                "generative_content": True,
                "content_page_type": "home",
                "navigation_category": "primary",
                "navigation_label": "Home",
            }
        ],
    }


def test_merge_rerun_added_pages_replaces_path_and_updates_counts():
    sitemap = _base_sitemap()
    added = [
        {
            "path": "/",
            "title": "Homepage",
            "content_page_type": "home",
        },
        {
            "path": "/service/network-audits",
            "title": "Network Audits",
            "content_page_type": "seo-service",
        },
    ]

    out = workflow.merge_rerun_added_pages_into_sitemap(sitemap, added_pages=added)

    assert len(out["rows"]) == 2
    assert out["meta"]["counts"]["total_rows"] == 2
    replaced_home = next(r for r in out["rows"] if r["path"] == "/")
    assert replaced_home["page_title"] == "Homepage"
    added_service = next(r for r in out["rows"] if r["path"] == "/service/network-audits")
    assert added_service["content_page_type"] == "seo-service"


def test_run_workflow_applies_specific_instructions_to_every_page_payload(monkeypatch):
    captured_payloads = []
    captured_sitemap_user_data = {}

    async def _fake_generate_sitemap(metadata, user_data, log_lines=None):
        captured_sitemap_user_data.update(user_data)
        return _base_sitemap()

    async def _fake_generate_page_with_retries(payload, log_lines=None):
        captured_payloads.append(payload)
        return {"page_kind": "skip", "path": payload.get("this_page", {}).get("path", ""), "reason": "non-generative"}

    async def _fake_campaign_pages(**kwargs):
        return []

    monkeypatch.setattr(workflow, "generate_sitemap", _fake_generate_sitemap)
    monkeypatch.setattr(workflow, "generate_page_with_retries", _fake_generate_page_with_retries)
    monkeypatch.setattr(workflow, "_generate_campaign_pages_best_effort", _fake_campaign_pages)
    monkeypatch.setattr(workflow, "compile_final", lambda *_args, **_kwargs: {"data": {"content": {}}})
    monkeypatch.setattr(workflow, "save_payload_json", lambda *_args, **_kwargs: "")

    payload = {
        "metadata": {"business_name": "Acme", "business_domain": "acme.com"},
        "user_data": {
            "rerun_overrides": {
                "specific_instructions": "Use a concise technical brand voice.",
                "added_pages": [
                    {
                        "path": "/service/network-audits",
                        "title": "Network Audits",
                        "content_page_type": "seo-service",
                    }
                ],
            }
        },
    }

    asyncio.run(workflow.run_workflow(payload, job_id=None))

    assert captured_sitemap_user_data["specific_instructions"] == "Use a concise technical brand voice."
    assert len(captured_payloads) == 2
    assert {item["this_page"]["path"] for item in captured_payloads} == {"/", "/service/network-audits"}
    for item in captured_payloads:
        assert item["userdata"]["specific_instructions"] == "Use a concise technical brand voice."
