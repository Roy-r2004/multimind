"""Operator-run script: deliberately acquire one country's boundary data
from geoBoundaries gbOpen into the local cache used by
``app.services.scraping.maps_geo_boundaries``.

This is the ONLY place in the codebase that performs network I/O to fetch
boundary geometry — nothing in a live census run ever calls
``fetch_and_cache_boundary`` directly. Run this ahead of time, once per
country/level you want dataset-backed (rather than static-table or
text-only) geographic targeting for.

Usage:
    python -m scripts.fetch_geo_boundaries US --level ADM0 ADM1
    python -m scripts.fetch_geo_boundaries CA FR --level ADM1 --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.services.scraping.maps_geo_boundaries import (
    cache_path,
    fetch_and_cache_boundary,
    iso2_to_iso3,
)


async def _run(country_codes: list[str], levels: list[str], *, force: bool) -> int:
    exit_code = 0
    for country_code in country_codes:
        iso2 = country_code.strip().upper()
        iso3 = iso2_to_iso3(iso2)
        if not iso3:
            print(
                f"SKIP {iso2}: not a recognized ISO 3166-1 alpha-2 country code",
                file=sys.stderr,
            )
            exit_code = 1
            continue
        for level in levels:
            path = cache_path(iso3, level)
            try:
                record = await fetch_and_cache_boundary(iso3, level, force=force)
            except FileExistsError:
                print(f"SKIP {iso3}/{level}: already cached at {path} (use --force to re-fetch)")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED {iso3}/{level}: {exc}", file=sys.stderr)
                exit_code = 1
                continue
            print(
                f"OK {iso3}/{level} -> {path} "
                f"(license={record.license!r}, sha256={record.sha256[:12]}...)"
            )
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("country_codes", nargs="+", help="ISO2 country codes, e.g. US CA FR")
    parser.add_argument(
        "--level",
        nargs="+",
        default=["ADM0", "ADM1"],
        choices=["ADM0", "ADM1"],
        help="Administrative levels to fetch (default: both)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-fetch even if already cached"
    )
    args = parser.parse_args()
    exit_code = asyncio.run(_run(args.country_codes, args.level, force=args.force))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
