from app.compile import compile_final


def test_compile_keeps_utility_page_title():
    envelopes = [
        {
            "page_kind": "utility_page",
            "path": "/about/why-choose-us",
            "utility_page": {
                "content_page_type": "about-why",
                "html_title": "Why Choose Us",
                "page_title": "Why Choose Us",
            },
        }
    ]

    final = compile_final(envelopes)

    assert final["utility_pages"][0]["page_title"] == "Why Choose Us"


def test_compile_omits_page_title_when_missing():
    envelopes = [
        {
            "page_kind": "utility_page",
            "path": "/about/why-choose-us",
            "utility_page": {
                "content_page_type": "about-why",
                "html_title": "Why Choose Us",
            },
        }
    ]

    final = compile_final(envelopes)

    assert "page_title" not in final["utility_pages"][0]
