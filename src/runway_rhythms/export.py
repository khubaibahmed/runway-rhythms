"""Train the models and export compact JSON for the static dashboard (docs/data)."""
from __future__ import annotations

import json
import logging
from datetime import date

import numpy as np
import pandas as pd

from . import delay_model, traffic_model
from .calendar_features import add_calendar_features, vacation_windows
from .config import AIRPORT_BY_ICAO, AIRPORTS, DOCS_DATA, FORECAST_YEAR, PROCESSED, TRAFFIC_YEAR

log = logging.getLogger(__name__)
PERIODS = ("all", "vac", "non")


def _r(a, nd=2):
    """Round a numpy array to nested lists with None for NaN."""
    a = np.round(np.asarray(a, dtype=float), nd)
    return [[None if np.isnan(v) else float(v) for v in row] for row in a]


def _dump(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def _period_mask(vac: pd.Series, period: str) -> pd.Series:
    return pd.Series(True, index=vac.index) if period == "all" else (vac if period == "vac" else ~vac)


# --------------------------------------------------------------------------- observed traffic heatmaps
def observed_heatmaps(route_hourly: pd.DataFrame) -> dict:
    """Average flights per calendar day (month x dow) and per weekday-hour (dow x hour)."""
    rh = route_hourly.copy()
    rh["o_country"] = rh["origin"].map(lambda c: AIRPORT_BY_ICAO[c].country)
    rh["d_country"] = rh["destination"].map(lambda c: AIRPORT_BY_ICAO[c].country)
    pairs = rh[["origin", "destination", "o_country", "d_country"]].drop_duplicates()

    days = pd.DataFrame({"date": pd.date_range(f"{TRAFFIC_YEAR}-01-01", f"{TRAFFIC_YEAR}-12-31")})
    cal = {}  # (o_country, d_country) -> calendar with vacation flag
    for oc, dc in pairs[["o_country", "d_country"]].drop_duplicates().itertuples(index=False):
        c = add_calendar_features(days.assign(o=oc, d=dc), countries=("o", "d"))
        cal[(oc, dc)] = c[["date", "dow", "month", "vacation"]]

    out: dict = {}
    for (o, d), g in rh.groupby(["origin", "destination"]):
        c = cal[(AIRPORT_BY_ICAO[o].country, AIRPORT_BY_ICAO[d].country)]
        g = g.merge(c, on="date", how="left")
        res = {"dh": {}, "md": {}, "flights": int(g["flights"].sum())}
        for p in PERIODS:
            cm = c[_period_mask(c["vacation"], p)]
            gm = g[_period_mask(g["vacation"].astype(bool), p)]
            n_dow = cm.groupby("dow").size().reindex(range(7)).to_numpy(dtype=float)
            dh = gm.pivot_table(index="dow", columns="hour", values="flights", aggfunc="sum").reindex(index=range(7), columns=range(24)).fillna(0)
            res["dh"][p] = dh.to_numpy() / n_dow[:, None]
            n_md = cm.groupby(["month", "dow"]).size().unstack().reindex(index=range(1, 13), columns=range(7)).to_numpy(dtype=float)
            md = gm.groupby(["month", "dow"])["flights"].sum().unstack().reindex(index=range(1, 13), columns=range(7)).fillna(0).to_numpy()
            with np.errstate(invalid="ignore", divide="ignore"):
                res["md"][p] = np.where(n_md > 0, md / n_md, np.nan)
        out.setdefault(o, {})[d] = res
    return out


# --------------------------------------------------------------------------- forecast heatmaps
def forecast_heatmaps(fc_daily: pd.DataFrame, profiles: pd.DataFrame) -> dict:
    prof = profiles.set_index(["origin", "destination", "vacation", "dow", "hour"])["share"]
    out: dict = {}
    for (o, d), g in fc_daily.groupby(["origin", "destination"]):
        res = {"dh": {}, "md": {}}
        p_route = prof.loc[(o, d)].unstack("hour").reindex(columns=range(24)).fillna(0)  # index (vacation, dow)
        for p in PERIODS:
            gm = g[_period_mask(g["vacation"].astype(bool), p)]
            md = gm.groupby(["month", "dow"])["pred"].mean().unstack().reindex(index=range(1, 13), columns=range(7))
            res["md"][p] = md.to_numpy()
            # expected flights in each weekday-hour = sum over days of pred x hour share / days
            dh = np.zeros((7, 24))
            n = gm.groupby("dow").size().reindex(range(7)).fillna(0).to_numpy()
            for (vac, dow), s in gm.groupby(["vacation", "dow"])["pred"].sum().items():
                dh[dow] += s * p_route.loc[(bool(vac), dow)].to_numpy()
            with np.errstate(invalid="ignore", divide="ignore"):
                res["dh"][p] = np.where(n[:, None] > 0, dh / n[:, None], np.nan)
        # uplift summary for KPI tiles
        vac = g[g["vacation"].astype(bool)]["pred"].mean()
        non = g[~g["vacation"].astype(bool)]["pred"].mean()
        res["kpi"] = {"vac_per_day": float(vac), "non_per_day": float(non), "uplift_pct": float(100 * (vac / non - 1)) if non > 0 else None}
        out.setdefault(o, {})[d] = res
    return out


def _sum_tables(tables: list[dict]) -> dict:
    res = {}
    for kind in ("dh", "md"):
        res[kind] = {}
        for p in PERIODS:
            stack = np.stack([t[kind][p] for t in tables])
            # a cell is missing only if missing everywhere
            res[kind][p] = np.where(np.all(np.isnan(stack), axis=0), np.nan, np.nansum(stack, axis=0))
    return res


def export_traffic(obs: dict, fc: dict) -> dict:
    index = {}
    for o, dests in obs.items():
        payload = {}
        for d, r in dests.items():
            entry = {"n": r["flights"], "obs": {k: {p: _r(r[k][p]) for p in PERIODS} for k in ("dh", "md")}}
            if d in fc.get(o, {}):
                f = fc[o][d]
                entry["fc"] = {k: {p: _r(f[k][p]) for p in PERIODS} for k in ("dh", "md")}
                entry["kpi"] = {k: (round(v, 2) if v is not None else None) for k, v in f["kpi"].items()}
            payload[d] = entry
        all_obs = _sum_tables([dests[d] for d in dests])
        all_entry = {"n": int(sum(r["flights"] for r in dests.values())), "obs": {k: {p: _r(all_obs[k][p]) for p in PERIODS} for k in ("dh", "md")}}
        fc_tabs = [fc[o][d] for d in fc.get(o, {})]
        if fc_tabs:
            all_fc = _sum_tables(fc_tabs)
            all_entry["fc"] = {k: {p: _r(all_fc[k][p]) for p in PERIODS} for k in ("dh", "md")}
            vac = sum(t["kpi"]["vac_per_day"] for t in fc_tabs)
            non = sum(t["kpi"]["non_per_day"] for t in fc_tabs)
            all_entry["kpi"] = {"vac_per_day": round(vac, 2), "non_per_day": round(non, 2), "uplift_pct": round(100 * (vac / non - 1), 2)}
        payload["ALL"] = all_entry
        _dump(payload, DOCS_DATA / "traffic" / f"{o}.json")
        index[o] = sorted(((d, r["flights"]) for d, r in dests.items()), key=lambda t: -t[1])
    return index


# --------------------------------------------------------------------------- punctuality (CAA)
def export_punctuality(caa: pd.DataFrame) -> None:
    out = {}
    caa = caa.copy()
    caa["is_vac_month"] = caa["month"].dt.month.isin([4, 7, 8, 12])
    for (uk, eu), g in caa.groupby(["uk", "eu"]):
        route = {}
        for grp, s in g.groupby("group"):
            s = s.sort_values("month")
            seasonal = s.groupby(s["month"].dt.month).apply(
                lambda x: np.average(x["on_time_pct"], weights=x["flights"])
            ).reindex(range(1, 13))
            w = lambda m: float(np.average(s.loc[m, "on_time_pct"], weights=s.loc[m, "flights"])) if m.any() else None
            route[grp] = {
                "m": s["month"].dt.strftime("%Y-%m").tolist(),
                "otp": np.round(s["on_time_pct"], 1).tolist(),
                "dly": np.round(s["avg_delay_min"], 1).tolist(),
                "cxl": np.round(s["cancel_pct"], 2).tolist(),
                "n": s["flights"].astype(int).tolist(),
                "seasonal": [None if np.isnan(v) else round(float(v), 1) for v in seasonal],
                "otp_vac": w(s["is_vac_month"]),
                "otp_non": w(~s["is_vac_month"]),
            }
        out[f"{uk}-{eu}"] = route
    _dump(out, DOCS_DATA / "punctuality.json")


# --------------------------------------------------------------------------- delays + weather
def export_delays(daily: pd.DataFrame) -> dict:
    df, apts, reg, clf, metrics, importance = delay_model.fit(daily)
    scen = delay_model.scenarios(df, apts, reg, clf)
    curves = delay_model.response_curves(df, apts, reg)
    bins = delay_model.observed_bins(df)

    per_apt = {}
    for icao, g in list(df.groupby("icao")) + [("ALL", df)]:
        md = g.pivot_table(index="month", columns="dow", values="delay_per_arrival", aggfunc="mean").reindex(index=range(1, 13), columns=range(7))
        wk = g.set_index("date").resample("W-MON")[["atfm_delay_min", "weather_delay_min", "arrivals"]].sum()
        entry = {
            "md": _r(md.to_numpy(), 3),
            "weekly": {
                "w": wk.index.strftime("%Y-%m-%d").tolist(),
                "total": np.round(wk["atfm_delay_min"] / wk["arrivals"].clip(lower=1), 3).tolist(),
                "weather": np.round(wk["weather_delay_min"] / wk["arrivals"].clip(lower=1), 3).tolist(),
            },
            "weather_share": round(float(g["weather_delay_min"].sum() / max(g["atfm_delay_min"].sum(), 1)), 4),
            "delay_vac": round(float(g.loc[g["vacation"] == 1, "delay_per_arrival"].mean()), 3),
            "delay_non": round(float(g.loc[g["vacation"] == 0, "delay_per_arrival"].mean()), 3),
            "disrupted_vac": round(float(g.loc[g["vacation"] == 1, "disrupted"].mean()), 4),
            "disrupted_non": round(float(g.loc[g["vacation"] == 0, "disrupted"].mean()), 4),
            "avg_delay": round(float(g["atfm_delay_min"].sum() / g["arrivals"].sum()), 3),
        }
        b = bins[bins["icao"] == icao]
        entry["bins"] = {
            v: {"band": s["band"].astype(str).tolist(), "delay": np.round(s["delay"].fillna(0), 3).tolist(),
                "days": s["days"].astype(int).tolist(), "disrupted": np.round(s["disrupted"].fillna(0), 4).tolist()}
            for v, s in b.groupby("var")
        }
        if icao != "ALL":
            c = curves[curves["icao"] == icao]
            entry["curves"] = {v: {"x": s["x"].tolist(), "y": np.round(s["y"], 3).tolist()} for v, s in c.groupby("var")}
            sc = scen[scen["icao"] == icao]
            entry["scenarios"] = {
                "name": sc["scenario"].tolist(),
                "delay": np.round(sc["delay_per_arrival"], 3).tolist(),
                "p": np.round(sc["p_disrupted"], 3).tolist(),
            }
        per_apt[icao] = entry
    _dump(per_apt, DOCS_DATA / "delays.json")
    return {"metrics": metrics, "importance": importance[["label", "importance"]].round(4).to_dict("records")}


# --------------------------------------------------------------------------- entry point
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    route_hourly = pd.read_parquet(PROCESSED / "route_hourly.parquet")
    route_hourly["date"] = pd.to_datetime(route_hourly["date"])

    log.info("observed heatmaps")
    obs = observed_heatmaps(route_hourly)
    log.info("traffic model")
    dense = traffic_model.daily_route_counts(route_hourly)
    tm = traffic_model.fit(dense)
    fc_daily = traffic_model.forecast_daily(tm)
    profiles = traffic_model.hour_profiles(route_hourly)
    fc = forecast_heatmaps(fc_daily, profiles)
    route_index = export_traffic(obs, fc)

    log.info("punctuality")
    export_punctuality(pd.read_parquet(PROCESSED / "caa_routes.parquet"))

    log.info("delay model")
    delay_summary = export_delays(pd.read_parquet(PROCESSED / "airport_daily.parquet"))

    windows = {
        str(y): [{"start": s.isoformat(), "end": e.isoformat(), "label": l} for s, e, l in vacation_windows(y)]
        for y in (TRAFFIC_YEAR, FORECAST_YEAR)
    }
    meta = {
        "generated": date.today().isoformat(),
        "traffic_year": TRAFFIC_YEAR,
        "forecast_year": FORECAST_YEAR,
        "airports": [
            {"icao": a.icao, "iata": a.iata, "name": a.name, "city": a.city, "country": a.country, "lat": a.lat, "lon": a.lon}
            for a in AIRPORTS
        ],
        "routes": route_index,
        "vacation_windows": windows,
        "traffic_metrics": tm.metrics,
        "delay_metrics": delay_summary["metrics"],
        "delay_importance": delay_summary["importance"],
    }
    _dump(meta, DOCS_DATA / "meta.json")
    log.info("export complete -> %s", DOCS_DATA)


if __name__ == "__main__":
    main()
