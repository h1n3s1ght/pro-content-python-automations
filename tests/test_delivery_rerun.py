import pytest
from pydantic import ValidationError

from app.delivery_rerun import build_rerun_payload, parse_rerun_request_from_form
from app.delivery_schemas import RerunRequest


def test_rerun_request_requires_conditional_subtypes():
    with pytest.raises(ValidationError):
        RerunRequest(
            mode="add_changes",
            new_pages=[
                {
                    "path": "/service/network-audits",
                    "title": "Network Audits",
                    "classification": "seo",
                }
            ],
        )



def test_parse_rerun_request_from_form_rejects_invalid_json():
    with pytest.raises(ValueError):
        parse_rerun_request_from_form(
            mode="add_changes",
            specific_instructions="x",
            new_pages_json="{not-json}",
        )



def test_build_rerun_payload_add_changes_normalizes_pages_and_replaces_duplicates():
    source = {
        "metadata": {"business_name": "Acme", "business_domain": "acme.com"},
        "user_data": {},
    }
    request = RerunRequest(
        mode="add_changes",
        specific_instructions="Shift tone to concise and technical.",
        new_pages=[
            {
                "path": "service/network-audits",
                "title": "Network Audits",
                "classification": "seo",
                "seo_subtype": "service",
            },
            {
                "path": "/about/meet-the-team",
                "title": "Meet The Team",
                "classification": "",
            },
            {
                "path": "/service/network-audits",
                "title": "Network Audits Updated",
                "classification": "",
            },
        ],
    )

    out = build_rerun_payload(
        source_payload=source,
        rerun_request=request,
        source_job_id="job-old",
        source_delivery_id="delivery-old",
    )

    overrides = out["user_data"]["rerun_overrides"]
    assert overrides["mode"] == "add_changes"
    assert overrides["specific_instructions"] == "Shift tone to concise and technical."
    assert overrides["source_job_id"] == "job-old"
    assert overrides["source_delivery_id"] == "delivery-old"

    pages = overrides["added_pages"]
    assert len(pages) == 2

    service_page = next(p for p in pages if p["path"] == "/service/network-audits")
    assert service_page["title"] == "Network Audits Updated"
    assert service_page["content_page_type"] == "seo-service"

    utility_page = next(p for p in pages if p["path"] == "/about/meet-the-team")
    assert utility_page["content_page_type"] == "about-team"



def test_build_rerun_payload_without_changes_adds_trace_metadata():
    source = {
        "metadata": {"business_name": "Acme"},
        "user_data": {"service_offerings": ["Managed IT"]},
    }

    out = build_rerun_payload(source_payload=source, rerun_request=None, source_job_id="job-old")

    assert out["user_data"]["rerun_overrides"]["mode"] == "without_changes"
    assert out["user_data"]["service_offerings"] == ["Managed IT"]
    assert out["job_details"]["rerun"]["source_job_id"] == "job-old"
