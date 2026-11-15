# ✈️ Runway Rhythms

**When does Europe fly, and what makes it late?**

### 👉 [Open the live dashboard](https://khubaibahmed.github.io/runway-rhythms/)

Pick any two of 30 airports to see:
1. **Traffic rhythm heatmaps:** weekday × hour of day, or month × weekday. View them observed (2022) or forecast (2026), for the whole year, vacation days, non-vacation days, or the difference between the two.
2. **Vacation forecast:** hourly and monthly profiles of a vacation day against a regular day.
3. **Punctuality:** UK CAA on-time performance for the route (British Airways vs other airlines), plus a month × weekday heatmap of arrival delay at the destination.
4. **Weather impact:** observed delay by wind and rain bands, model response curves, what-if scenarios, and each airport's weather share of delay.

Runway Rhythms is an operations-analytics project on European air traffic. It covers 30 major UK and EU airports and joins three public data sources:

| Question | Source | Granularity |
|---|---|---|
| When do flights depart on a route (hour × weekday, month × weekday)? | [OpenSky Network](https://opensky-network.org) flight lists, 2022 | one row per flight |
| How punctual is a UK ↔ EU route, and how does British Airways compare? | [UK CAA punctuality statistics](https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/), 2022–2025 | route × airline × month |
| How much does weather drive arrival delays? | [EUROCONTROL airport arrival ATFM delay](https://www.ansperformance.eu/reference/dataset/airport-arrival-atfm-delay/) + [Open-Meteo](https://open-meteo.com) history, 2022–2024 | airport × day |

## Headline findings

* **Vacation reshapes leisure routes, not hubs.** Forecast 2026 vacation days run **+71 %** on Amsterdam → Palma, **+52 %** on Manchester → Palma and **+40 %** on Gatwick → Palma. Business routes like Heathrow → Paris CDG move by only **−1.5 %**.
* **Weather causes about half of all airport arrival delay.** Across the 30 airports, **48 %** of ATFM arrival-delay minutes in 2022–2024 were coded weather or de-icing. At Stockholm, Munich and Heathrow the share is above **85 %**.
* **Wind and rain scale delay sharply.** Days with gusts of 75 km/h or more average **2.9 min** of ATFM delay per arrival, against **0.7 min** under 30 km/h (3.9×). Days with 10 mm+ of rain average **1.9 min**, against **0.8 min** on dry days.
* **Vacation days are less punctual.** ATFM delay per arrival is **15 % higher** on vacation days (1.03 vs 0.89 min), and disrupted days rise from **11.7 %** to **14.0 %**.

## Models

| Model | Target | Validation | Result |
|---|---|---|---|
| Traffic forecaster (Poisson gradient-boosted trees) | daily flights per route, 812 routes | every 4th ISO week of 2022 held out | weekly WAPE **10.0 %** vs 10.2 % for the route × weekday × month baseline |
| Delay regressor (Poisson gradient-boosted trees) | ATFM delay min per arrival | train 2022–23, test 2024 (out-of-time) | MAE **1.12** vs 1.17 baseline |
| Disruption classifier (gradient-boosted trees) | > 5 % of arrivals delayed 15+ min | train 2022–23, test 2024 | ROC-AUC **0.83**, recall **0.72**, precision **0.39** |

The traffic model matches a strong seasonal baseline within the training year, because airline schedules are highly regular. What it adds is the ability to move vacation windows and public holidays to the forecast year's dates. Forecasts are recalibrated from 2022 recovery levels to 2024 levels using EUROCONTROL arrival counts at both ends of each route.

The disruption classifier is tuned for recall (class-balanced). Missing a disrupted day costs an operations team more than a false alarm does.

## Project layout

```
src/runway_rhythms/
  config.py             airport catalogue (ICAO/IATA, timezone, coordinates, CAA spelling)
  download.py           idempotent downloaders for every source
  calendar_features.py  European vacation windows + public holidays
  pipeline.py           cleaning and aggregation into tidy parquet tables
  traffic_model.py      route-level vacation / non-vacation traffic forecaster
  delay_model.py        weather -> arrival delay regressor, disruption classifier, scenarios
  export.py             trains models and writes compact JSON for the dashboard
tests/                  unit tests for cleaning, time zones, calendars, models and exports
docs/data/              exported JSON consumed by the dashboard
```

## Quick start

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -e ".[dev]"
python -m runway_rhythms.download                    # ~2.3 GB, mostly OpenSky
python -m runway_rhythms.pipeline                    # -> data/processed/*.parquet
python -m runway_rhythms.export                      # train models -> docs/data/*.json
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
