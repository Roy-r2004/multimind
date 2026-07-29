from app.services.scraping.official_source_seed_service import (
    _dedupe_valid_seeds,
    _normalize_seed_payload,
)
from app.schemas.api import OfficialSourceSeed


def test_normalize_seed_payload_accepts_url_strings():
    payload = _normalize_seed_payload(
        {"sources": ["https://valvira.fi/en/licensing", {"url": "https://paihdelinkki.fi/"}]},
        max_sources=8,
    )
    assert len(payload["sources"]) == 2
    assert payload["sources"][0]["url"].startswith("https://")


def test_dedupe_valid_seeds_keeps_canonical_https():
    seeds = _dedupe_valid_seeds(
        [
            OfficialSourceSeed(
                url="https://valvira.fi/path?utm_source=x",
                title="Valvira",
                purpose="Official registry",
                trust_tier="official",
            ),
            OfficialSourceSeed(
                url="https://valvira.fi/path",
                title="Valvira dup",
                purpose="Dup",
                trust_tier="high",
            ),
            OfficialSourceSeed(
                url="not-a-url",
                title="Bad",
                purpose="Bad",
                trust_tier="official",
            ),
        ],
        max_sources=8,
    )
    assert len(seeds) == 1
    assert seeds[0].url.startswith("https://valvira.fi/path")
