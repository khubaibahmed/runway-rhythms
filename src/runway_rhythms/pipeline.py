"""Clean and aggregate raw sources into tidy parquet tables in ``data/processed``.

Outputs
-------
route_hourly.parquet   flights per (origin, destination, local date, local hour)
airport_daily.parquet  arrivals, ATFM delay and weather per (airport, date)
caa_routes.parquet     monthly punctuality per (UK airport, EU airport, airline group)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .config import AIRPORT_BY_ICAO, AIRPORTS, ICAO_CODES, PROCESSED, RAW

log = logging.getLogger(__name__)

AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}[0-9][0-9A-Z]{0,4}$")
OPENSKY_COLS = ["callsign", "origin", "destination", "firstseen"]


# --------------------------------------------------------------------------- traffic
def clean_flights(chunk: pd.DataFrame) -> pd.DataFrame:
    """Keep airline flights between two different in-scope airports."""
    chunk = chunk.dropna(subset=["origin", "destination", "firstseen"])
    mask = (
        chunk["origin"].isin(ICAO_CODES)
        & chunk["destination"].isin(ICAO_CODES)
        & (chunk["origin"] != chunk["destination"])
        & chunk["callsign"].fillna("").str.strip().str.match(AIRLINE_CALLSIGN)
    )
    return chunk.loc[mask, OPENSKY_COLS].copy()


def localise_departures(flights: pd.DataFrame) -> pd.DataFrame:
    """Convert UTC first-seen times to the origin airport's local date and hour."""
    utc = pd.to_datetime(flights["firstseen"], utc=True, format="mixed")
    parts = []
    for icao, idx in flights.groupby("origin").groups.items():
        local = utc.loc[idx].dt.tz_convert(AIRPORT_BY_ICAO[icao].tz)
        parts.append(
            pd.DataFrame(
                {
                    "origin": icao,
                    "destination": flights.loc[idx, "destination"].to_numpy(),
                    "date": local.dt.tz_localize(None).dt.normalize().to_numpy(),
                    "hour": local.dt.hour.to_numpy(),
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=["origin", "destination", "date", "hour"])
    return pd.concat(parts, ignore_index=True)


def build_route_hourly(files: list[Path] | None = None) -> pd.DataFrame:
    files = files or sorted((RAW / "opensky").glob("flightlist_*.csv.gz"))
    if not files:
        raise FileNotFoundError("no OpenSky files; run `python -m runway_rhythms.download opensky`")
    counts = []
    for path in map(Path, files):
        n_raw = n_kept = 0
        for chunk in pd.read_csv(path, usecols=OPENSKY_COLS, chunksize=1_000_000, dtype=str):
            n_raw += len(chunk)
            flights = clean_flights(chunk)
            n_kept += len(flights)
            local = localise_departures(flights)
            counts.append(local.groupby(["origin", "destination", "date", "hour"]).size().rename("flights"))
        log.info("%s: kept %s of %s flights", path.name, f"{n_kept:,}", f"{n_raw:,}")
    out = (
        pd.concat(counts)
        .groupby(level=[0, 1, 2, 3])
        .sum()
        .reset_index()
        .astype({"hour": "int8", "flights": "int32"})
    )
    return out


# --------------------------------------------------------------------------- delays + weather
WEATHER_CAUSES = ("W", "D")  # EUROCONTROL codes: W = weather, D = de-icing


def load_eurocontrol(files: list[Path] | None = None) -> pd.DataFrame:
    files = files or sorted((RAW / "eurocontrol").glob("apt_dly_*.csv.bz2"))
    df = pd.concat((pd.read_csv(f, compression="bz2") for f in files), ignore_index=True)
    df = df[df["APT_ICAO"].isin(ICAO_CODES)].copy()
    cause_cols = [c for c in df.columns if re.fullmatch(r"DLY_APT_ARR_[A-Z]{1,2}_1", c)]
    df[cause_cols + ["DLY_APT_ARR_1", "FLT_ARR_1_DLY", "FLT_ARR_1_DLY_15"]] = df[
        cause_cols + ["DLY_APT_ARR_1", "FLT_ARR_1_DLY", "FLT_ARR_1_DLY_15"]
    ].fillna(0)
    weather_cols = [f"DLY_APT_ARR_{c}_1" for c in WEATHER_CAUSES if f"DLY_APT_ARR_{c}_1" in df]
    out = pd.DataFrame(
        {
            "icao": df["APT_ICAO"],
            "date": pd.to_datetime(df["FLT_DATE"]).dt.tz_localize(None).dt.normalize(),
            "arrivals": df["FLT_ARR_1"].astype(float),
            "atfm_delay_min": df["DLY_APT_ARR_1"].astype(float),
            "weather_delay_min": df[weather_cols].sum(axis=1).astype(float),
            "flights_delayed": df["FLT_ARR_1_DLY"].astype(float),
            "flights_delayed_15": df["FLT_ARR_1_DLY_15"].astype(float),
        }
    )
    out = out[out["arrivals"] > 0]
    out["delay_per_arrival"] = out["atfm_delay_min"] / out["arrivals"]
    out["share_delayed_15"] = out["flights_delayed_15"] / out["arrivals"]
    out["weather_delay_per_arrival"] = out["weather_delay_min"] / out["arrivals"]
    return out.drop_duplicates(["icao", "date"]).reset_index(drop=True)


def load_weather(files: list[Path] | None = None) -> pd.DataFrame:
    files = files or sorted((RAW / "weather").glob("*.json"))
    frames = []
    for f in files:
        payload = json.loads(Path(f).read_text())
        daily = pd.DataFrame(payload["daily"]).rename(columns={"time": "date"})
        daily["icao"] = Path(f).name.split("_")[0]
        frames.append(daily)
    w = pd.concat(frames, ignore_index=True)
    w["date"] = pd.to_datetime(w["date"])
    return w.rename(
        columns={
            "temperature_2m_max": "temp_max",
            "temperature_2m_min": "temp_min",
            "precipitation_sum": "precip_mm",
            "snowfall_sum": "snow_cm",
            "wind_speed_10m_max": "wind_kmh",
            "wind_gusts_10m_max": "gust_kmh",
        }
    )


def build_airport_daily() -> pd.DataFrame:
    delays = load_eurocontrol()
    weather = load_weather()
    df = delays.merge(weather, on=["icao", "date"], how="inner")
    missing = sorted(ICAO_CODES - set(df["icao"]))
    if missing:
        log.warning("no delay/weather overlap for %s", missing)
    return df


# --------------------------------------------------------------------------- CAA punctuality
def _read_caa(path: Path, cols: list[str]) -> pd.DataFrame:
    """CAA files mix UTF-8 and Windows-1252 encodings across months."""
    try:
        return pd.read_csv(path, encoding="utf-8-sig", usecols=cols)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp1252", usecols=cols)


def build_caa_routes(files: list[Path] | None = None) -> pd.DataFrame:
    files = files or sorted((RAW / "caa").glob("caa_*.csv"))
    caa_to_icao = {a.caa_name: a.icao for a in AIRPORTS if a.caa_name}
    cols = [
        "reporting_period",
        "reporting_airport",
        "origin_destination",
        "airline_name",
        "number_flights_matched",
        "number_flights_cancelled",
        "flights_more_than_15_minutes_early_percent",
        "flights_15_minutes_early_to_1_minute_early_percent",
        "flights_0_to_15_minutes_late_percent",
        "average_delay_mins",
    ]
    df = pd.concat((_read_caa(f, cols) for f in files), ignore_index=True)
    df["reporting_airport"] = df["reporting_airport"].str.strip().str.upper()
    df["origin_destination"] = df["origin_destination"].str.strip().str.upper()
    df["uk"] = df["reporting_airport"].map(caa_to_icao)
    df["eu"] = df["origin_destination"].map(caa_to_icao)
    df = df.dropna(subset=["uk", "eu"])
    df = df[df["uk"] != df["eu"]]
    df["on_time_flights"] = df["number_flights_matched"] * (
        df["flights_more_than_15_minutes_early_percent"]
        + df["flights_15_minutes_early_to_1_minute_early_percent"]
        + df["flights_0_to_15_minutes_late_percent"]
    ) / 100.0
    df["delay_x_flights"] = df["average_delay_mins"] * df["number_flights_matched"]
    df["group"] = np.where(df["airline_name"].str.contains("BRITISH AIRWAYS", na=False), "British Airways", "Other airlines")

    def agg(frame: pd.DataFrame, label: str | None) -> pd.DataFrame:
        g = frame.groupby(["reporting_period", "uk", "eu"] + ([] if label else ["group"]))
        r = g[["number_flights_matched", "number_flights_cancelled", "on_time_flights", "delay_x_flights"]].sum().reset_index()
        if label:
            r["group"] = label
        return r

    out = pd.concat([agg(df, None), agg(df, "All airlines")], ignore_index=True)
    out = out[out["number_flights_matched"] > 0].copy()
    out["on_time_pct"] = 100 * out["on_time_flights"] / out["number_flights_matched"]
    out["avg_delay_min"] = out["delay_x_flights"] / out["number_flights_matched"]
    out["cancel_pct"] = 100 * out["number_flights_cancelled"] / (out["number_flights_matched"] + out["number_flights_cancelled"])
    out["month"] = pd.to_datetime(out["reporting_period"].astype(str), format="%Y%m")
    return out.rename(columns={"number_flights_matched": "flights"})[
        ["month", "uk", "eu", "group", "flights", "on_time_pct", "avg_delay_min", "cancel_pct"]
    ].sort_values(["uk", "eu", "group", "month"]).reset_index(drop=True)


# --------------------------------------------------------------------------- entry point
STEPS = {
    "traffic": ("route_hourly.parquet", build_route_hourly),
    "delays": ("airport_daily.parquet", build_airport_daily),
    "caa": ("caa_routes.parquet", build_caa_routes),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("steps", nargs="*", default=list(STEPS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    PROCESSED.mkdir(parents=True, exist_ok=True)
    for step in args.steps:
        name, fn = STEPS[step]
        df = fn()
        df.to_parquet(PROCESSED / name, index=False)
        log.info("wrote %s (%s rows)", name, f"{len(df):,}")


if __name__ == "__main__":
    main()
