from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal, Union
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator, AliasChoices

from .webhook_utils import normalize_webhook_payload


def _to_camel(string: str) -> str:
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class WebhookBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        alias_generator=_to_camel,
    )


# -------------------------
# Core content sub-models
# -------------------------
class Hero(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    content: str = ""


class HeadingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = ""
    content: str = ""


class FAQItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = ""
    answer: str = ""


class StakesHome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    home_stakes_content: List[HeadingItem] = Field(default_factory=list)


class ValuesHome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    home_values_content: List[HeadingItem] = Field(default_factory=list)


class StakesAbout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    about_stakes_content: List[HeadingItem] = Field(default_factory=list)


class ValuesAbout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    about_values_content: List[HeadingItem] = Field(default_factory=list)


class Guide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    content: str = ""


class CTA(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    content: str = ""

# -------------------------
# Sitemap Models (Corrected Order)
# -------------------------

# 1. Define this FIRST so it exists when SitemapMeta tries to use it.
#    (I renamed 'Counts' to 'SitemapMetaCounts' to match your reference below)
class SitemapMetaCounts(BaseModel):
    model_config = ConfigDict(extra="allow")  # Changed to 'allow' to prevent validation errors on extra keys
    mandatory: int = 0
    optional: int = 0
    service_details: int = 0
    industry_details: int = 0
    location_details: int = 0
    total: int = 0


# 2. Define SitemapMeta SECOND.
class SitemapMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_name_sanitized: str = ""
    service_type: str = ""
    locale: str = "en-US"
    counts: SitemapMetaCounts = Field(default_factory=SitemapMetaCounts)
    budget_ok: bool = True
    validation_passed: bool = True


# 3. Rest of the models follow...
class SitemapRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    page_type: str
    page_title: str
    html_title: str
    meta_description: str
    index: bool
    follow: bool
    canonical: str
    sort_order: int
    locale: str
    notes: str
    generative_content: bool
    content_page_type: str
    navigation_category: Optional[str] = None
    navigation_label: Optional[str] = None

class SitemapData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    meta: SitemapMeta
    headers: List[Any]
    rows: List[SitemapRow]

class SitemapAssistantOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sitemap_data: SitemapData

# -------------------------
# Home/About payloads
# -------------------------
class HomePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    page_title: Optional[str] = None
    html_title: str = ""
    meta_description: str = ""
    home_hero: Hero = Field(default_factory=Hero)
    home_stakes: StakesHome = Field(default_factory=StakesHome)
    home_values: ValuesHome = Field(default_factory=ValuesHome)
    home_guide: Guide = Field(default_factory=Guide)
    home_cta: CTA = Field(default_factory=CTA)


class AboutPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    page_title: Optional[str] = None
    html_title: str = ""
    meta_description: str = ""
    about_hero: Hero = Field(default_factory=Hero)
    about_stakes: StakesAbout = Field(default_factory=StakesAbout)
    about_values: ValuesAbout = Field(default_factory=ValuesAbout)
    about_guide: Guide = Field(default_factory=Guide)
    about_cta: CTA = Field(default_factory=CTA)


# -------------------------
# SEO payload
# -------------------------
class SEOStakes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    seo_stakes_content: List[HeadingItem] = Field(default_factory=list)


class SEOValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    seo_values_content: List[HeadingItem] = Field(default_factory=list)


class SEOFAQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    content: List[FAQItem] = Field(default_factory=list)


class SEOFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    html_title: str = ""
    meta_description: str = ""
    seo_hero: Hero = Field(default_factory=Hero)
    seo_stakes: SEOStakes = Field(default_factory=SEOStakes)
    seo_values: SEOValues = Field(default_factory=SEOValues)
    seo_faq: SEOFAQ = Field(default_factory=SEOFAQ)
    seo_cta: CTA = Field(default_factory=CTA)


class SEOPageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    page_title: Optional[str] = None
    seo_page_type: str = "service"
    post_title: str = ""
    post_name: str = ""
    post_status: str = "publish"
    fields: SEOFields = Field(default_factory=SEOFields)

    @field_validator("seo_page_type", mode="before")
    @classmethod
    def _normalize_seo_page_type(cls, v):
        t = str(v or "").strip().lower()
        mapping = {
            "service": "service",
            "seo-service": "service",
            "industry": "industry",
            "seo-industry": "industry",
            "location": "location",
            "seo-location": "location",
        }
        return mapping.get(t, "service")


# -------------------------
# Envelopes returned by assistant
# -------------------------
class HomeEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_kind: Literal["home"]
    path: str
    home: HomePayload


class AboutEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_kind: Literal["about"]
    path: str
    about: AboutPayload


class SEOEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_kind: Literal["seo_page"]
    path: str
    seo_page: SEOPageItem


class UtilityAboutItem(AboutPayload):
    # Same as AboutPayload plus identifiers
    model_config = ConfigDict(extra="forbid")
    content_page_type: Literal["about-why", "about-team"]


class UtilityEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_kind: Literal["utility_page"]
    path: str
    utility_page: UtilityAboutItem


class SkipEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_kind: Literal["skip"]
    path: str
    reason: str = "non-generative"


AssistantEnvelope = Union[HomeEnvelope, AboutEnvelope, SEOEnvelope, UtilityEnvelope, SkipEnvelope]


# -------------------------
# Final compiled output schema
# -------------------------
class UtilityAboutContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    content: str = ""


class UtilityAboutValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = ""
    subtitle: str = ""
    about_values_content: List[HeadingItem] = Field(default_factory=list)


class UtilityPageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: None = None
    page_title: str = ""
    slug: str = ""
    html_title: str = ""
    meta_description: str = ""
    about_content: UtilityAboutContent = Field(default_factory=UtilityAboutContent)
    about_values: UtilityAboutValues = Field(default_factory=UtilityAboutValues)
    about_cta: CTA = Field(default_factory=CTA)


class FinalCopyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home: HomePayload = Field(default_factory=HomePayload)
    about: AboutPayload = Field(default_factory=AboutPayload)
    seo_pages: List[SEOPageItem] = Field(default_factory=list)
    utility_pages: List[UtilityPageOutput] = Field(default_factory=list)


# -------------------------
# Input schema for webhook payload
# -------------------------
class MetadataInput(WebhookBaseModel):
    business_name: str = ""
    business_domain: str = Field(
        default="",
        validation_alias=AliasChoices(
            "business_domain",
            "businessDomain",
            "domain_name",
            "domainName",
        ),
    )
    submission_datetime: Optional[datetime] = None
    service_type: str = ""


class WhyChooseUsPage(WebhookBaseModel):
    generate_page: bool = False
    why_choose_us_description: str = ""


class TaggedPerson(WebhookBaseModel):
    name: str = ""
    position: str = ""
    bio: str = ""
    x: float = 0.0
    y: float = 0.0


class TeamPhotoWithTags(WebhookBaseModel):
    image_url: str = ""
    tagged_people: List[TaggedPerson] = Field(default_factory=list)


class MeetTheTeamPage(WebhookBaseModel):
    generate_page: bool = False
    team_introduction: str = ""
    team_photo_with_tags: TeamPhotoWithTags = Field(default_factory=TeamPhotoWithTags)


class AdditionalPagesList(WebhookBaseModel):
    why_choose_us_page: WhyChooseUsPage = Field(default_factory=WhyChooseUsPage)
    meet_the_team_page: MeetTheTeamPage = Field(default_factory=MeetTheTeamPage)


class GeographicAreaMeta(WebhookBaseModel):
    name: str = ""
    label: str = ""
    lat: str = ""
    lon: str = ""
    place_id: str = ""
    source: str = ""
    primary: bool = False


class GeographicArea(WebhookBaseModel):
    geographic_area_meta: GeographicAreaMeta = Field(default_factory=GeographicAreaMeta)


class CertificationPartnership(WebhookBaseModel):
    cert_item_name: str = ""
    cert_item_type: str = ""
    cert_item_image_url: str = ""
    cert_item_file_url: str = ""


class ServiceGuaranteeItem(WebhookBaseModel):
    guarantee_name: str = ""
    guarantee_type: str = ""
    guarantee_file_url: str = ""
    guarantee_description: str = ""


class UserDataInput(WebhookBaseModel):
    additional_pages_list: AdditionalPagesList = Field(default_factory=AdditionalPagesList)
    service_offerings: List[str] = Field(default_factory=list)
    service_offerings_other: str = ""
    target_industries: List[str] = Field(default_factory=list)
    target_industries_other: str = ""
    geographic_areas: List[GeographicArea] = Field(default_factory=list)
    company_description: str = ""
    delivery_model: str = ""
    delivery_model_other: str = ""
    pricing_packaging: List[str] = Field(default_factory=list)
    pricing_packaging_other: str = ""
    differentiation: str = ""
    company_goals: List[str] = Field(default_factory=list)
    company_goals_other: str = ""
    brand_tone: str = ""
    brand_tone_other: str = ""
    certifications_partnerships: List[CertificationPartnership] = Field(default_factory=list)
    sales_process: str = ""
    service_guarantee: bool = False
    service_guarantee_items: List[ServiceGuaranteeItem] = Field(default_factory=list)
    client_acquisition: str = ""
    client_acquisition_other: str = ""
    website_objectives: List[str] = Field(default_factory=list)
    website_objectives_other: str = ""
    client_size: str = ""
    client_challenges: List[str] = Field(default_factory=list)
    client_challenges_other: str = ""
    client_frustrations: str = ""
    client_outcomes: List[str] = Field(default_factory=list)
    client_outcomes_other: str = ""
    value_description: str = ""
    ideal_client: str = ""
    avoided_clients: str = ""
    primary_cta: str = ""
    primary_cta_other: str = ""
    additional_notes: str = ""


class WebhookInput(WebhookBaseModel):
    metadata: MetadataInput = Field(default_factory=MetadataInput)
    user_data: UserDataInput = Field(
        default_factory=UserDataInput,
        validation_alias=AliasChoices("user_data", "userData", "userdata"),
    )
    query_string: Dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("query_string", "queryString", "querystring"),
    )
    job_details: Dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("job_details", "jobDetails", "jobdetails"),
    )
    sitemap_data: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload_keys(cls, data):
        return normalize_webhook_payload(data)


WebhookInput.model_rebuild()
