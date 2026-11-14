"""Approximate fire-event counts from active-fire detections.

FIRMS lists fire *pixels*, not fires. Many users nevertheless want a "number
of fires". Ember Atlas offers an explicitly approximate answer: quality
detections of ONE platform are grouped into space-time clusters, and the
number of clusters is reported as "estimated fire events".

Rule (single linkage): two detections belong to the same event when they are
within ``link_km`` of each other in both projected x and y (a square
neighbourhood on the equal-area plane) AND their acquisition dates differ by
at most ``link_days``. Chains of such links form one event.

Defaults: link_km = 2.0 for MODIS (about two nominal 1 km pixels, covering
adjacent pixels and their geolocation spread), 1.0 for VIIRS (about two to
three 375 m pixels); link_days = 1 (a fire seen on consecutive days stays one
event; a gap of two or more clear days starts a new one).

Limits, stated wherever the number is shown:
* Counts depend on the sensor: VIIRS resolves fires that MODIS merges or
  misses, so event counts are compared only within one platform.
* Cloud gaps longer than ``link_days`` split one long fire into several
  events; large fronts that merge make several fires one event.
* Not a count of reported fires, ignitions or burned areas, and not
  validated against fire reports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

DEFAULT_LINK_KM = {"MODIS": 2.0, "VIIRS": 1.0}
DEFAULT_LINK_DAYS = 1


def cluster_events(df: pd.DataFrame, link_km: float, link_days: int = DEFAULT_LINK_DAYS) -> np.ndarray:
    """Return an event label for each row of ``df`` (columns x_ease, y_ease
    in metres, acq_date YYYY-MM-DD). Rows without projected coordinates get -1."""
    n = len(df)
    labels = np.full(n, -1, dtype=np.int64)
    ok = df["x_ease"].notna().to_numpy() & df["y_ease"].notna().to_numpy()
    if not ok.any():
        return labels
    x = df["x_ease"].to_numpy(float)[ok] / (link_km * 1000.0)
    y = df["y_ease"].to_numpy(float)[ok] / (link_km * 1000.0)
    days = pd.to_datetime(df["acq_date"].to_numpy()[ok], format="%Y-%m-%d")
    t = ((days - pd.Timestamp("2000-01-01")).days.to_numpy(float)) / max(link_days, 1e-9)
    pts = np.column_stack([x, y, t])
    tree = cKDTree(pts)
    # Chebyshev distance <= 1 in scaled units == |dx|,|dy| <= link_km and |dt| <= link_days
    pairs = tree.query_pairs(r=1.0 + 1e-9, p=np.inf, output_type="ndarray")
    m = len(pts)
    if len(pairs):
        g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(m, m))
        _, lab = connected_components(g, directed=False)
    else:
        lab = np.arange(m)
    labels[ok] = lab
    return labels


def events_by_year(df: pd.DataFrame, instrument: str, link_km: float | None = None, link_days: int = DEFAULT_LINK_DAYS) -> pd.DataFrame:
    """Events per calendar year for one platform's quality detections.

    An event is counted in the year of its first detection. Returns columns
    year, events, detections."""
    if df.empty:
        return pd.DataFrame(columns=["year", "events", "detections"])
    lk = link_km if link_km is not None else DEFAULT_LINK_KM[instrument]
    lab = cluster_events(df, lk, link_days)
    d = pd.DataFrame({"label": lab, "date": df["acq_date"].to_numpy()})
    d = d[d["label"] >= 0]
    first = pd.Series(event_start_dates(df, instrument, link_km, link_days))
    years = first.str.slice(0, 4).astype(int)
    ev = years.value_counts().rename("events")
    det = d["date"].str.slice(0, 4).astype(int).value_counts().rename("detections")
    out = pd.concat([ev, det], axis=1).fillna(0).astype(int).reset_index()
    out.columns = ["year", "events", "detections"]
    return out.sort_values("year").reset_index(drop=True)


def events_in_period(df: pd.DataFrame, instrument: str, start: str, end: str, link_km: float | None = None, link_days: int = DEFAULT_LINK_DAYS) -> int:
    """Number of events whose first detection falls in [start, end]."""
    sub = df[(df["acq_date"] >= start) & (df["acq_date"] <= end)]
    if sub.empty:
        return 0
    lk = link_km if link_km is not None else DEFAULT_LINK_KM[instrument]
    lab = cluster_events(sub, lk, link_days)
    return int(len(np.unique(lab[lab >= 0])))


def event_start_dates(df: pd.DataFrame, instrument: str, link_km: float | None = None, link_days: int = DEFAULT_LINK_DAYS) -> np.ndarray:
    """Cluster the whole record once and return the start date (YYYY-MM-DD) of
    every event. Events are attributed to the date of their first detection, so
    counting events in any period is a cheap range count on this array."""
    if df.empty:
        return np.array([], dtype=object)
    lk = link_km if link_km is not None else DEFAULT_LINK_KM[instrument]
    lab = cluster_events(df, lk, link_days)
    ok = lab >= 0
    day = (pd.to_datetime(df["acq_date"].to_numpy()[ok], format="%Y-%m-%d") - pd.Timestamp("2000-01-01")).days.to_numpy()
    first = np.full(lab.max() + 1, np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(first, lab[ok], day)
    first = np.sort(first[first < np.iinfo(np.int64).max])
    return (pd.Timestamp("2000-01-01") + pd.to_timedelta(first, unit="D")).strftime("%Y-%m-%d").to_numpy().astype(str)


def count_between(starts: np.ndarray, a: str, b: str) -> int:
    return int(np.searchsorted(starts, b, side="right") - np.searchsorted(starts, a, side="left"))
