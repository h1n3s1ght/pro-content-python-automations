from __future__ import annotations
from typing import List, Literal, Optional, Union
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
# Expected Input JSON schema
# -------------------------

class WebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: dict
    userdata: dict


# -------------------------
# Final compiled output schema
# -------------------------
class FinalCopyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home: HomePayload = Field(default_factory=HomePayload)
    about: AboutPayload = Field(default_factory=AboutPayload)
    seo_pages: List[SEOPageItem] = Field(default_factory=list)
    utility_pages: List[UtilityAboutItem] = Field(default_factory=list)
