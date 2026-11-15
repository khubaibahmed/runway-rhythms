"""How weather drives airport arrival delays.

Two models are trained on airport-days for 2022-2023 and tested on 2024
(an out-of-time test):

* **Regressor:** ATFM arrival delay minutes per arrival (Poisson gradient-boosted trees).
* **Classifier:** "disrupted day", meaning more than 5 % of arrivals were delayed
  over 15 minutes by ATFM regulations. Reported with precision, recall and ROC-AUC.

Features are daily weather at the airport, traffic level, weekday, month and the
vacation flag. The airport is a categorical feature, so the model learns how
sensitive each hub is to weather.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    mean_absolute_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)

from .calendar_features import add_calendar_features
from .config import AIRPORT_BY_ICAO

log = logging.getLogger(__name__)

DISRUPTED_SHARE = 0.05
WEATHER = ["gust_kmh", "wind_kmh", "precip_mm", "snow_cm", "temp_min", "temp_max", "storm"]
FEATURES = ["apt_code"] + WEATHER + ["traffic_index", "dow", "month", "vacation"]
FEATURE_LABELS = {
    "apt_code": "Airport",
    "gust_kmh": "Wind gusts",
    "wind_kmh": "Sustained wind",
    "precip_mm": "Precipitation",
    "snow_cm": "Snowfall",
    "temp_min": "Min temperature",
    "temp_max": "Max temperature",
    "storm": "Thunderstorm",
    "traffic_index": "Traffic vs normal",
    "dow": "Weekday",
    "month": "Month",
    "vacation": "Vacation period",
}


def prepare(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.Index]:
    df = daily.dropna(subset=["gust_kmh", "precip_mm"]).copy()
    df["country"] = df["icao"].map(lambda c: AIRPORT_BY_ICAO[c].country)
    df = add_calendar_features(df, countries=("country",))
    df["vacation"] = df["vacation"].astype(int)
    df["storm"] = df["weather_code"].fillna(0).ge(95).astype(int)  # WMO 95-99 thunderstorm
    df["snow_cm"] = df["snow_cm"].fillna(0)
    norm = df.groupby(["icao", "dow"])["arrivals"].transform("median")
    df["traffic_index"] = df["arrivals"] / norm
    apts = pd.Index(sorted(df["icao"].unique()))
    df["apt_code"] = apts.get_indexer(df["icao"])
    df["disrupted"] = (df["share_delayed_15"] > DISRUPTED_SHARE).astype(int)
    return df, apts


def _reg() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="poisson", max_iter=500, learning_rate=0.05, max_leaf_nodes=31,
        min_samples_leaf=30, l2_regularization=1.0, categorical_features=[0], random_state=7,
    )


def _clf() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=30,
        l2_regularization=1.0, categorical_features=[0], class_weight="balanced", random_state=7,
    )


def fit(daily: pd.DataFrame):
    df, apts = prepare(daily)
    train = df["date"].dt.year < 2024
    Xtr, Xte = df.loc[train, FEATURES], df.loc[~train, FEATURES]
    ytr, yte = df.loc[train, "delay_per_arrival"], df.loc[~train, "delay_per_arrival"]

    reg = _reg().fit(Xtr, ytr)
    p = reg.predict(Xte)
    clf = _clf().fit(Xtr, df.loc[train, "disrupted"])
    proba = clf.predict_proba(Xte)[:, 1]
    ytrue = df.loc[~train, "disrupted"]
    yhat = (proba >= 0.5).astype(int)

    naive = df.loc[train].groupby(["icao", "month"])["delay_per_arrival"].mean()
    base = df.loc[~train, ["icao", "month"]].join(naive, on=["icao", "month"])["delay_per_arrival"].fillna(0)

    imp = permutation_importance(reg, Xte, yte, n_repeats=5, random_state=7, scoring="neg_mean_absolute_error")
    importance = (
        pd.DataFrame({"feature": FEATURES, "importance": imp.importances_mean})
        .assign(label=lambda d: d["feature"].map(FEATURE_LABELS))
        .sort_values("importance", ascending=False)
    )
    metrics = {
        "train": "2022-2023 airport-days",
        "test": "2024 airport-days (out-of-time)",
        "train_rows": int(train.sum()),
        "test_rows": int((~train).sum()),
        "delay_mae": round(float(mean_absolute_error(yte, p)), 3),
        "delay_mae_baseline": round(float(mean_absolute_error(yte, base)), 3),
        "delay_r2": round(float(r2_score(yte, p)), 3),
        "disrupted_rate_test": round(float(ytrue.mean()), 4),
        "disrupted_precision": round(float(precision_score(ytrue, yhat, zero_division=0)), 3),
        "disrupted_recall": round(float(recall_score(ytrue, yhat, zero_division=0)), 3),
        "disrupted_roc_auc": round(float(roc_auc_score(ytrue, proba)), 3),
        "disrupted_definition": f"> {int(DISRUPTED_SHARE * 100)}% of arrivals delayed 15+ min by ATFM",
    }
    log.info("delay model metrics: %s", metrics)
    # refit on all years for scenario analysis
    reg_all = _reg().fit(df[FEATURES], df["delay_per_arrival"])
    clf_all = _clf().fit(df[FEATURES], df["disrupted"])
    return df, apts, reg_all, clf_all, metrics, importance


def typical_day(df: pd.DataFrame, icao: str) -> dict:
    a = df[df["icao"] == icao]
    return {
        "gust_kmh": float(a["gust_kmh"].median()),
        "wind_kmh": float(a["wind_kmh"].median()),
        "precip_mm": 0.0,
        "snow_cm": 0.0,
        "temp_min": float(a["temp_min"].median()),
        "temp_max": float(a["temp_max"].median()),
        "storm": 0,
        "traffic_index": 1.0,
        "dow": 2,
        "month": 6,
        "vacation": 0,
    }


SCENARIOS = {
    "Calm day": {},
    "Strong gusts (70 km/h)": {"gust_kmh": 70.0, "wind_kmh": 40.0},
    "Heavy rain (20 mm)": {"precip_mm": 20.0},
    "Thunderstorm": {"storm": 1, "precip_mm": 10.0, "gust_kmh": 55.0, "temp_max": 28.0, "month": 7},
    "Snow (5 cm, -3 °C)": {"snow_cm": 5.0, "temp_min": -3.0, "temp_max": 1.0, "month": 1},
    "Busy vacation day": {"traffic_index": 1.1, "vacation": 1, "month": 7},
}


def scenarios(df, apts, reg, clf) -> pd.DataFrame:
    rows = []
    for code, icao in enumerate(apts):
        base = typical_day(df, icao)
        for name, change in SCENARIOS.items():
            x = {**base, **change, "apt_code": code}
            X = pd.DataFrame([x])[FEATURES]
            rows.append({
                "icao": icao,
                "scenario": name,
                "delay_per_arrival": float(reg.predict(X)[0]),
                "p_disrupted": float(clf.predict_proba(X)[0, 1]),
            })
    return pd.DataFrame(rows)


CURVES = {
    "gust_kmh": np.arange(10, 101, 5),
    "precip_mm": np.array([0, 1, 2, 4, 6, 8, 10, 15, 20, 30, 40]),
    "snow_cm": np.array([0, 0.5, 1, 2, 3, 5, 8, 12]),
    "temp_max": np.arange(-5, 41, 3),
}


def response_curves(df, apts, reg) -> pd.DataFrame:
    """Predicted delay per arrival as one weather variable varies, all else typical."""
    rows = []
    for code, icao in enumerate(apts):
        base = typical_day(df, icao)
        for var, grid in CURVES.items():
            X = pd.DataFrame([{**base, "apt_code": code, var: float(v)} for v in grid])
            if var == "snow_cm":
                X["temp_min"], X["temp_max"], X["month"] = -3.0, 1.0, 1
            pred = reg.predict(X[FEATURES])
            rows += [{"icao": icao, "var": var, "x": float(v), "y": float(y)} for v, y in zip(grid, pred)]
    return pd.DataFrame(rows)


GUST_BINS = [0, 30, 45, 60, 75, 300]
GUST_LABELS = ["<30", "30–45", "45–60", "60–75", "75+"]
RAIN_BINS = [-0.1, 0.2, 2, 5, 10, 500]
RAIN_LABELS = ["Dry", "0.2–2", "2–5", "5–10", "10+"]


def observed_bins(df: pd.DataFrame) -> pd.DataFrame:
    """Empirical delay per arrival by gust and rain bands, per airport and network-wide."""
    d = df.assign(
        gust_band=pd.cut(df["gust_kmh"], GUST_BINS, labels=GUST_LABELS),
        rain_band=pd.cut(df["precip_mm"], RAIN_BINS, labels=RAIN_LABELS),
    )
    out = []
    for var in ("gust_band", "rain_band"):
        for icao, g in list(d.groupby("icao")) + [("ALL", d)]:
            s = g.groupby(var, observed=False).agg(
                days=("delay_per_arrival", "size"),
                delay=("delay_per_arrival", "mean"),
                disrupted=("disrupted", "mean"),
            ).reset_index().rename(columns={var: "band"})
            s["icao"], s["var"] = icao, var
            out.append(s)
    return pd.concat(out, ignore_index=True)
