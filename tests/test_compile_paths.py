import pytest

from app.compile import compile_final


def test_compile_final_paths_present_and_canonical():
    envelopes = [
        {"page_kind": "home", "path": "", "home": {}},
        {"page_kind": "about", "path": "", "about": {}},
        {
            "page_kind": "seo_page",
            "path": "",
            "seo_page": {"seo_page_type": "service", "post_name": "HVAC Repair"},
        },
        {
            "page_kind": "seo_page",
            "path": "/industries/healthcare",
            "seo_page": {"seo_page_type": "industry", "post_name": "Healthcare"},
        },
        {
            "page_kind": "utility_page",
            "path": "",
            "utility_page": {"content_page_type": "about-why", "html_title": "Why Choose Us"},
        },
    ]

    final = compile_final(envelopes)

    assert final["home"]["path"] == "/"
    assert final["about"]["path"] == "/about"

    seo_paths = [page["path"] for page in final["seo_pages"]]
    assert seo_paths == ["/services/hvac-repair", "/industries/healthcare"]
    assert len(seo_paths) == len(set(seo_paths))

    assert final["utility_pages"][0]["path"] == "/about/why-choose-us"
    assert all(page["path"] for page in final["utility_pages"])


def test_compile_final_raises_on_missing_seo_slug():
    envelopes = [
        {
            "page_kind": "seo_page",
            "path": "",
            "seo_page": {},
        }
    ]

    with pytest.raises(ValueError, match="seo_page"):
        compile_final(envelopes)
