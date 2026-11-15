import numpy as np
import pandas as pd

from runway_rhythms import delay_model, traffic_model
from runway_rhythms.export import _r, _sum_tables, forecast_heatmaps


def _synthetic_route_hourly(n_days=60):
    rng = np.random.default_rng(0)
    rows = []
    for day in pd.date_range("2022-06-01", periods=n_days):
        for hour in (7, 12, 18):
            rows.append(("EGLL", "LFPG", day, hour, int(rng.integers(1, 4))))
    return pd.DataFrame(rows, columns=["origin", "destination", "date", "hour", "flights"])


def test_hour_profiles_sum_to_one_per_cell():
    prof = traffic_model.hour_profiles(_synthetic_route_hourly())
    route = prof[(prof.origin == "EGLL") & (prof.destination == "LFPG")]
    sums = route.groupby(["vacation", "dow"])["share"].sum()
    assert np.allclose(sums.to_numpy(), 1.0)
    # never-flown hours carry no weight
    assert route.loc[route.hour == 3, "share"].max() == 0


def test_forecast_heatmaps_shapes_and_totals():
    profiles = traffic_model.hour_profiles(_synthetic_route_hourly())
    dates = pd.date_range("2026-01-01", "2026-12-31")
    fc = pd.DataFrame({
        "origin": "EGLL", "destination": "LFPG", "date": dates, "dow": dates.day_of_week,
        "month": dates.month, "vacation": dates.month.isin([7, 8]), "pred": 10.0,
    })
    out = forecast_heatmaps(fc, profiles)["EGLL"]["LFPG"]
    assert out["dh"]["all"].shape == (7, 24) and out["md"]["vac"].shape == (12, 7)
    # hourly cells of a weekday add back to the daily forecast
    assert np.allclose(np.nansum(out["dh"]["all"], axis=1), 10.0)
    # no vacation days in March -> missing, not zero
    assert np.isnan(out["md"]["vac"][2]).all()
    assert out["kpi"]["uplift_pct"] == 0


def test_sum_tables_keeps_all_missing_cells_missing():
    a = {"dh": {p: np.array([[1.0, np.nan]]) for p in ("all", "vac", "non")}, "md": {p: np.array([[np.nan]]) for p in ("all", "vac", "non")}}
    b = {"dh": {p: np.array([[2.0, np.nan]]) for p in ("all", "vac", "non")}, "md": {p: np.array([[np.nan]]) for p in ("all", "vac", "non")}}
    s = _sum_tables([a, b])
    assert s["dh"]["all"][0, 0] == 3.0 and np.isnan(s["dh"]["all"][0, 1])
    assert _r(s["md"]["all"]) == [[None]]


def test_delay_prepare_flags():
    daily = pd.DataFrame({
        "icao": ["EGLL"] * 3, "date": pd.to_datetime(["2023-07-03", "2023-11-14", "2023-11-15"]),
        "arrivals": [600, 600, 300], "atfm_delay_min": [0, 1200, 0], "weather_delay_min": [0, 900, 0],
        "flights_delayed": [0, 80, 0], "flights_delayed_15": [0, 40, 0],
        "delay_per_arrival": [0, 2.0, 0], "share_delayed_15": [0, 40 / 600, 0], "weather_delay_per_arrival": [0, 1.5, 0],
        "temp_max": [25, 10, 9], "temp_min": [15, 4, 3], "precip_mm": [0, 12, 0], "snow_cm": [0, 0, None],
        "wind_kmh": [10, 40, 12], "gust_kmh": [20, 75, 25], "weather_code": [1, 96, 3],
    })
    df, apts = delay_model.prepare(daily)
    assert df["disrupted"].tolist() == [0, 1, 0]
    assert df["storm"].tolist() == [0, 1, 0]
    assert df["vacation"].tolist() == [1, 0, 0]  # 3 July is in the summer window
    assert list(apts) == ["EGLL"]
