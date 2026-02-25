import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PRO_COPY_ASSISTANT_ID", "test-copy")
os.environ.setdefault("PRO_SITEMAP_ASSISTANT_ID", "test-sitemap")

from app.compile import compile_final
from app.models import CampaignPageItem
from app.payload_store import load_payload_json, save_payload_json
from app.tasks import _build_zapier_payload


def test_build_zapier_payload_keeps_campaign_pages_under_data_content():
    row = {}
    target_url = "https://example.com"
    content = {
        "data": {
            "content": {
                "home": {"path": "/"},
                "campaign_pages": [
                    {
                        "data": {
                            "content": {
                                "slug": "discoverycall",
                                "title": "Discovery Call",
                                "subtitle": "Book now",
                                "content": "<p>Main</p>",
                                "desc-content": "<p>Desc</p>",
                            }
                        }
                    }
                ],
            }
        }
    }

    payload = _build_zapier_payload(row, target_url, content)

    assert payload["metadata"]["deliveryDomain"] == target_url
    assert payload["data"]["content"]["campaign_pages"][0]["data"]["content"]["slug"] == "discoverycall"
    assert payload["data"]["content"]["campaign_pages"][0]["data"]["content"]["desc-content"] == "<p>Desc</p>"


def test_storage_and_delivery_keep_desc_content_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYLOAD_DISK_DIR", str(tmp_path))

    campaign_item = CampaignPageItem.model_validate(
        {
            "data": {
                "content": {
                    "slug": "it-buyers-guide",
                    "title": "IT Buyer's Guide",
                    "subtitle": "Download now",
                    "content": "<p>Main</p>",
                    "desc_content": "<p>Alias must be hyphenated</p>",
                }
            }
        }
    )
    compiled = compile_final([], campaign_pages=[campaign_item])

    ref = save_payload_json("job-campaign-alias", compiled)
    loaded = load_payload_json(ref)
    payload = _build_zapier_payload({}, "https://example.com", loaded)

    page = payload["data"]["content"]["campaign_pages"][0]["data"]["content"]
    assert page["desc-content"] == "<p>Alias must be hyphenated</p>"
    assert "desc_content" not in page
