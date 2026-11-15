"""Project-wide configuration: paths, study period and the airport catalogue."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
DOCS_DATA = ROOT / "docs" / "data"

# Flight-level traffic year (OpenSky flight lists end in Dec 2022)
TRAFFIC_YEAR = 2022
# Years used for the weather / delay study (EUROCONTROL + Open-Meteo)
DELAY_YEARS = (2022, 2023, 2024)
# Years used for UK route punctuality (CAA)
CAA_YEARS = (2022, 2023, 2024, 2025)
# Year the forecast heatmaps are generated for
FORECAST_YEAR = 2026


@dataclass(frozen=True)
class Airport:
    icao: str
    iata: str
    name: str
    city: str
    country: str  # ISO 3166-1 alpha-2, used for public holidays
    tz: str
    lat: float
    lon: float
    caa_name: str | None = None  # how the CAA spells the airport, if it differs


AIRPORTS: tuple[Airport, ...] = (
    Airport("EGLL", "LHR", "Heathrow", "London", "GB", "Europe/London", 51.4706, -0.4619, "HEATHROW"),
    Airport("EGKK", "LGW", "Gatwick", "London", "GB", "Europe/London", 51.1481, -0.1903, "GATWICK"),
    Airport("EGCC", "MAN", "Manchester", "Manchester", "GB", "Europe/London", 53.3537, -2.2750, "MANCHESTER"),
    Airport("EIDW", "DUB", "Dublin", "Dublin", "IE", "Europe/Dublin", 53.4213, -6.2701, "DUBLIN"),
    Airport("LFPG", "CDG", "Charles de Gaulle", "Paris", "FR", "Europe/Paris", 49.0097, 2.5479, "PARIS (CHARLES DE GAULLE)"),
    Airport("LFPO", "ORY", "Orly", "Paris", "FR", "Europe/Paris", 48.7262, 2.3652, "PARIS (ORLY)"),
    Airport("LFMN", "NCE", "Nice Côte d'Azur", "Nice", "FR", "Europe/Paris", 43.6584, 7.2159, "NICE"),
    Airport("EHAM", "AMS", "Schiphol", "Amsterdam", "NL", "Europe/Amsterdam", 52.3105, 4.7683, "AMSTERDAM"),
    Airport("EBBR", "BRU", "Brussels", "Brussels", "BE", "Europe/Brussels", 50.9014, 4.4844, "BRUSSELS"),
    Airport("EDDF", "FRA", "Frankfurt", "Frankfurt", "DE", "Europe/Berlin", 50.0379, 8.5622, "FRANKFURT MAIN"),
    Airport("EDDM", "MUC", "Munich", "Munich", "DE", "Europe/Berlin", 48.3538, 11.7861, "MUNICH"),
    Airport("EDDB", "BER", "Berlin Brandenburg", "Berlin", "DE", "Europe/Berlin", 52.3667, 13.5033, "BERLIN BRANDENBURG"),
    Airport("LSZH", "ZRH", "Zurich", "Zurich", "CH", "Europe/Zurich", 47.4582, 8.5555, "ZURICH"),
    Airport("LSGG", "GVA", "Geneva", "Geneva", "CH", "Europe/Zurich", 46.2381, 6.1090, "GENEVA"),
    Airport("LOWW", "VIE", "Vienna", "Vienna", "AT", "Europe/Vienna", 48.1103, 16.5697, "VIENNA"),
    Airport("LEMD", "MAD", "Madrid-Barajas", "Madrid", "ES", "Europe/Madrid", 40.4983, -3.5676, "MADRID"),
    Airport("LEBL", "BCN", "Barcelona-El Prat", "Barcelona", "ES", "Europe/Madrid", 41.2974, 2.0833, "BARCELONA"),
    Airport("LEPA", "PMI", "Palma de Mallorca", "Palma", "ES", "Europe/Madrid", 39.5517, 2.7388, "PALMA DE MALLORCA"),
    Airport("LEMG", "AGP", "Málaga", "Málaga", "ES", "Europe/Madrid", 36.6749, -4.4991, "MALAGA"),
    Airport("LPPT", "LIS", "Lisbon", "Lisbon", "PT", "Europe/Lisbon", 38.7742, -9.1342, "LISBON"),
    Airport("LIRF", "FCO", "Rome Fiumicino", "Rome", "IT", "Europe/Rome", 41.8003, 12.2389, "ROME (FIUMICINO)"),
    Airport("LIMC", "MXP", "Milan Malpensa", "Milan", "IT", "Europe/Rome", 45.6306, 8.7281, "MILAN (MALPENSA)"),
    Airport("LGAV", "ATH", "Athens", "Athens", "GR", "Europe/Athens", 37.9364, 23.9445, "ATHENS"),
    Airport("EKCH", "CPH", "Copenhagen", "Copenhagen", "DK", "Europe/Copenhagen", 55.6180, 12.6508, "COPENHAGEN"),
    Airport("ESSA", "ARN", "Stockholm Arlanda", "Stockholm", "SE", "Europe/Stockholm", 59.6498, 17.9238, "STOCKHOLM (ARLANDA)"),
    Airport("ENGM", "OSL", "Oslo Gardermoen", "Oslo", "NO", "Europe/Oslo", 60.1976, 11.1004, "OSLO (GARDERMOEN)"),
    Airport("EFHK", "HEL", "Helsinki-Vantaa", "Helsinki", "FI", "Europe/Helsinki", 60.3172, 24.9633, "HELSINKI"),
    Airport("EPWA", "WAW", "Warsaw Chopin", "Warsaw", "PL", "Europe/Warsaw", 52.1657, 20.9671, "WARSAW (CHOPIN)"),
    Airport("LKPR", "PRG", "Prague", "Prague", "CZ", "Europe/Prague", 50.1008, 14.2600, "PRAGUE"),
    Airport("LHBP", "BUD", "Budapest", "Budapest", "HU", "Europe/Budapest", 47.4369, 19.2556, "BUDAPEST"),
)

AIRPORT_BY_ICAO = {a.icao: a for a in AIRPORTS}
ICAO_CODES = frozenset(AIRPORT_BY_ICAO)

DOW_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
