import asyncio
import importlib
import os

import pytest

from app.models import CampaignPageItem

os.environ["OPENAI_API_KEY"] = "test-key"

openai_campaign = importlib.import_module("app.openai_campaign")


def test_campaign_assistant_id_is_fixed():
    assert openai_campaign.CAMPAIGN_ASSISTANT_ID == "asst_Px0t2QTPDdxgL3hKUWFqsHJa"


def test_coerce_campaign_item_accepts_desc_content_and_serializes_alias():
    payload = {"campaign_path": "/campaign/discoverycall", "campaign_slug": "discoverycall"}
    raw = {
        "slug": "discoverycall",
        "title": "Discovery Call",
        "subtitle": "Book now",
        "content": "<p>Main content</p>",
        "desc_content": "<p>Description content</p>",
        "unexpected_field": "ignored",
    }

    coerced = openai_campaign._coerce_campaign_item(raw, payload)
    item = CampaignPageItem.model_validate(coerced)
    dumped = item.model_dump(by_alias=True)

    assert dumped["data"]["content"]["slug"] == "discoverycall"
    assert dumped["data"]["content"]["desc-content"] == "<p>Description content</p>"
    assert "desc_content" not in dumped["data"]["content"]


def test_generate_campaign_page_with_retries_returns_campaign_item(monkeypatch):
    async def _fake_call(payload, log_lines=None):
        return CampaignPageItem.model_validate(
            {
                "data": {
                    "content": {
                        "slug": payload.get("campaign_slug"),
                        "title": "Campaign Title",
                        "subtitle": "Campaign Subtitle",
                        "content": "<p>Main</p>",
                        "desc_content": "<p>Desc</p>",
                    }
                }
            }
        ).model_dump(by_alias=True)

    monkeypatch.setattr(openai_campaign, "_call_openai_with_transient_retries", _fake_call)

    out = asyncio.run(
        openai_campaign.generate_campaign_page_with_retries(
            metadata={"business_name": "Acme"},
            user_data={"service_offerings": ["Discovery"]},
            job_details={"foo": "bar"},
            sitemap_data={"rows": []},
            campaign_path="/campaign/discoverycall",
            campaign_slug="discoverycall",
        )
    )

    assert out["data"]["content"]["slug"] == "discoverycall"
    assert out["data"]["content"]["desc-content"] == "<p>Desc</p>"


def test_generate_campaign_page_with_retries_raises_structured_error(monkeypatch):
    async def _always_bad(payload, log_lines=None):
        raise openai_campaign.CampaignParseError("bad schema", details={"reason": "missing_fields"})

    monkeypatch.setattr(openai_campaign, "_call_openai_with_transient_retries", _always_bad)
    monkeypatch.setattr(openai_campaign, "MAX_CAMPAIGN_RETRIES", 1)

    with pytest.raises(openai_campaign.CampaignGenerationError) as exc:
        asyncio.run(
            openai_campaign.generate_campaign_page_with_retries(
                metadata={},
                user_data={},
                campaign_path="/campaign/discoverycall",
                campaign_slug="discoverycall",
            )
        )

    assert exc.value.details["campaign_slug"] == "discoverycall"
    assert exc.value.details["last_details"]["reason"] == "missing_fields"
