import numpy as np
import pandas as pd

from ember_atlas.geo import equal_area as ea
from ember_atlas.science.events import cluster_events, events_by_year, events_in_period


def _df(rows):
    """rows: (lon, lat, date)"""
    lon = np.array([r[0] for r in rows], float)
    lat = np.array([r[1] for r in rows], float)
    x, y = ea.forward(lon, lat)
    return pd.DataFrame({"x_ease": x, "y_ease": y, "acq_date": [r[2] for r in rows]})


KM = 1 / 111.0  # ~1 km in degrees of latitude


def test_adjacent_pixels_on_consecutive_days_are_one_event():
    rows = [(-47.0, -10.0, "2019-08-01"), (-47.0, -10.0 + 0.8 * KM, "2019-08-01"), (-47.0, -10.0 + 1.6 * KM, "2019-08-02")]
    lab = cluster_events(_df(rows), link_km=1.0, link_days=1)
    assert len(set(lab)) == 1  # chained links form one event


def test_distance_and_time_gaps_split_events():
    rows = [
        (-47.0, -10.0, "2019-08-01"),
        (-47.0, -10.0 + 5 * KM, "2019-08-01"),  # 5 km away
        (-47.0, -10.0, "2019-08-04"),  # same place, 3 days later
    ]
    lab = cluster_events(_df(rows), link_km=2.0, link_days=1)
    assert len(set(lab)) == 3


def test_missing_coordinates_are_not_counted():
    df = _df([(-47.0, -10.0, "2019-08-01")])
    df.loc[1] = [np.nan, np.nan, "2019-08-01"]
    lab = cluster_events(df, 2.0)
    assert lab.tolist()[1] == -1


def test_events_counted_in_year_of_first_detection():
    rows = [(-47.0, -10.0, "2019-12-31"), (-47.0, -10.0, "2020-01-01"), (-46.0, -10.0, "2020-03-01")]
    by = events_by_year(_df(rows), "MODIS")
    assert by.set_index("year")["events"].to_dict() == {2019: 1, 2020: 1}
    assert by.set_index("year")["detections"].to_dict() == {2019: 1, 2020: 2}
    assert events_in_period(_df(rows), "MODIS", "2020-01-01", "2020-12-31") == 2


def test_vectorised_matches_bruteforce_on_random_data():
    rng = np.random.default_rng(3)
    n = 300
    rows = [(-47 + rng.uniform(0, 0.3), -10 + rng.uniform(0, 0.3), f"2019-08-{rng.integers(1, 20):02d}") for _ in range(n)]
    df = _df(rows)
    lab = cluster_events(df, 2.0, 1)
    # brute force single linkage with the same square rule
    x, y = df["x_ease"].to_numpy() / 2000, df["y_ease"].to_numpy() / 2000
    t = pd.to_datetime(df["acq_date"]).map(pd.Timestamp.toordinal).to_numpy()
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if abs(x[i] - x[j]) <= 1 and abs(y[i] - y[j]) <= 1 and abs(t[i] - t[j]) <= 1:
                parent[find(i)] = find(j)
    brute = [find(i) for i in range(n)]
    # same partition
    assert len(set(brute)) == len(set(lab))
    pairs = {(a, b) for a, b in zip(brute, lab)}
    assert len(pairs) == len(set(brute))


def test_event_start_dates_match_period_counts():
    from ember_atlas.science.events import count_between, event_start_dates

    rows = [(-47.0, -10.0, "2019-12-31"), (-47.0, -10.0, "2020-01-01"), (-46.0, -10.0, "2020-03-01"), (-45.0, -10.0, "2020-03-05")]
    starts = event_start_dates(_df(rows), "MODIS")
    assert starts.tolist() == ["2019-12-31", "2020-03-01", "2020-03-05"]
    assert count_between(starts, "2020-01-01", "2020-12-31") == 2
    assert count_between(starts, "2020-03-01", "2020-03-01") == 1
