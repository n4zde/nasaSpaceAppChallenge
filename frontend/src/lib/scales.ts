import { interpolateRgbBasis } from "d3-interpolate";

export const PLATFORM_COLOR: Record<string, string> = {
  Terra: "var(--s-terra)",
  Aqua: "var(--s-aqua)",
  "Suomi-NPP": "var(--s-snpp)",
  "NOAA-20": "var(--s-noaa20)",
  "NOAA-21": "var(--s-noaa21)",
};

/** Fixed display order: colour and position follow the platform, never its rank. */
export const PLATFORM_ORDER = ["Terra", "Aqua", "Suomi-NPP", "NOAA-20", "NOAA-21"];
export const byPlatformOrder = (a: string, b: string) => PLATFORM_ORDER.indexOf(a) - PLATFORM_ORDER.indexOf(b);

export const PLATFORM_INSTRUMENT: Record<string, string> = {
  Terra: "MODIS",
  Aqua: "MODIS",
  "Suomi-NPP": "VIIRS",
  "NOAA-20": "VIIRS",
  "NOAA-21": "VIIRS",
};

// Heat ramp: used only for detection-based quantities. One hue family
// (orange to red, spread <= 40 degrees in OKLCH), lightness monotone, and the
// faintest step clears 2:1 against the panel. Each colour scheme has its own
// steps: on light panels more activity is darker, on dark panels it is
// brighter. Both ramps were checked with an ordinal palette validator
// (docs/methods.md, "Colour").
export const HEAT_LIGHT = ["#f5a24d", "#e8721c", "#ce4714", "#a12818", "#6f1a13"];
export const HEAT_DARK = ["#9a3420", "#ca4b20", "#e97125", "#ffa14b", "#ffcf92"];

export function prefersDark(): boolean {
  if (typeof document === "undefined") return false;
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "dark" || t === "light") return t === "dark";
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

const ramps = { light: interpolateRgbBasis(HEAT_LIGHT), dark: interpolateRgbBasis(HEAT_DARK) };
const ramp = (t: number) => (prefersDark() ? ramps.dark : ramps.light)(t);

/** log-scaled colour for a positive value given the max of the view. */
export function heatColor(v: number, max: number): string {
  if (!(v > 0) || !(max > 0)) return ramp(0);
  const t = Math.log1p(v) / Math.log1p(max);
  return ramp(Math.min(1, Math.max(0, t)));
}

export function heatStops(n = 6): string[] {
  return Array.from({ length: n }, (_, i) => ramp(i / (n - 1)));
}

export type CellStatus =
  | "detections"
  | "observed_none"
  | "partially_observed_none"
  | "unobserved"
  | "none_reported_coverage_unknown"
  | "not_queried";

export const STATUS_TEXT: Record<string, string> = {
  detections: "Detections reported",
  observed_none: "Observed, no detections",
  partially_observed_none: "Partly observed, no detections",
  unobserved: "Not observed (clouds or no overpass)",
  none_reported_coverage_unknown: "No detections reported; coverage unknown",
  not_queried: "No data imported for this period",
};

export const SIGNAL_TEXT: Record<string, string> = {
  unusually_high: "Unusually high for the season (FDR-controlled)",
  unusually_low: "Unusually low for the season (FDR-controlled)",
  typical: "Within the usual range for the season",
  insufficient_history: "Not enough other years to judge",
  not_observed: "Not observed",
  too_sparse: "Baseline too sparse to test",
};
