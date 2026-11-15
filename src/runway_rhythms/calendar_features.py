"""Vacation calendar and date features.

Europe has no single school-holiday calendar, so "vacation" is defined as the
union of the travel peaks shared by most of the countries in scope, plus each
country's own public holidays:

* Winter / Christmas break   20 Dec - 6 Jan
* February half-term week    the week containing 15 Feb (Mon-Sun)
* Easter break               Good Friday - 9 days ... Easter Monday + 7 days
* Whitsun / May half-term    the last full week of May (Mon-Sun)
* Summer holidays            1 Jul - 31 Aug
* October half-term          the last full week of October (Mon-Sun)

A route-day counts as vacation if the date falls in one of these windows or is
a public holiday in the origin or destination country.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays
import numpy as np
import pandas as pd
from dateutil.easter import easter


def _week_containing(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _last_full_week(year: int, month: int) -> tuple[date, date]:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last_sunday = nxt - timedelta(days=1)
    while last_sunday.weekday() != 6:
        last_sunday -= timedelta(days=1)
    return last_sunday - timedelta(days=6), last_sunday


@lru_cache(maxsize=None)
def vacation_windows(year: int) -> tuple[tuple[date, date, str], ...]:
    """Return the shared European vacation windows touching ``year``."""
    e = easter(year)
    feb = _week_containing(date(year, 2, 15))
    may = _last_full_week(year, 5)
    octo = _last_full_week(year, 10)
    return (
        (date(year - 1, 12, 20), date(year, 1, 6), "Christmas"),
        (feb[0], feb[1], "February half-term"),
        (e - timedelta(days=11), e + timedelta(days=8), "Easter"),
        (may[0], may[1], "May half-term"),
        (date(year, 7, 1), date(year, 8, 31), "Summer"),
        (octo[0], octo[1], "October half-term"),
        (date(year, 12, 20), date(year + 1, 1, 6), "Christmas"),
    )


def season_label(d: date) -> str | None:
    for start, end, label in vacation_windows(d.year):
        if start <= d <= end:
            return label
    return None


@lru_cache(maxsize=None)
def _public_holidays(country: str, year: int) -> frozenset[date]:
    return frozenset(holidays.country_holidays(country, years=year).keys())


def is_public_holiday(d: date, country: str) -> bool:
    return d in _public_holidays(country, d.year)


def add_calendar_features(
    df: pd.DataFrame, date_col: str = "date", countries: tuple[str, ...] = ()
) -> pd.DataFrame:
    """Add dow, month, day-of-year, vacation and public-holiday columns.

    ``countries`` names columns holding ISO country codes; a public holiday in any
    of them sets ``public_holiday``.
    """
    out = df.copy()
    dates = pd.to_datetime(out[date_col])
    out["dow"] = dates.dt.day_of_week
    out["month"] = dates.dt.month
    out["doy"] = dates.dt.day_of_year
    py_dates = dates.dt.date
    unique = pd.Series(py_dates.unique())
    season = {d: season_label(d) for d in unique}
    out["season"] = py_dates.map(season).fillna("Regular")
    holiday = np.zeros(len(out), dtype=bool)
    for col in countries:
        pairs = pd.Series(list(zip(py_dates, out[col])))
        lookup = {p: is_public_holiday(p[0], p[1]) for p in pairs.unique()}
        holiday |= pairs.map(lookup).to_numpy(dtype=bool)
    out["public_holiday"] = holiday
    out["vacation"] = (out["season"] != "Regular") | out["public_holiday"]
    return out
