"""Fetch raw source data. Every download is idempotent: existing files are skipped.

Sources
-------
* OpenSky Network flight lists (Zenodo record 7923702), one CSV per month.
* EUROCONTROL airport arrival ATFM delay, one CSV per year.
* Open-Meteo historical weather archive, daily values per airport.
* UK CAA punctuality statistics, one "Full Analysis" CSV per month.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path

import requests

from .config import AIRPORTS, CAA_YEARS, DELAY_YEARS, RAW, TRAFFIC_YEAR

log = logging.getLogger(__name__)

ZENODO_RECORD = "https://zenodo.org/api/records/7923702"
EUROCONTROL_DELAY = "https://www.eurocontrol.int/performance/data/download/csv/apt_dly_{year}.csv.bz2"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
CAA_YEAR_PAGE = (
    "https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/"
    "uk-flight-punctuality-statistics/{year}/"
)
WEATHER_VARS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "weather_code",
)
HEADERS = {"User-Agent": "runway-rhythms-research/0.1"}


def _stream(url: str, dest: Path, retries: int = 3) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        log.info("skip %s (exists)", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=120, headers=HEADERS) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            tmp.replace(dest)
            log.info("saved %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
            return dest
        except requests.RequestException as exc:
            log.warning("attempt %d failed for %s: %s", attempt, url, exc)
            time.sleep(5 * attempt)
    raise RuntimeError(f"could not download {url}")


def opensky(year: int = TRAFFIC_YEAR) -> list[Path]:
    files = requests.get(ZENODO_RECORD, timeout=60, headers=HEADERS).json()["files"]
    wanted = sorted(
        (f for f in files if f["key"].startswith(f"flightlist_{year}")), key=lambda f: f["key"]
    )
    if not wanted:
        raise RuntimeError(f"no OpenSky flight lists found for {year}")
    out = RAW / "opensky"
    return [_stream(f["links"]["self"], out / f["key"]) for f in wanted]


def eurocontrol(years=DELAY_YEARS) -> list[Path]:
    out = RAW / "eurocontrol"
    return [_stream(EUROCONTROL_DELAY.format(year=y), out / f"apt_dly_{y}.csv.bz2") for y in years]


def weather(years=DELAY_YEARS) -> list[Path]:
    out = RAW / "weather"
    out.mkdir(parents=True, exist_ok=True)
    start, end = f"{min(years)}-01-01", f"{max(years)}-12-31"
    paths = []
    for apt in AIRPORTS:
        dest = out / f"{apt.icao}_{start[:4]}_{end[:4]}.json"
        paths.append(dest)
        if dest.exists():
            continue
        params = {
            "latitude": apt.lat,
            "longitude": apt.lon,
            "start_date": start,
            "end_date": end,
            "daily": ",".join(WEATHER_VARS),
            "timezone": apt.tz,
            "wind_speed_unit": "kmh",
        }
        for attempt in range(8):
            r = requests.get(OPEN_METEO, params=params, timeout=120, headers=HEADERS)
            if r.status_code != 429:
                break
            wait = 65 * (attempt + 1)
            log.info("weather API rate limit, waiting %ss", wait)
            time.sleep(wait)
        r.raise_for_status()
        dest.write_text(json.dumps(r.json()))
        log.info("weather %s ok", apt.icao)
        time.sleep(5.0)  # be polite to the free API
    return paths


_CAA_LINK = re.compile(
    r'href="(?P<href>[^"]+)"[^>]*>\s*(?:<span>)?\s*(?P<ym>\d{6})\s+Punctuality Statistics Full Analysis\s*\(CSV',
    re.IGNORECASE,
)


def caa(years=CAA_YEARS) -> list[Path]:
    out = RAW / "caa"
    paths = []
    for year in years:
        html = requests.get(CAA_YEAR_PAGE.format(year=year), timeout=60, headers=HEADERS).text
        found = {m["ym"]: m["href"] for m in _CAA_LINK.finditer(html)}
        if not found:
            log.warning("no CAA monthly files parsed for %s", year)
        for ym, href in sorted(found.items()):
            url = href if href.startswith("http") else "https://www.caa.co.uk" + href
            paths.append(_stream(url, out / f"caa_{ym}.csv"))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", default=["eurocontrol", "weather", "caa", "opensky"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for name in args.sources:
        {"opensky": opensky, "eurocontrol": eurocontrol, "weather": weather, "caa": caa}[name]()


if __name__ == "__main__":
    main()
