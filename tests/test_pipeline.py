from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from runway_rhythms.calendar_features import add_calendar_features, season_label, vacation_windows
from runway_rhythms.config import AIRPORTS, ICAO_CODES
from runway_rhythms.pipeline import _read_caa, build_caa_routes, clean_flights, localise_departures


def test_airport_catalogue_is_consistent():
    assert len(ICAO_CODES) == len(AIRPORTS)
    assert all(len(a.icao) == 4 and len(a.iata) == 3 for a in AIRPORTS)
    assert len({a.caa_name for a in AIRPORTS}) == len(AIRPORTS)


@pytest.mark.parametrize(
    "d, expected",
    [
        (date(2022, 7, 15), "Summer"),
        (date(2022, 12, 25), "Christmas"),
        (date(2023, 1, 3), "Christmas"),
        (date(2022, 4, 17), "Easter"),  # Easter Sunday 2022
        (date(2022, 3, 9), None),
        (date(2022, 11, 16), None),
    ],
)
def test_season_label(d, expected):
    assert season_label(d) == expected


def test_half_term_weeks_are_monday_to_sunday():
    for start, end, label in vacation_windows(2026):
        if "half-term" in label:
            assert start.weekday() == 0 and end.weekday() == 6, label


def test_calendar_features_flags_public_holidays():
    df = pd.DataFrame({"date": ["2022-05-02", "2022-05-03"], "c1": ["GB", "GB"], "c2": ["FR", "FR"]})
    out = add_calendar_features(df, countries=("c1", "c2"))
    # 2 May 2022 is the UK Early May bank holiday; 3 May is an ordinary Tuesday
    assert out["public_holiday"].tolist() == [True, False]
    assert out["vacation"].tolist() == [True, False]
    assert out["dow"].tolist() == [0, 1]


def test_clean_flights_keeps_only_airline_flights_between_scope_airports():
    raw = pd.DataFrame(
        {
            "callsign": ["BAW304", "N123AB", "AFR1234", "DLH9", "EZY12AB"],
            "origin": ["EGLL", "EGLL", "LFPG", "EDDF", "EGKK"],
            "destination": ["LFPG", "LFPG", "LFPG", "KJFK", "LEPA"],
            "firstseen": ["2022-07-01 06:00:00+00:00"] * 5,
            "number": [None] * 5,
        }
    )
    kept = clean_flights(raw)
    assert kept["callsign"].tolist() == ["BAW304", "EZY12AB"]


def test_localise_departures_uses_origin_timezone():
    flights = pd.DataFrame(
        {
            "callsign": ["BAW1", "AFR1"],
            "origin": ["EGLL", "LFPG"],
            "destination": ["LFPG", "EGLL"],
            "firstseen": ["2022-07-01 23:30:00+00:00", "2022-01-10 06:15:00+00:00"],
        }
    )
    out = localise_departures(flights).set_index("origin")
    assert out.loc["EGLL", "hour"] == 0 and out.loc["EGLL", "date"] == pd.Timestamp("2022-07-02")  # BST
    assert out.loc["LFPG", "hour"] == 7  # CET


def test_caa_aggregation(tmp_path: Path):
    header = (
        "run_date,reporting_period,reporting_airport,origin_destination_country,origin_destination,airline_name,"
        "scheduled_charter,number_flights_matched,actual_flights_unmatched,number_flights_cancelled,"
        "flights_more_than_15_minutes_early_percent,flights_15_minutes_early_to_1_minute_early_percent,"
        "flights_0_to_15_minutes_late_percent,average_delay_mins\n"
    )
    rows = (
        "x,202407,HEATHROW,FRANCE,PARIS (CHARLES DE GAULLE),BRITISH AIRWAYS PLC,S,100,0,0,0,50,30,10\n"
        "x,202407,HEATHROW,FRANCE,PARIS (CHARLES DE GAULLE),AIR FRANCE,S,300,0,10,0,40,20,20\n"
        "x,202407,HEATHROW,USA,NEW YORK (JFK),BRITISH AIRWAYS PLC,S,500,0,0,0,40,20,20\n"
    )
    f = tmp_path / "caa_202407.csv"
    f.write_text(header + rows, encoding="utf-8")
    assert len(_read_caa(f, ["reporting_airport"])) == 3
    out = build_caa_routes([f]).set_index("group")
    allr = out.loc["All airlines"]
    assert allr["flights"] == 400
    assert allr["on_time_pct"] == pytest.approx((80 + 180) / 400 * 100)
    assert allr["avg_delay_min"] == pytest.approx((1000 + 6000) / 400)
    assert out.loc["British Airways", "on_time_pct"] == pytest.approx(80)
