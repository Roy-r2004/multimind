"""Small, safe scraping amplifier utilities."""

from app.services.scraping.amplifiers.js_render_allowlist import is_js_render_allowed
from app.services.scraping.amplifiers.multi_source_fusion import choose_best_value
from app.services.scraping.amplifiers.pdf_text_extract import extract_pdf_text
from app.services.scraping.amplifiers.registry_seed_lists import build_registry_seed_list
from app.services.scraping.amplifiers.safe_geocode import safe_parse_coordinates

__all__ = [
    "build_registry_seed_list",
    "choose_best_value",
    "extract_pdf_text",
    "is_js_render_allowed",
    "safe_parse_coordinates",
]
