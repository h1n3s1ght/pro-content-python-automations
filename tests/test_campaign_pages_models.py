from app.compile import compile_final
from app.models import CampaignPageItem, FinalCopyOutput


def test_campaign_page_item_serializes_desc_content_alias():
    item = CampaignPageItem.model_validate(
        {
            "data": {
                "content": {
                    "slug": "spring-sale",
                    "title": "Spring Sale",
                    "subtitle": "Save now",
                    "content": "<p>Body</p>",
                    "desc_content": "<p>Description</p>",
                }
            }
        }
    )

    payload = item.model_dump()
    payload_by_alias = item.model_dump(by_alias=True)

    assert payload == {
        "data": {
            "content": {
                "slug": "spring-sale",
                "title": "Spring Sale",
                "subtitle": "Save now",
                "content": "<p>Body</p>",
                "desc-content": "<p>Description</p>",
            }
        }
    }
    assert payload_by_alias == payload
    assert "desc_content" not in payload["data"]["content"]


def test_campaign_page_item_accepts_desc_content_alias_on_input():
    item = CampaignPageItem.model_validate(
        {
            "data": {
                "content": {
                    "slug": "fall-sale",
                    "title": "Fall Sale",
                    "subtitle": "Limited time",
                    "content": "<p>Body</p>",
                    "desc-content": "<p>Alias input</p>",
                }
            }
        }
    )

    assert item.data.content.desc_content == "<p>Alias input</p>"


def test_final_output_includes_campaign_pages_default():
    final = FinalCopyOutput()
    assert final.model_dump(by_alias=True)["campaign_pages"] == []

    compiled = compile_final([])
    assert compiled["data"]["content"]["campaign_pages"] == []
