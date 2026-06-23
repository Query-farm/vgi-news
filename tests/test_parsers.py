"""Unit tests for provider JSON -> unified-schema mapping.

Drives the parsers against captured fixture responses (no network). Covers:
seendate parsing, tone parsing (GDELT carries none in ArtList -> NULL),
missing-field -> NULL, the ``extra`` JSON, and the NewsAPI error path.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

import pytest

from vgi_news.providers import gdelt, newsapi
from vgi_news.providers.base import ProviderError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------- GDELT ------


def test_gdelt_seendate_parse():
    dt = gdelt.parse_seendate("20240615T123000Z")
    assert dt == datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)
    assert dt.tzinfo is UTC


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "20240631T999999Z"])
def test_gdelt_seendate_parse_missing_or_bad_is_none(bad):
    assert gdelt.parse_seendate(bad) is None


def test_gdelt_maps_unified_schema():
    rows = gdelt.map_response(_load("gdelt_artlist.json"))
    assert len(rows) == 3
    first = rows[0]
    assert first.title == "World leaders gather for climate summit"
    assert first.url == "https://www.bbc.co.uk/news/world-climate-summit-2024"
    assert first.domain == "bbc.co.uk"
    assert first.language == "English"
    assert first.country == "United Kingdom"
    assert first.seendate == datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)
    assert first.source == "gdelt"
    # GDELT ArtList rows have no per-article tone -> NULL.
    assert first.tone is None


def test_gdelt_missing_seendate_is_none():
    rows = gdelt.map_response(_load("gdelt_artlist.json"))
    third = rows[2]
    assert third.domain == "example.org"
    assert third.seendate is None  # missing field -> NULL


def test_gdelt_extra_json_carries_unmapped_fields():
    rows = gdelt.map_response(_load("gdelt_artlist.json"))
    extra = json.loads(rows[0].extra)
    # socialimage / url_mobile are not unified columns -> live in extra.
    assert extra["socialimage"] == "https://www.bbc.co.uk/img/summit.jpg"
    assert "url_mobile" in extra


def test_gdelt_tone_parsed_when_present():
    row = gdelt.map_article({"title": "x", "url": "u", "seendate": "20240615T120000Z", "tone": "-3.5"})
    assert row.tone == -3.5


def test_gdelt_articles_not_a_list_raises():
    with pytest.raises(ProviderError):
        gdelt.map_response({"articles": "oops"})


# --------------------------------------------------------------- NewsAPI -----


def test_newsapi_published_at_parse():
    dt = newsapi.parse_published_at("2024-06-15T12:30:00Z")
    assert dt == datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)


@pytest.mark.parametrize("bad", [None, "", "garbage"])
def test_newsapi_published_at_missing_is_none(bad):
    assert newsapi.parse_published_at(bad) is None


def test_newsapi_maps_unified_schema():
    rows, total = newsapi.map_response(_load("newsapi_everything.json"))
    assert total == 42
    assert len(rows) == 2
    first = rows[0]
    assert first.title == "Markets rally after election results"
    assert first.url == "https://www.reuters.com/markets/election-rally"
    assert first.domain == "reuters.com"  # derived from URL host
    assert first.seendate == datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)
    assert first.source == "newsapi"
    # NewsAPI has no sentiment and /everything has no country/language.
    assert first.tone is None
    assert first.country is None
    assert first.language is None
    extra = json.loads(first.extra)
    assert extra["source_name"] == "Reuters"
    assert extra["author"] == "Jane Doe"


def test_newsapi_null_author_omitted_from_extra():
    rows, _ = newsapi.map_response(_load("newsapi_everything.json"))
    extra = json.loads(rows[1].extra)
    assert "author" not in extra  # null author -> omitted
    assert extra["source_name"] == "The Guardian"


def test_newsapi_error_payload_raises():
    with pytest.raises(ProviderError, match="invalid"):
        newsapi.map_response(_load("newsapi_error.json"))
