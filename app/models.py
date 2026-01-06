from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal, Union
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


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
    html_title: str = ""
    meta_description: str = ""
    home_hero: Hero = Field(default_factory=Hero)
    home_stakes: StakesHome = Field(default_factory=StakesHome)
    home_values: ValuesHome = Field(default_factory=ValuesHome)
    home_guide: Guide = Field(default_factory=Guide)
    home_cta: CTA = Field(default_factory=CTA)


class AboutPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    seo_page_type: Literal["service", "industry", "location"] = "service"
    post_title: str = ""
    post_name: str = ""
    post_status: str = "publish"
    fields: SEOFields = Field(default_factory=SEOFields)


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
    path: str
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
class FinalCopyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home: HomePayload = Field(default_factory=HomePayload)
    about: AboutPayload = Field(default_factory=AboutPayload)
    seo_pages: List[SEOPageItem] = Field(default_factory=list)
    utility_pages: List[UtilityAboutItem] = Field(default_factory=list)


# -------------------------
# Input schema for webhook payload
# -------------------------
class MetadataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_name: str = ""
    businessDomain: str = ""
    submission_datetime: Optional[datetime] = None
    service_type: str = ""


class WhyChooseUsPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generate_page: bool = False
    why_choose_us_description: str = ""


class TaggedPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    position: str = ""
    bio: str = ""
    x: float = 0.0
    y: float = 0.0


class TeamPhotoWithTags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    imageUrl: str = ""
    taggedPeople: List[TaggedPerson] = Field(default_factory=list)


class MeetTheTeamPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generate_page: bool = False
    team_introduction: str = ""
    team_photo_with_tags: TeamPhotoWithTags = Field(default_factory=TeamPhotoWithTags)


class AdditionalPagesList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    why_choose_us_page: WhyChooseUsPage = Field(default_factory=WhyChooseUsPage)
    meet_the_team_page: MeetTheTeamPage = Field(default_factory=MeetTheTeamPage)


class GeographicAreaMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    label: str = ""
    lat: str = ""
    lon: str = ""
    place_id: str = ""
    source: str = ""
    primary: bool = False


class GeographicArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geographic_area_meta: GeographicAreaMeta = Field(default_factory=GeographicAreaMeta)


class CertificationPartnership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cert_item_name: str = ""
    cert_item_type: str = ""
    cert_item_image_url: str = ""
    cert_item_file_url: str = ""


class ServiceGuaranteeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guarantee_name: str = ""
    guarantee_type: str = ""
    guarantee_file_url: str = ""
    guarantee_description: str = ""


class UserdataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
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


class WebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: MetadataInput = Field(default_factory=MetadataInput)
    userdata: UserdataInput = Field(default_factory=UserdataInput)


WebhookInput.model_rebuild()
