"""Route-level traffic forecasting for vacation and non-vacation periods.

Approach
--------
1. Daily flights per route (2022) are modelled with a Poisson gradient-boosted
   tree. Features: route, weekday, month, vacation window and public holidays.
2. 2022 was still a recovery year, so forecasts are recalibrated to post-recovery
   levels with a per-airport, per-month ratio of EUROCONTROL arrivals
   (2024 / 2022).
3. Hour-of-day forecasts are the predicted daily flights multiplied by the route's
   departure-hour profile for that weekday and period type. The profile is smoothed
   towards the route's overall profile when data is sparse.

Validation holds out every fourth ISO week, which keeps the full seasonal cycle in
training. The model is compared with a seasonal-naive baseline (route x weekday x
month mean).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .calendar_features import add_calendar_features
from .config import AIRPORT_BY_ICAO, FORECAST_YEAR, PROCESSED, RAW, TRAFFIC_YEAR

log = logging.getLogger(__name__)

MIN_ROUTE_FLIGHTS = 150  # routes below this yearly volume are shown but not forecast
# target encodings: log mean daily flights per route, route x weekday and route x month
ENCODINGS = {"route_level": ["route"], "lvl_rd": ["route", "dow"], "lvl_rm": ["route", "month"]}
FEATURES = ["o_code", "d_code", *ENCODINGS, "dow", "month", "vacation", "public_holiday", "season_code"]
AIRPORT_CODES = sorted(AIRPORT_BY_ICAO)
SEASONS = ["Regular", "Christmas", "February half-term", "Easter", "May half-term", "Summer", "October half-term"]


@dataclass
class TrafficModel:
    model: HistGradientBoostingRegressor
    routes: pd.Index  # route strings "ORIG-DEST"
    encodings: dict  # name -> Series indexed by the encoding keys
    metrics: dict


def _with_country(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["o_country"] = df["origin"].map(lambda c: AIRPORT_BY_ICAO[c].country)
    df["d_country"] = df["destination"].map(lambda c: AIRPORT_BY_ICAO[c].country)
    return df


def daily_route_counts(route_hourly: pd.DataFrame) -> pd.DataFrame:
    """Dense route x day table (zeros included) for routes above the volume floor."""
    daily = route_hourly.groupby(["origin", "destination", "date"], as_index=False)["flights"].sum()
    volume = daily.groupby(["origin", "destination"])["flights"].sum()
    keep = volume[volume >= MIN_ROUTE_FLIGHTS].index
    dates = pd.date_range(f"{TRAFFIC_YEAR}-01-01", f"{TRAFFIC_YEAR}-12-31", freq="D")
    grid = pd.MultiIndex.from_tuples(
        [(o, d, t) for o, d in keep for t in dates], names=["origin", "destination", "date"]
    )
    dense = daily.set_index(["origin", "destination", "date"])["flights"].reindex(grid, fill_value=0).reset_index()
    return dense


def _design(df: pd.DataFrame) -> pd.DataFrame:
    df = add_calendar_features(_with_country(df), countries=("o_country", "d_country"))
    df["route"] = df["origin"] + "-" + df["destination"]
    df["o_code"] = df["origin"].map(AIRPORT_CODES.index)
    df["d_code"] = df["destination"].map(AIRPORT_CODES.index)
    df["season_code"] = df["season"].map({s: i for i, s in enumerate(SEASONS)}).fillna(0).astype(int)
    df["vacation"] = df["vacation"].astype(int)
    df["public_holiday"] = df["public_holiday"].astype(int)
    return df


def _new_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="poisson",
        max_iter=400,
        learning_rate=0.08,
        max_leaf_nodes=63,
        min_samples_leaf=20,
        categorical_features=[0, 1, 9],  # origin, destination, season
        random_state=7,
    )


def fit(dense: pd.DataFrame) -> TrafficModel:
    routes = pd.Index(sorted((dense["origin"] + "-" + dense["destination"]).unique()))
    df = _design(dense)
    week = df["date"].dt.isocalendar().week.astype(int)
    test = (week % 4) == 0

    # route volume is learnt from training weeks only, so the holdout stays unseen
    _apply_encodings(df, _encodings(df.loc[~test]))
    model = _new_model().fit(df.loc[~test, FEATURES], df.loc[~test, "flights"])
    pred = model.predict(df.loc[test, FEATURES])
    truth = df.loc[test, "flights"].to_numpy()

    keys = ["route", "dow", "month"]
    naive = df.loc[~test].groupby(keys)["flights"].mean().rename("naive")
    base = df.loc[test, keys].join(naive, on=keys)["naive"].fillna(0).to_numpy()

    def wape(y, p):
        return float(np.abs(y - p).sum() / max(y.sum(), 1))

    # weekly route totals: the level the heatmaps are read at
    wk = df.loc[test, ["route"]].assign(week=week[test], y=truth, p=pred, b=base)
    wk = wk.groupby(["route", "week"])[["y", "p", "b"]].sum()
    metrics = {
        "validation": "every 4th ISO week of 2022 held out",
        "routes": int(len(routes)),
        "train_rows": int((~test).sum()),
        "test_rows": int(test.sum()),
        "daily_mae": round(float(mean_absolute_error(truth, pred)), 3),
        "daily_mae_baseline": round(float(mean_absolute_error(truth, base)), 3),
        "daily_wape": round(wape(truth, pred), 4),
        "daily_wape_baseline": round(wape(truth, base), 4),
        "weekly_wape": round(wape(wk["y"].to_numpy(), wk["p"].to_numpy()), 4),
        "weekly_wape_baseline": round(wape(wk["y"].to_numpy(), wk["b"].to_numpy()), 4),
    }
    vac = df.loc[test, "vacation"].to_numpy() == 1
    metrics["vacation_mae"] = round(float(mean_absolute_error(truth[vac], pred[vac])), 3)
    metrics["vacation_mae_baseline"] = round(float(mean_absolute_error(truth[vac], base[vac])), 3)
    log.info("traffic model metrics: %s", metrics)
    enc = _encodings(df)
    _apply_encodings(df, enc)
    final = _new_model().fit(df[FEATURES], df["flights"])
    return TrafficModel(final, routes, enc, metrics)


def _encodings(df: pd.DataFrame) -> dict:
    return {name: np.log1p(df.groupby(keys)["flights"].mean()) for name, keys in ENCODINGS.items()}


def _apply_encodings(df: pd.DataFrame, enc: dict) -> None:
    for name, keys in ENCODINGS.items():
        df[name] = df[keys].join(enc[name].rename("_v"), on=keys)["_v"].to_numpy()


def recovery_factors() -> pd.DataFrame:
    """Per airport and month: EUROCONTROL arrivals in 2024 divided by arrivals in 2022."""
    from .pipeline import load_eurocontrol

    files = [RAW / "eurocontrol" / f"apt_dly_{y}.csv.bz2" for y in (TRAFFIC_YEAR, 2024)]
    d = load_eurocontrol(files)
    d["year"] = d["date"].dt.year
    d["month"] = d["date"].dt.month
    m = d.groupby(["icao", "year", "month"])["arrivals"].mean().unstack("year")
    ratio = (m[2024] / m[TRAFFIC_YEAR]).clip(0.6, 2.5).fillna(1.0)
    return ratio.rename("factor").reset_index()


def forecast_daily(tm: TrafficModel, year: int = FORECAST_YEAR) -> pd.DataFrame:
    dates = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    od = pd.DataFrame([r.split("-") for r in tm.routes], columns=["origin", "destination"])
    grid = od.merge(pd.DataFrame({"date": dates}), how="cross")
    df = _design(grid)
    _apply_encodings(df, tm.encodings)
    df["pred"] = tm.model.predict(df[FEATURES])
    factors = recovery_factors()
    # traffic between two airports is limited by both ends: average the two ratios
    f_o = df[["origin", "month"]].merge(factors, left_on=["origin", "month"], right_on=["icao", "month"], how="left")["factor"]
    f_d = df[["destination", "month"]].merge(factors, left_on=["destination", "month"], right_on=["icao", "month"], how="left")["factor"]
    df["pred"] = df["pred"] * ((f_o.fillna(1.0).to_numpy() + f_d.fillna(1.0).to_numpy()) / 2)
    return df[["origin", "destination", "date", "dow", "month", "vacation", "season", "pred"]]


def hour_profiles(route_hourly: pd.DataFrame, shrink: float = 30.0) -> pd.DataFrame:
    """Share of a route's departures in each local hour, per weekday and vacation flag.

    Shares are shrunk towards the route's overall hour profile with a pseudo-count of
    ``shrink`` flights, which stabilises thin weekday/period cells.
    """
    rh = add_calendar_features(_with_country(route_hourly), countries=("o_country", "d_country"))
    overall = rh.groupby(["origin", "destination", "hour"])["flights"].sum()
    overall = overall / overall.groupby(level=[0, 1]).transform("sum")
    cell = rh.groupby(["origin", "destination", "vacation", "dow", "hour"])["flights"].sum()
    tot = cell.groupby(level=[0, 1, 2, 3]).transform("sum")
    idx = cell.index
    prior = overall.reindex(pd.MultiIndex.from_arrays([idx.get_level_values(0), idx.get_level_values(1), idx.get_level_values(4)])).to_numpy()
    full = pd.MultiIndex.from_product(
        [sorted(rh["origin"].unique()), sorted(rh["destination"].unique()), [False, True], range(7), range(24)],
        names=["origin", "destination", "vacation", "dow", "hour"],
    )
    share = pd.Series((cell.to_numpy() + shrink * np.nan_to_num(prior)) / (tot.to_numpy() + shrink), index=idx)
    share = share.reindex(full)
    # cells never observed fall back to the overall profile
    fallback = overall.reindex(
        pd.MultiIndex.from_arrays([full.get_level_values(0), full.get_level_values(1), full.get_level_values(4)])
    ).to_numpy()
    share = share.fillna(pd.Series(fallback, index=full)).fillna(0.0)
    return share.rename("share").reset_index()
