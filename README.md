# ✈️ Runway Rhythms

**When does Europe fly, and what makes it late?**

Runway Rhythms is an operations-analytics project on European air traffic. It covers 30 major UK and EU airports and joins three public data sources:

| Question | Source | Granularity |
|---|---|---|
| When do flights depart on a route (hour × weekday, month × weekday)? | [OpenSky Network](https://opensky-network.org) flight lists, 2022 | one row per flight |
| How punctual is a UK ↔ EU route, and how does British Airways compare? | [UK CAA punctuality statistics](https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/), 2022–2025 | route × airline × month |
| How much does weather drive arrival delays? | [EUROCONTROL airport arrival ATFM delay](https://www.ansperformance.eu/reference/dataset/airport-arrival-atfm-delay/) + [Open-Meteo](https://open-meteo.com) history, 2022–2024 | airport × day |

## Project layout

```
src/runway_rhythms/
  config.py             airport catalogue (ICAO/IATA, timezone, coordinates, CAA spelling)
  download.py           idempotent downloaders for every source
  calendar_features.py  European vacation windows + public holidays
  pipeline.py           cleaning and aggregation into tidy parquet tables
tests/                  unit tests for cleaning, time zones, calendars and CAA aggregation
```

## Quick start

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -e ".[dev]"
python -m runway_rhythms.download                    # ~2.3 GB, mostly OpenSky
python -m runway_rhythms.pipeline                    # -> data/processed/*.parquet
pytest
```

## Data processing notes

* **Departure time:** the time OpenSky first sees the aircraft (`firstseen`), converted to the origin airport's local time. This is close to the actual take-off time, not the scheduled time.
* **Scope:** only airline call signs (3-letter ICAO prefix plus a flight number) flying between two in-scope airports are kept. General aviation and positioning flights are dropped.
* **Vacation:** shared European peaks (Christmas, February half-term, Easter, May half-term, July–August, October half-term) plus public holidays in either the origin or destination country. See `calendar_features.py`.
* **Punctuality (CAA):** on-time means the flight arrived or departed no more than 15 minutes after schedule. Figures are flight-weighted across all airlines on the route.
* **Weather delay (EUROCONTROL):** minutes of airport arrival ATFM delay coded **W** (weather) or **D** (de-icing).

## Data credits

* Matthias Schäfer, Martin Strohmeier, Vincent Lenders, Ivan Martinovic and Matthias Wilhelm. *Bringing Up OpenSky: A Large-scale ADS-B Sensor Network for Research*. IPSN 2014. Flight lists are used under OpenSky's non-commercial terms.
* Source: UK Civil Aviation Authority punctuality statistics.
* EUROCONTROL Aviation Intelligence Portal.
* Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).

Only derived aggregates are published in this repository. No raw source files are redistributed.
