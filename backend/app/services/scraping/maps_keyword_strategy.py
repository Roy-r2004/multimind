"""Targeted keyword strategy for addiction treatment facility discovery.

Combines facility type, modifiers, and addiction-specific terms to generate
high-quality Google Maps search queries. Search words are hidden from users;
only results are displayed.
"""

from dataclasses import dataclass

# Core facility types - combine these with modifiers and addiction terms
CORE_FACILITY_TYPES = [
    "private inpatient rehab",
    "residential addiction treatment",
    "inpatient rehabilitation center",
    "private rehab center",
    "addiction treatment center",
    "residential rehab",
    "addiction clinic",
    "NGO rehab center",
    "charity-funded rehab",
]

# Modifiers to refine searches
MODIFIERS = [
    "inpatient",
    "residential",
    "private",
    "NGO",
    "charity-funded",
    "non-government",
    "24 hour care",
    "dedicated beds",
]

# Substance-specific addictions
SUBSTANCE_ADDICTIONS = [
    "alcohol",
    "cocaine",
    "heroin",
    "methamphetamine",
    "prescription opioids",
    "benzodiazepines",
    "cannabis dependency",
    "synthetic cannabinoids",
    "synthetic stimulants",
    "inhalants",
    "stimulant medications",
    "ketamine",
    "kratom",
    "MDMA",
    "GHB",
    "anabolic steroids",
    "novel psychoactive substances",
]

# Behavioral addictions
BEHAVIORAL_ADDICTIONS = [
    "gambling addiction",
    "sex addiction",
    "pornography addiction",
    "gaming addiction",
    "internet addiction",
    "food addiction",
    "shopping addiction",
    "spending/buying addiction",
    "exercise addiction",
    "workaholism",
    "social media addiction",
    "cryptocurrency trading addiction",
]

# Exclusion keywords - used to filter OUT bad results
EXCLUSION_KEYWORDS = [
    "outpatient",
    "telehealth",
    "virtual",
    "general psychiatric",
    "mental health clinic",
    "wellness retreat",
    "sober living",
    "coaching center",
    "solo practitioner",
    "government-funded",
    "public health",
]

# Language translations for multilingual support
TRANSLATIONS = {
    "FR": {  # French
        "facility": ["clinique de désintoxication privée", "centre de réadaptation résidentiel", "centre de traitement des dépendances"],
        "substance": ["alcool", "cocaïne", "héroïne", "méthédrine", "opioïdes", "benzodiazépines", "cannabis", "MDMA"],
    },
    "ES": {  # Spanish
        "facility": ["clínica de desintoxicación privada", "centro de rehabilitación residencial", "centro de tratamiento de adicciones"],
        "substance": ["alcohol", "cocaína", "heroína", "metanfetamina", "opioides", "benzodiazepinas", "cannabis", "MDMA"],
    },
}


def generate_search_queries(city_name: str, region_name: str = "", country_code: str = "") -> list[str]:
    """Generate targeted search queries for a city/region.

    Combines facility types with addiction-specific terms to create high-quality
    Google Maps search queries. Results are much more targeted than generic searches.

    Args:
        city_name: City to search (e.g., "Paris", "Algiers")
        region_name: Region/state (optional, added to location context)
        country_code: 2-letter country code for language selection (optional)

    Returns:
        List of search queries, hidden from UI
    """
    location = city_name
    if region_name:
        location = f"{city_name} {region_name}"

    queries = []

    # Pattern 1: Core facility type + location
    for facility in CORE_FACILITY_TYPES[:5]:  # Use top 5 core types
        queries.append(f"{facility} {location}")

    # Pattern 2: Core facility + addiction type + location
    for facility in CORE_FACILITY_TYPES[:3]:
        for addiction in SUBSTANCE_ADDICTIONS[:3]:  # Top substances
            queries.append(f"{facility} {addiction} {location}")

    # Pattern 3: Facility + behavioral addiction + location
    for facility in CORE_FACILITY_TYPES[:2]:
        for addiction in BEHAVIORAL_ADDICTIONS[:2]:  # Top behavioral
            queries.append(f"{facility} {addiction} {location}")

    # Pattern 4: Modifier + facility + location (for refinement)
    for modifier in MODIFIERS[:3]:
        queries.append(f"{modifier} {CORE_FACILITY_TYPES[0]} {location}")

    # Pattern 5: Multilingual if country specified
    if country_code in TRANSLATIONS:
        trans = TRANSLATIONS[country_code]
        if "facility" in trans:
            for facility in trans["facility"][:2]:
                queries.append(f"{facility} {location}")
        if "substance" in trans:
            for substance in trans["substance"][:2]:
                queries.append(f"{facility} {substance} {location}")

    # Remove duplicates while preserving order
    seen = set()
    unique_queries = []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique_queries.append(q)

    return unique_queries[:20]  # Return top 20 queries


def should_exclude_result(name: str, place_types: list[str]) -> bool:
    """Check if a result should be filtered out based on exclusion keywords.

    Args:
        name: Place name/title
        place_types: Google Place types

    Returns:
        True if should exclude, False if should keep
    """
    if not name:
        return True

    name_lower = name.lower()

    # Exclude if name contains exclusion keywords
    for keyword in EXCLUSION_KEYWORDS:
        if keyword.lower() in name_lower:
            return True

    # Exclude if place type suggests outpatient/non-clinical
    exclude_types = ["point_of_interest", "health", "wellness"]
    for ptype in (place_types or []):
        if any(e in ptype.lower() for e in exclude_types):
            # Check if also contains inpatient/residential signal
            if not any(sig in name_lower for sig in ["inpatient", "residential", "center", "clinic"]):
                return True

    return False
