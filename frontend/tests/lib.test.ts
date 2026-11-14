import assert from "node:assert/strict";
import { test } from "node:test";
import { combineStatus, dayOfYear, harmonizedCells, naiveSplice, rawCells, weekIndex, yearsBetween } from "../src/lib/calendar.ts";
import { fmt, windowEnd } from "../src/lib/format.ts";
import { checkPointRadius, circlePolygon, distanceKm, rewindForD3 } from "../src/lib/geo.ts";

test("week bins match the backend (7-day bins from 1 Jan, last bin absorbs year end)", () => {
  assert.equal(weekIndex("2019-01-01"), 0);
  assert.equal(weekIndex("2019-01-08"), 1);
  assert.equal(weekIndex("2019-12-24"), 51);
  assert.equal(weekIndex("2020-12-31"), 51);
  assert.equal(dayOfYear("2020-03-01"), 61);
  assert.equal(windowEnd("2019-12-24", "week"), "2019-12-31");
  assert.equal(windowEnd("2019-01-01", "week"), "2019-01-07");
  assert.deepEqual(yearsBetween("2018-06-01", "2020-01-01"), [2018, 2019, 2020]);
});

test("unknown coverage is never shown as observed zero", () => {
  assert.equal(combineStatus(["none_reported_coverage_unknown", "observed_none"], 0), "none_reported_coverage_unknown");
  assert.equal(combineStatus(["not_queried", "not_queried"], 0), "not_queried");
  assert.equal(combineStatus(["observed_none", "not_queried"], 0), "observed_none");
  assert.equal(combineStatus(["observed_none", "detections"], 3), "detections");
});

test("raw cells pick one platform or pool all of them", () => {
  const rows = [
    { window_start: "2019-01-01", platform: "Aqua", status: "detections", n_quality: 3 },
    { window_start: "2019-01-01", platform: "Suomi-NPP", status: "detections", n_quality: 9 },
    { window_start: "2019-01-08", platform: "Aqua", status: "none_reported_coverage_unknown", n_quality: 0 },
    { window_start: "2019-01-08", platform: "Suomi-NPP", status: "not_queried", n_quality: 0 },
  ];
  const aqua = rawCells(rows, "Aqua", "n_quality", "week");
  assert.equal(aqua.length, 2);
  assert.equal(aqua[0].value, 3);
  const pooled = rawCells(rows, "__pooled__", "n_quality", "week");
  assert.equal(pooled.find((c) => c.windowStart === "2019-01-01")!.value, 12);
  assert.equal(pooled.find((c) => c.windowStart === "2019-01-08")!.status, "none_reported_coverage_unknown");
});

test("harmonised cells keep basis and intervals; zero means observed only with coverage", () => {
  const rows = [
    { window_start: "2021-07-02", harmonized: 10, harm_lo: 8, harm_hi: 12, basis: "converted_from_Suomi-NPP" },
    { window_start: "2021-07-09", harmonized: 0, harm_lo: 0, harm_hi: 1, basis: "converted_from_Suomi-NPP" },
    { window_start: "2009-12-24", harmonized: null, harm_lo: null, harm_hi: null, basis: "none" },
  ];
  const c = harmonizedCells(rows, "2021-01-01", "2021-12-31");
  assert.equal(c.length, 2);
  assert.equal(c[0].basis, "converted_from_Suomi-NPP");
  assert.equal(c[1].status, "none_reported_coverage_unknown");
  assert.equal(harmonizedCells(rows, "2021-01-01", "2021-12-31", true)[1].status, "observed_none");
});

test("naive splice switches sensors where the reference stops", () => {
  const s = naiveSplice([
    { window_start: "a", ref_raw: 10, src_raw: 30, ref_queried: true, src_queried: true },
    { window_start: "b", ref_raw: 0, src_raw: 33, ref_queried: false, src_queried: true },
  ]);
  assert.deepEqual(s.map((r) => r.value), [10, 33]);
  assert.deepEqual(s.map((r) => r.from), ["reference", "source"]);
});

test("GeoJSON exterior rings are rewound clockwise for d3", () => {
  const ccw = { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] };
  const out = rewindForD3(ccw);
  assert.deepEqual(out.coordinates[0][1], [0, 1]);
  const again = rewindForD3(out);
  assert.deepEqual(again.coordinates[0], out.coordinates[0]);
});

test("number formatting", () => {
  assert.equal(fmt(null), "–");
  assert.equal(fmt(12345.6), "12,346");
  assert.equal(fmt(3.14159, 2), "3.14");
});

test("point + radius makes a closed counter-clockwise circle of the right size", () => {
  const g = circlePolygon(-10.25, -47.25, 50);
  const ring = g.coordinates[0];
  assert.deepEqual(ring[0], ring[ring.length - 1]);
  for (const [x, y] of ring.slice(0, -1)) assert.ok(Math.abs(distanceKm(-10.25, -47.25, y, x) - 50) < 0.05);
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  assert.ok(a > 0, "exterior ring should be counter-clockwise");
  // crossing 180°: longitudes stay continuous for the server to split
  const xs = circlePolygon(0, 179.9, 100).coordinates[0].map((p) => p[0]);
  assert.ok(Math.max(...xs) > 180 && Math.min(...xs) > 178);
});

test("point + radius input is range-checked", () => {
  assert.equal(checkPointRadius(-10, -47, 50), null);
  assert.match(checkPointRadius(95, 0, 10)!, /Latitude/);
  assert.match(checkPointRadius(0, -190, 10)!, /Longitude/);
  assert.match(checkPointRadius(0, 0, 0.2)!, /Radius/);
  assert.match(checkPointRadius(0, 0, 900)!, /Radius/);
  assert.match(checkPointRadius(88, 0, 200)!, /pole/);
  assert.match(checkPointRadius(NaN, 0, 10)!, /numbers/);
});
