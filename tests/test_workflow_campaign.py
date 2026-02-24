import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")

from app import workflow


def test_generate_campaign_pages_best_effort_continues_on_failure(monkeypatch):
    async def _fake_generate_campaign_page_with_retries(**kwargs):
        slug = kwargs.get("campaign_slug")
        if slug == "discoverycall":
            raise RuntimeError("boom")
        return {
            "data": {
                "content": {
                    "slug": slug,
                    "title": "Campaign Title",
                    "subtitle": "Campaign Subtitle",
                    "content": "<p>Main</p>",
                    "desc-content": "<p>Desc</p>",
                }
            }
        }

    monkeypatch.setattr(workflow, "generate_campaign_page_with_retries", _fake_generate_campaign_page_with_retries)

    logs = {"i": [], "d": [], "e": []}
    progress = []

    async def _log_i(msg: str):
        logs["i"].append(msg)

    async def _log_d(msg: str):
        logs["d"].append(msg)

    async def _log_e(msg: str):
        logs["e"].append(msg)

    async def _prog(patch):
        progress.append(dict(patch))

    out = asyncio.run(
        workflow._generate_campaign_pages_best_effort(
            metadata={},
            user_data={},
            job_details={},
            sitemap_data={},
            job_id=None,
            log_i=_log_i,
            log_d=_log_d,
            log_e=_log_e,
            prog=_prog,
        )
    )

    assert len(out) == 1
    assert out[0]["data"]["content"]["slug"] == "it-buyers-guide"
    assert any("campaign_page_start: path=/campaign/discoverycall slug=discoverycall" in msg for msg in logs["i"])
    assert any("campaign_page_done: path=/campaign/it-buyers-guide slug=it-buyers-guide" in msg for msg in logs["i"])
    assert any("campaign_page_failed: path=/campaign/discoverycall slug=discoverycall" in msg for msg in logs["e"])
    assert any("campaign_page_traceback: path=/campaign/discoverycall slug=discoverycall" in msg for msg in logs["e"])
    assert any("campaign_pages_summary: total=2 done=1 failed=1" in msg for msg in logs["i"])
    assert any(p.get("campaign_pages_failed") == 1 for p in progress)


def test_ensure_final_content_container_exists():
    final_copy = {}
    content = workflow._ensure_final_content_container(final_copy)
    content["campaign_pages"] = [{"data": {"content": {"slug": "x"}}}]

    assert final_copy["data"]["content"]["campaign_pages"][0]["data"]["content"]["slug"] == "x"


def test_questionnaire_campaign_pages_placeholder_is_safe():
    assert workflow._questionnaire_campaign_pages({}) == []
    assert workflow._questionnaire_campaign_pages({"additional_campaigns": None}) == []
    assert workflow._questionnaire_campaign_pages({"additional_campaigns": ["future-page"]}) == []
