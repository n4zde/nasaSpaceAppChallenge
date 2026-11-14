"""Application service: the single entry point used by the HTTP API and the CLI.

Every analytical response carries a ``provenance`` block (method version,
config fingerprint, data version, data origin, batches used, generation time)
so exported numbers can be traced and reproduced.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import __version__
from .analysis.aggregate import Selection, cell_aggregates, coverage_set_for, load, platform_window_series
from .analysis.windows import window_table
from .config import method_config, method_fingerprint
from .geo import equal_area as ea
from .geo.polygons import AOIError, parse_aoi
from .ingest import firms_schema as fs
from .ingest.importer import import_bytes
from .ingest.normalize import Declaration
from .presets import PRESET_BY_ID, PRESETS
from .science import anomalies as anom
from .science.calibration import build_pair_table
from .science.evaluation import evaluate
from .science.harmonize import harmonize
from .store.base import Store

ALL_PLATFORMS = ["Terra", "Aqua", "Suomi-NPP", "NOAA-20", "NOAA-21"]


def _code_fingerprint() -> str:
    """Hash of the analysis code, so cached results are invalidated whenever
    the scientific code changes (not only the method config)."""
    from pathlib import Path

    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for sub in ("analysis", "science", "geo", "ingest", "synthetic"):
        for f in sorted((root / sub).glob("*.py")):
            h.update(f.read_bytes())
    h.update((root / "service.py").read_bytes())
    return h.hexdigest()[:16]


CODE_FINGERPRINT = _code_fingerprint()


class ServiceError(ValueError):
    """User-facing error with an HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if not np.isfinite(f) else f
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if o is pd.NaT:
        return None
    return o


@dataclass
class Job:
    id: str
    kind: str
    key: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    result_key: str | None = None
    error: str | None = None
    created: str = field(default_factory=_now)


class EmberService:
    def __init__(self, store: Store, method_overrides: dict | None = None):
        self.store = store
        self.base_cfg = method_config(method_overrides)
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ember-job")
        self._mem: dict[str, object] = {}
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_guard = threading.Lock()

    def _once(self, key: str, fn):
        """Compute-or-wait: concurrent requests for the same result share one
        computation instead of racing (e.g. the calendar and the anomaly view
        both needing the same calibration)."""
        if (c := self._cached(key)) is not None:
            return c
        with self._key_locks_guard:
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            if (c := self._cached(key)) is not None:
                return c
            return self._put(key, fn())

    # ================================================================ helpers
    def cfg_for(self, quality_min_confidence: str | None = None) -> dict:
        if quality_min_confidence and quality_min_confidence != self.base_cfg["quality"]["min_confidence"]:
            if quality_min_confidence not in fs.CONFIDENCE_ORDER:
                raise ServiceError("min_confidence must be low, nominal or high")
            cfg = json.loads(json.dumps(self.base_cfg))
            cfg["quality"]["min_confidence"] = quality_min_confidence
            return cfg
        return self.base_cfg

    def selection(self, req: dict) -> Selection:
        origin = req.get("dataset", "synthetic")
        if origin not in ("synthetic", "real"):
            raise ServiceError("dataset must be 'synthetic' or 'real'")
        aoi_in = req.get("aoi")
        label = None
        if isinstance(aoi_in, str) and aoi_in in PRESET_BY_ID:
            label = PRESET_BY_ID[aoi_in]["name"]
            aoi_in = PRESET_BY_ID[aoi_in]["geometry"]
        if aoi_in is None:
            raise ServiceError("aoi is required (GeoJSON polygon, bbox, or preset id)")
        try:
            aoi = parse_aoi(aoi_in, name=label)
        except AOIError as exc:
            raise ServiceError(f"Invalid area of interest: {exc}") from None
        ds, de = req.get("date_start"), req.get("date_end")
        try:
            d0, d1 = pd.Timestamp(ds), pd.Timestamp(de)
        except (TypeError, ValueError):
            raise ServiceError("date_start and date_end must be YYYY-MM-DD") from None
        if d1 < d0:
            raise ServiceError("date_end is before date_start")
        if (d1 - d0).days > 366 * 30:
            raise ServiceError("date range longer than 30 years")
        plats = req.get("platforms") or ["Terra", "Aqua", "Suomi-NPP", "NOAA-20"]
        bad = [p for p in plats if p not in ALL_PLATFORMS]
        if bad:
            raise ServiceError(f"unknown platforms {bad}")
        streams = req.get("streams") or None
        if streams and any(s not in ("SP", "NRT", "RT", "URT") for s in streams):
            raise ServiceError("streams must be among SP, NRT, RT, URT")
        cfg = self.cfg_for(req.get("min_confidence"))
        grid_km = int(req.get("grid_km") or cfg["grid"]["default_km"])
        if grid_km not in cfg["grid"]["allowed_km"]:
            raise ServiceError(f"grid_km must be one of {cfg['grid']['allowed_km']}")
        window = req.get("window") or cfg["temporal"]["default_window"]
        if window not in ("day", "week"):
            raise ServiceError("window must be 'day' or 'week'")
        sel = Selection(origin, aoi, d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d"), list(plats), streams, grid_km, window, cfg, label)
        return sel

    def _key(self, kind: str, sel: Selection, extra: dict | None = None) -> str:
        payload = {
            "kind": kind,
            "sel": sel.key(),
            "extra": extra or {},
            "data": self.store.data_version(sel.data_origin),
            "method": method_fingerprint(sel.cfg),
            "v": __version__,
            "code": CODE_FINGERPRINT,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _cached(self, key: str):
        if key in self._mem:
            return self._mem[key]
        raw = self.store.cache_get(key)
        if raw is not None:
            val = json.loads(raw)
            self._mem[key] = val
            return val
        return None

    def _put(self, key: str, value: dict) -> dict:
        value = _jsonable(value)
        self.store.cache_put(key, json.dumps(value))
        self._mem[key] = value
        return value

    def provenance(self, sel: Selection, extra: dict | None = None) -> dict:
        batches = self.store.list_batches(sel.data_origin)
        return {
            "app_version": __version__,
            "method_version": sel.cfg["method_version"],
            "method_fingerprint": method_fingerprint(sel.cfg),
            "data_origin": sel.data_origin,
            "data_version": self.store.data_version(sel.data_origin),
            "synthetic": sel.data_origin == "synthetic",
            "generated_at": _now(),
            "store": self.store.kind,
            "batches": [
                {
                    k: b.get(k)
                    for k in ("id", "file_name", "file_sha256", "declared_source", "source_url", "retrieved_at", "imported_at", "parser_version", "status")
                }
                for b in batches
                if b.get("status") == "complete"
            ],
            "selection": {**sel.key(), "aoi_label": sel.aoi_label, "aoi_area_km2": round(sel.aoi.area_km2(), 1), "quality": sel.cfg["quality"]},
            **(extra or {}),
        }

    # ================================================================ meta
    def meta(self) -> dict:
        out = {
            "app_version": __version__,
            "method": self.base_cfg,
            "method_fingerprint": method_fingerprint(self.base_cfg),
            "datasets": {},
            "presets": PRESETS,
            "platforms": ALL_PLATFORMS,
            "products": {
                k: {
                    "instrument": p.instrument,
                    "platforms": p.platforms,
                    "stream": p.stream,
                    "nominal_pixel_m": p.nominal_pixel_m,
                    "archive_start": p.archive_start,
                    "description": p.description,
                }
                for k, p in fs.PRODUCTS.items()
            },
            "docs": fs.DOCS,
            "docs_retrieved": fs.RETRIEVED,
            "store": self.store.kind,
        }
        for origin in ("synthetic", "real"):
            s = self.store.dataset_summary(origin)
            cs = coverage_set_for(self.store, origin)
            s["coverage_available"] = cs is not None
            s["coverage_set"] = cs
            av = self.store.availability(origin)
            s["availability"] = av.drop(columns=["bbox"]).to_dict("records") if len(av) else []
            out["datasets"][origin] = s
        return _jsonable(out)

    def validate_aoi(self, aoi_in) -> dict:
        try:
            aoi = parse_aoi(PRESET_BY_ID[aoi_in]["geometry"] if isinstance(aoi_in, str) and aoi_in in PRESET_BY_ID else aoi_in)
        except AOIError as exc:
            raise ServiceError(str(exc)) from None
        cells = {k: len(aoi.cell_overlaps(ea.GridSpec(k))) for k in (18, 36, 72)}
        return {
            "geometry": aoi.to_geojson(),
            "area_km2": aoi.area_km2(),
            "crosses_antimeridian": aoi.crosses_antimeridian,
            "warnings": aoi.warnings,
            "parts": len(aoi.polygons),
            "cells_by_grid_km": cells,
            "bboxes": aoi.bboxes(),
        }

    # ================================================================ summary / calendar
    def summary(self, req: dict) -> dict:
        sel = self.selection(req)
        key = self._key("summary", sel)

        def compute():
            series, meta = platform_window_series(self.store, sel)
            windows = window_table(sel.date_start, sel.date_end, sel.window)
            totals = series.groupby("platform")[["n_raw", "n_quality"]].sum().to_dict("index")
            return {
                "windows": windows.to_dict("records"),
                "series": series.replace({np.nan: None}).to_dict("records"),
                "totals": totals,
                "meta": meta,
                "aoi": {"geometry": sel.aoi.to_geojson(), "area_km2": sel.aoi.area_km2(), "warnings": sel.aoi.warnings, "label": sel.aoi_label},
                "grid": {"km": sel.grid_km, "cell_area_km2": sel.grid.cell_area_km2, "projection": "EASE-Grid 2.0 (EPSG:6933)"},
                "provenance": self.provenance(sel),
            }

        return self._once(key, compute)

    # ================================================================ map
    def map_cells(self, req: dict) -> dict:
        sel = self.selection(req)
        wf, wt = req.get("window_from"), req.get("window_to")
        key = self._key("cells", sel, {"wf": wf, "wt": wt})

        def compute():
            # restrict the query itself to the requested window (much faster than filtering afterwards)
            csel = self.selection({**req, "date_start": wf, "date_end": wt or wf}) if wf else sel
            cells = cell_aggregates(self.store, csel)
            feats = []
            for _, r in cells.iterrows():
                size, col, row = ea.decode_id(r["cell_id"])
                w, s, e, n = sel.grid.cell_bounds_lonlat(col, row)
                props = {k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in r.items()}
                feats.append(
                    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}, "properties": props}
                )
            res = {
                "type": "FeatureCollection",
                "features": feats,
                "grid_km": sel.grid_km,
                "window_from": wf,
                "window_to": wt,
                "provenance": self.provenance(sel),
            }
            return res

        return self._once(key, compute)

    def detections(self, req: dict) -> dict:
        wf, wt = req.get("window_from"), req.get("window_to")
        sel = self.selection({**req, "date_start": wf, "date_end": wt or wf} if wf else req)
        df = load(self.store, sel)
        page = max(1, int(req.get("page", 1)))
        size = min(5000, max(1, int(req.get("page_size", 2000))))
        total = len(df)
        df = df.sort_values(["acq_datetime_utc", "platform"]).iloc[(page - 1) * size : page * size]
        feats = []
        for _, r in df.iterrows():
            props = {
                k: r[k]
                for k in (
                    "platform",
                    "instrument",
                    "source_product",
                    "processing_stream",
                    "version_raw",
                    "acq_datetime_utc",
                    "confidence_class",
                    "confidence_raw",
                    "daynight",
                    "frp_mw",
                    "scan_km",
                    "track_km",
                    "fire_type",
                    "batch_id",
                    "quality",
                    "cell_id",
                )
            }
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(r["longitude"]), float(r["latitude"])]}, "properties": props})
        return _jsonable(
            {
                "type": "FeatureCollection",
                "features": feats,
                "page": page,
                "page_size": size,
                "total": total,
                "pages": int(np.ceil(total / size)) if total else 0,
                "provenance": self.provenance(sel),
            }
        )

    def window_detail(self, req: dict) -> dict:
        """Selected-day/week details: per-platform metrics, coverage, provenance."""
        sel = self.selection(req)
        ws = req.get("window_start")
        if not ws:
            raise ServiceError("window_start is required")
        summ = self.summary(req)
        rows = [r for r in summ["series"] if r["window_start"] == ws]
        if not rows:
            raise ServiceError("window not in selection", 404)
        df = load(self.store, sel)
        sub = df[df["window_start"] == ws]
        prov = []
        if len(sub):
            g = sub.groupby(["platform", "source_product", "processing_stream", "version_raw", "batch_id"]).size().reset_index(name="detections")
            prov = g.to_dict("records")
        batches = {b["id"]: b for b in self.store.list_batches(sel.data_origin)}
        for p in prov:
            b = batches.get(int(p["batch_id"]), {})
            p.update({"file_name": b.get("file_name"), "source_url": b.get("source_url"), "retrieved_at": b.get("retrieved_at")})
        conf = sub.groupby(["platform", "confidence_class"]).size().unstack(fill_value=0).to_dict("index") if len(sub) else {}
        return _jsonable(
            {
                "window_start": ws,
                "platforms": rows,
                "provenance_rows": prov,
                "confidence_breakdown": conf,
                "static_or_offshore_excluded": int(sub["fire_type"].isin([1, 2, 3]).sum()) if len(sub) else 0,
                "provenance": self.provenance(sel),
            }
        )

    # ================================================================ harmonisation
    def _pair(self, req: dict) -> tuple[str, str]:
        ref = req.get("reference") or self.base_cfg["calibration"]["reference_platform"]
        src = req.get("source") or self.base_cfg["calibration"]["default_source_platform"]
        if [ref, src] not in self.base_cfg["calibration"]["allowed_pairs"]:
            raise ServiceError(f"pair {ref} <- {src} not supported; allowed: {self.base_cfg['calibration']['allowed_pairs']}")
        return ref, src

    def full_extent(self, origin: str, platforms: list[str]) -> tuple[str, str] | None:
        av = self.store.availability(origin)
        av = av[av["platform"].isin(platforms)]
        if av.empty:
            return None
        return str(av["date_start"].min()), str(av["date_end"].max())

    def _calib_selection(self, req: dict) -> Selection:
        """Calibration, harmonisation and anomaly baselines always use the full
        stored record of the AOI for the platforms involved, not just the dates
        on screen: a short view must not starve the fit or the baseline.
        The response covers the whole record; clients filter to their view."""
        ref, src = self._pair(req)
        ext = self.full_extent(req.get("dataset", "synthetic"), [ref, src])
        if ext is None:
            raise ServiceError("No data imported for these platforms in this dataset.", 404)
        return self.selection({**req, "date_start": ext[0], "date_end": ext[1], "platforms": [ref, src], "window": "week"})

    def evaluation_key(self, req: dict) -> str:
        sel = self._calib_selection(req)
        ref, src = self._pair(req)
        return self._key("evaluation", sel, {"ref": ref, "src": src, "B": req.get("bootstrap_reps")})

    def get_evaluation(self, req: dict) -> dict | None:
        return self._cached(self.evaluation_key(req))

    def run_evaluation(self, req: dict, progress=None) -> dict:
        sel = self._calib_selection(req)
        ref, src = self._pair(req)
        key = self.evaluation_key(req)

        def compute():
            pair = build_pair_table(self.store, sel, ref, src)
            from .science.status import sufficiency

            suff = sufficiency(pair.info, sel.cfg)
            if not suff["passed"]:
                res = {"pair": pair.info, "sufficiency": suff, "skipped": "insufficient overlapping evidence", "gates": {"passed": False, "checks": []}}
            else:
                res = evaluate(pair, sel.cfg, bootstrap_reps=req.get("bootstrap_reps"))
                res["pair"] = pair.info
                res["sufficiency"] = suff
            res["provenance"] = self.provenance(sel, {"reference": ref, "source": src})
            return res

        return self._once(key, compute)

    def start_evaluation(self, req: dict) -> dict:
        self.selection(req)  # validate now, fail fast
        self._pair(req)
        key = self.evaluation_key(req)
        if self._cached(key) is not None:
            return {"job": None, "status": "done", "result_key": key}
        with self._jobs_lock:
            for j in self._jobs.values():
                if j.key == key and j.status in ("queued", "running"):
                    return {"job": j.id, "status": j.status}
            job = Job(id=uuid.uuid4().hex[:12], kind="evaluation", key=key)
            self._jobs[job.id] = job

        def run():
            job.status = "running"
            job.message = "Running blocked temporal and spatial hold-out validation"
            try:
                self.run_evaluation(req)
                job.result_key = key
                job.status = "done"
                job.progress = 1.0
            except Exception as exc:  # surfaced to the UI, details in server log
                job.status = "error"
                job.error = str(exc)
                traceback.print_exc()

        self._pool.submit(run)
        return {"job": job.id, "status": job.status}

    def job(self, job_id: str) -> dict:
        j = self._jobs.get(job_id)
        if j is None:
            raise ServiceError("unknown job", 404)
        return {"id": j.id, "kind": j.kind, "status": j.status, "message": j.message, "error": j.error, "created": j.created, "done": j.status == "done"}

    def harmonized(self, req: dict) -> dict:
        sel = self._calib_selection(req)
        ref, src = self._pair(req)
        evaluation = self.get_evaluation(req)
        key = self._key("harmonized", sel, {"ref": ref, "src": src, "eval": bool(evaluation), "B": req.get("bootstrap_reps")})

        def compute():
            res = harmonize(self.store, sel, ref, src, evaluation=evaluation, bootstrap_reps=req.get("bootstrap_reps"))
            res["evaluation_summary"] = _eval_summary(evaluation)
            res["provenance"] = self.provenance(sel, {"reference": ref, "source": src})
            return res

        return self._once(key, compute)

    # ================================================================ anomalies
    def _weekly_activity(self, req: dict, sel: Selection, basis: str) -> tuple[pd.DataFrame, dict]:
        """Weekly activity series over the full stored record: harmonised when the
        calibration supports it ("auto"/"harmonized"), else one platform's quality
        detections. Columns: window_start, value, lo, hi, observed, conservative,
        coverage_known."""
        if basis in ("auto", "harmonized"):
            h = self.harmonized({**req, "window": "week"})
            if h["status"]["show_harmonized"]:
                s = pd.DataFrame(h["series"])
                df = pd.DataFrame(
                    {
                        "window_start": s["window_start"],
                        "value": s["harmonized"],
                        "lo": s["harm_lo"],
                        "hi": s["harm_hi"],
                        "observed": s["basis"] != "none",
                        "conservative": s["basis"].str.startswith("converted"),
                        "coverage_known": h["mode"] == "exposure",
                    }
                )
                return df, {"basis": "harmonized", "label": h["target"], "unit": f"{h['pair']['reference']}-equivalent detections", "status": h["status"]}
            if basis == "harmonized":
                raise ServiceError("Harmonised series not available for this selection: " + h["status"]["explanation"], 409)
            basis = "platform:" + self._pair(req)[0]
        plat = basis.split(":", 1)[1]
        ext = self.full_extent(sel.data_origin, [plat])
        if ext is None:
            raise ServiceError(f"No {plat} data imported in this dataset.", 404)
        psel = self.selection({**req, "window": "week", "platforms": [plat], "date_start": ext[0], "date_end": ext[1]})
        series, _ = platform_window_series(self.store, psel)
        df = pd.DataFrame(
            {
                "window_start": series["window_start"],
                "value": series["n_quality"].astype(float),
                "lo": np.nan,
                "hi": np.nan,
                "observed": series["queried_days"] >= series["n_days"],
                "conservative": False,
                "coverage_known": series["coverage_known"],
            }
        )
        return df, {
            "basis": f"platform:{plat}",
            "label": f"{plat} quality-filtered detections per week (single platform, unharmonised)",
            "unit": f"{plat} detections",
        }

    def anomalies(self, req: dict) -> dict:
        sel = self.selection({**req, "window": "week"})
        basis = _check_basis(req.get("basis", "auto"))  # auto | harmonized | platform:<name>
        key = self._key("anomalies", sel, {"basis": basis, "ref": req.get("reference"), "src": req.get("source"), "view": req.get("view")})

        def compute():
            df, info = self._weekly_activity(req, sel, basis)
            view = req.get("view") or [sel.date_start, sel.date_end]
            res = anom.analyse(df, sel.cfg, view=tuple(view))
            res["annual"] = anom.annual_totals(df)
            res["basis"] = info
            res["provenance"] = self.provenance(sel)
            return res

        return self._once(key, compute)

    # ================================================================ key figures
    def key_figures(self, req: dict) -> dict:
        """Headline figures for the last calendar year of the selected dates,
        each compared with the same dates in the other years of the record."""
        sel = self.selection({**req, "window": "week"})
        basis = _check_basis(req.get("basis", "auto"))
        key = self._key("keyfigures", sel, {"basis": basis, "ref": req.get("reference"), "src": req.get("source")})

        def compute():
            df, info = self._weekly_activity(req, sel, basis)
            year = int(sel.date_end[:4])
            p0 = max(sel.date_start, f"{year}-01-01")
            p1 = sel.date_end
            out = key_figures_from_series(df, p0, p1, sel.cfg)
            out["basis"] = info
            out["events"] = self._event_figures(sel, p0, p1)
            out["summary"] = summary_sentence(out, sel.aoi_label)
            out["provenance"] = self.provenance(sel)
            return out

        return self._once(key, compute)

    def _event_figures(self, sel: Selection, p0: str, p1: str) -> list[dict]:
        from dataclasses import replace

        from .analysis.aggregate import QUERY_COLUMNS, quality_mask
        from .science import events as ev
        from .store.base import DetectionQuery

        av = self.store.availability(sel.data_origin)
        out = []
        for plat in sel.platforms:
            ext = self.full_extent(sel.data_origin, [plat])
            if ext is None:
                continue
            instrument = fs.INSTRUMENT_OF_PLATFORM[plat]
            # keyed on the platform's full record, not the dates on screen, so changing the view reuses it
            ck = self._key("event_starts", replace(sel, date_start=ext[0], date_end=ext[1], platforms=[plat], window="week", _cache={}))
            cached = self._mem.get(ck)
            if cached is None:
                df = self.store.query_detections(
                    DetectionQuery(sel.data_origin, sel.aoi, ext[0], ext[1], [plat], sel.streams, QUERY_COLUMNS)
                )
                df = df[quality_mask(df, sel.cfg)] if not df.empty else df
                cached = (ev.event_start_dates(df, instrument), np.sort(df["acq_date"].to_numpy().astype(str)) if not df.empty else np.array([], dtype=str))
                self._mem[ck] = cached
            starts, det_dates = cached
            covered = _covered_years(av, plat, sel.streams, p0, p1)
            cur = ev.count_between(starts, p0, p1) if int(p0[:4]) in covered else None
            others = [ev.count_between(starts, _shift_year(p0, y), _shift_year(p1, y)) for y in sorted(covered) if y != int(p0[:4])]
            out.append(
                {
                    "platform": plat,
                    "instrument": instrument,
                    "events": cur,
                    "detections": ev.count_between(det_dates, p0, p1),
                    "average": float(np.mean(others)) if others else None,
                    "n_years": len(others),
                    "link_km": ev.DEFAULT_LINK_KM[instrument],
                    "link_days": ev.DEFAULT_LINK_DAYS,
                }
            )
        return out

    # ================================================================ imports
    def import_csv(self, data: bytes, file_name: str, decl: dict) -> dict:
        try:
            d = Declaration(
                data_origin=decl.get("dataset", "real"),
                source_id=decl.get("source_id") or None,
                source_url=decl.get("source_url") or None,
                retrieved_at=decl.get("retrieved_at") or None,
                period_start=decl.get("period_start") or None,
                period_end=decl.get("period_end") or None,
                notes=decl.get("notes") or None,
            )
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if len(data) > 200 * 1024 * 1024:
            raise ServiceError("file larger than 200 MB; import it with the command-line importer", 413)
        rep = import_bytes(self.store, data, file_name, d)
        self._mem.clear()
        return _jsonable(rep)

    def fetch_firms(self, req: dict, map_key: str | None, cache_dir=None, max_requests: int = 200, fetch=None) -> dict:
        """Download FIRMS Area-API data for the AOI's bounding box(es) and import
        it as REAL data. The key is used only for the outbound request."""
        from .ingest.firms_api import FirmsClient, FirmsError, public_source_url, redact, retrieved_now

        source = req.get("source")
        if source not in fs.PRODUCTS:
            raise ServiceError(f"source must be one of {sorted(fs.PRODUCTS)}")
        if not map_key:
            raise ServiceError("No FIRMS MAP_KEY: set FIRMS_MAP_KEY on the server or enter a key for this session.", 401)
        sel = self.selection({**req, "dataset": "real"})
        kwargs = {"map_key": map_key, "cache_dir": cache_dir, "max_requests": max_requests}
        if fetch is not None:
            kwargs["fetch"] = fetch
        try:
            client = FirmsClient(**kwargs)
            reports = []
            for bbox in sel.aoi.bboxes():
                data, chunks = client.area_csv(source, bbox, sel.date_start, sel.date_end)
                name = f"FIRMS_{source}_{sel.date_start}_{sel.date_end}_{bbox[0]:.2f}_{bbox[1]:.2f}.csv"
                decl = Declaration(
                    data_origin="real",
                    source_id=source,
                    source_url=public_source_url(source, bbox, sel.date_start, sel.date_end),
                    retrieved_at=retrieved_now(),
                    period_start=sel.date_start,
                    period_end=sel.date_end,
                    query_bbox=list(bbox),
                    notes=f"FIRMS Area API, {len(chunks)} request chunk(s)",
                )
                rep = import_bytes(self.store, data, name, decl)
                reports.append(rep)
        except FirmsError as exc:
            raise ServiceError(redact(str(exc), map_key), 502) from None
        self._mem.clear()
        return _jsonable(
            {
                "imports": reports,
                "requests_made": client.requests_made,
                "cache_hits": client.cache_hits,
                "note": "Your MAP_KEY was sent to NASA FIRMS as part of the request URL (required by the FIRMS API). It was not stored or logged.",
            }
        )

    def batches(self, origin: str | None = None) -> list[dict]:
        return _jsonable(
            [{k: v for k, v in b.items() if k != "report"} | {"summary": _batch_summary(b.get("report"))} for b in self.store.list_batches(origin)]
        )

    def batch(self, batch_id: int) -> dict:
        b = self.store.get_batch(batch_id)
        if b is None:
            raise ServiceError("batch not found", 404)
        b["issues"] = self.store.row_issues(batch_id, 200)
        return _jsonable(b)

    # ================================================================ export
    def export(self, req: dict) -> tuple[bytes, str, str]:
        fmt = req.get("format", "csv")
        content = req.get("content", "series")
        sel = self.selection(req)
        prov = self.provenance(sel)
        tag = ("SYNTHETIC_" if sel.data_origin == "synthetic" else "") + f"ember_{content}_{sel.date_start}_{sel.date_end}"
        header = {
            "data_origin": sel.data_origin.upper(),
            "method_version": prov["method_version"],
            "method_fingerprint": prov["method_fingerprint"],
            "data_version": prov["data_version"],
            "generated_at": prov["generated_at"],
        }
        if content == "series":
            df = pd.DataFrame(self.summary(req)["series"])
        elif content == "harmonized":
            h = self.harmonized(req)
            df = pd.DataFrame(h["series"])
            header["calibration_status"] = h["status"]["code"]
            header["target"] = h.get("target")
        elif content == "anomalies":
            df = pd.DataFrame(self.anomalies(req)["windows"])
        elif content == "cells":
            fc = self.map_cells(req)
            if fmt == "geojson":
                fc = {**fc, "properties": {**header, "provenance": prov}}
                return json.dumps(fc).encode(), f"{tag}.geojson", "application/geo+json"
            df = pd.DataFrame([f["properties"] for f in fc["features"]])
        elif content == "detections":
            d = load(self.store, sel)
            df = d.drop(columns=["x_ease", "y_ease"], errors="ignore")
            if fmt == "geojson":
                feats = [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
                        "properties": {k: _jsonable(v) for k, v in r._asdict().items() if k not in ("Index", "latitude", "longitude")},
                    }
                    for r in df.itertuples()
                ]
                out = {"type": "FeatureCollection", "features": feats, "properties": {**header, "provenance": prov}}
                return json.dumps(_jsonable(out)).encode(), f"{tag}.geojson", "application/geo+json"
        else:
            raise ServiceError("content must be series, harmonized, anomalies, cells or detections")
        if fmt == "geojson":
            raise ServiceError("GeoJSON export is available for cells and detections; use CSV for tables")
        for k, v in header.items():
            df[k] = v
        buf = io.StringIO()
        buf.write(
            "# Ember Atlas export. "
            + ("SYNTHETIC DEMO DATA - NOT NASA MEASUREMENTS. " if sel.data_origin == "synthetic" else "")
            + "Provenance JSON follows on the next line.\n"
        )
        buf.write("# " + json.dumps(_jsonable(prov)) + "\n")
        df.to_csv(buf, index=False)
        return buf.getvalue().encode(), f"{tag}.csv", "text/csv"


def _eval_summary(ev: dict | None) -> dict | None:
    if not ev or "schemes" not in ev:
        return None if not ev else {"skipped": ev.get("skipped"), "gates": ev.get("gates")}
    out = {"gates": ev["gates"], "schemes": {}}
    for sch, res in ev["schemes"].items():
        if "methods" not in res:
            continue
        out["schemes"][sch] = {
            "interval_coverage": res.get("interval_coverage"),
            "methods": {m: {"aoi_week": v["aoi_week"], "cell_week": v["cell_week"], "label": v["label"], "kind": v["kind"]} for m, v in res["methods"].items()},
        }
    return out


def _batch_summary(rep: dict | None) -> dict | None:
    if not rep:
        return None
    keys = (
        "status",
        "rows_read",
        "rows_valid",
        "rows_rejected",
        "inserted",
        "duplicates_in_file",
        "duplicates_existing",
        "conflicts_in_file",
        "conflicts_existing",
        "date_range",
        "platforms",
        "processing_streams",
        "message",
    )
    return {k: rep.get(k) for k in keys if k in rep}


# ---------------------------------------------------------------------- key figures helpers

def _check_basis(basis) -> str:
    if not isinstance(basis, str) or not (basis in ("auto", "harmonized") or (basis.startswith("platform:") and len(basis) > 9)):
        raise ServiceError("basis must be auto, harmonized or platform:<name>")
    return basis


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _shift_year(iso: str, year: int) -> str:
    m, d = int(iso[5:7]), int(iso[8:10])
    if m == 2 and d == 29:
        d = 28
    return f"{year:04d}-{m:02d}-{d:02d}"


def _covered_years(av: pd.DataFrame, plat: str, streams, p0: str, p1: str) -> set[int]:
    """Years whose [p0, p1] span (shifted to that year) lies inside a queried window."""
    sub = av[av["platform"] == plat]
    if streams:
        sub = sub[sub["processing_stream"].isin(streams)]
    out = set()
    if sub.empty:
        return out
    y0 = int(str(sub["date_start"].min())[:4])
    y1 = int(str(sub["date_end"].max())[:4])
    for y in range(y0, y1 + 1):
        a, b = _shift_year(p0, y), _shift_year(p1, y)
        if ((sub["date_start"].astype(str) <= a) & (sub["date_end"].astype(str) >= b)).any():
            out.add(y)
    return out


def key_figures_from_series(df: pd.DataFrame, p0: str, p1: str, cfg: dict) -> dict:
    """Period total vs the same dates in other years, busiest month, unusual
    weeks and the typical season, from a weekly activity series."""
    d = df.copy()
    d["year"] = d["window_start"].str.slice(0, 4).astype(int)
    d["md"] = d["window_start"].str.slice(5, 10)
    md0, md1 = p0[5:10], p1[5:10]
    year = int(p0[:4])
    in_span = (d["md"] >= md0) & (d["md"] <= md1)
    span = d[in_span]

    def year_total(g):
        complete = bool(len(g) and g["observed"].all() and g["value"].notna().all())
        return pd.Series({"total": g["value"].sum(min_count=1), "complete": complete, "weeks": len(g)})

    per_year = span.groupby("year").apply(year_total, include_groups=False) if len(span) else pd.DataFrame(columns=["total", "complete", "weeks"])
    cur = per_year.loc[year] if year in per_year.index else None
    others = per_year[(per_year.index != year) & per_year["complete"].astype(bool)] if len(per_year) else per_year
    total = {
        "period": [p0, p1],
        "value": float(cur["total"]) if cur is not None and pd.notna(cur["total"]) else None,
        "complete": bool(cur["complete"]) if cur is not None else False,
        "weeks": int(cur["weeks"]) if cur is not None else 0,
        "average": float(others["total"].mean()) if len(others) else None,
        "min": float(others["total"].min()) if len(others) else None,
        "max": float(others["total"].max()) if len(others) else None,
        "n_years": int(len(others)),
        "years": [int(y) for y in others.index],
        "pct_vs_average": None,
        "rank": None,
    }
    # interval for the period total (independent normal errors per week; same approximation as annual totals)
    cw = span[span["year"] == year]
    if len(cw) and cw["lo"].notna().all() and cw["hi"].notna().all():
        sd = ((cw["hi"] - cw["lo"]) / (2 * 1.645)).to_numpy(float)
        s = float(np.sqrt(np.sum(sd**2)))
        total["lo"], total["hi"] = max(0.0, total["value"] - 1.645 * s), total["value"] + 1.645 * s
    if total["complete"] and total["average"] and total["n_years"] >= 3:
        total["pct_vs_average"] = 100.0 * (total["value"] / total["average"] - 1.0)
        allv = list(others["total"]) + [total["value"]]
        total["rank"] = int(sorted(allv, reverse=True).index(total["value"]) + 1)
        total["rank_of"] = len(allv)

    # busiest month this period, and the typical busiest month over complete years
    cw = cw.assign(month=cw["window_start"].str.slice(5, 7).astype(int))
    busiest = None
    if len(cw) and cw["value"].notna().any():
        mm = cw.groupby("month")["value"].sum()
        busiest = {"month": int(mm.idxmax()), "name": MONTHS[int(mm.idxmax()) - 1], "value": float(mm.max())}
    yrs_ok = d.groupby("year")["observed"].all()
    full = d[d["year"].isin(yrs_ok[yrs_ok].index) & (d["year"] != year)]
    typical = None
    if len(full):
        full = full.assign(month=full["window_start"].str.slice(5, 7).astype(int))
        mm = full.groupby(["year", "month"])["value"].sum().groupby("month").mean()
        typical = {"month": int(mm.idxmax()), "name": MONTHS[int(mm.idxmax()) - 1], "n_years": int(full["year"].nunique())}

    # unusual weeks in the period (same FDR-controlled tests as the anomaly view)
    a = anom.analyse(df, cfg, view=(p0, p1))
    sig = [w["signal"] for w in a["windows"]]
    unusual = {
        "high": sum(s == "unusually_high" for s in sig),
        "low": sum(s == "unusually_low" for s in sig),
        "tested": sum(s in ("typical", "unusually_high", "unusually_low") for s in sig),
        "insufficient_history": sum(s == "insufficient_history" for s in sig),
        "n_tests": a["n_tests"],
        "low_testable": bool(df["coverage_known"].any()),
        "episodes": a["episodes"],
    }
    season = None
    if a["peak_season"]:
        wk = a["peak_season"]["weeks"]
        # contiguous run containing the peak week (seasons can wrap the new year)
        peak = a["peak_season"]["peak_week"]
        run = {peak}
        k = peak
        while (k - 1) % 52 in wk and len(run) < 52:
            k = (k - 1) % 52
            run.add(k)
        start = k
        k = peak
        while (k + 1) % 52 in wk and len(run) < 52:
            k = (k + 1) % 52
            run.add(k)
        season = {"start_week": int(start), "peak_week": int(peak), "end_week": int(k), "weeks": len(run),
                  "start_label": _week_label(int(start)), "peak_label": _week_label(int(peak)), "end_label": _week_label(int(k))}
    return {"year": year, "total": total, "busiest_month": busiest, "typical_busiest_month": typical, "unusual": unusual, "season": season}


def _week_label(k: int) -> str:
    d = pd.Timestamp("2001-01-01") + pd.Timedelta(days=7 * k)
    part = "early" if d.day <= 10 else ("mid" if d.day <= 20 else "late")
    return f"{part} {MONTHS[d.month - 1]}"


def summary_sentence(kf: dict, aoi_label: str | None) -> str:
    t = kf["total"]
    unit = kf.get("basis", {}).get("unit", "detections")
    p0, p1 = t["period"]
    a, b = pd.Timestamp(p0), pd.Timestamp(p1)
    span = f"{a.day} {MONTHS[a.month - 1][:3]}–{b.day} {MONTHS[b.month - 1][:3]} {b.year}"  # no %-d: not portable to Windows
    parts = []
    if t["value"] is None:
        parts.append(f"{span}: no observations in the series used here ({unit}). Choose another year or series.")
    else:
        s = f"{span}: {t['value']:,.0f} {unit}"
        if t["pct_vs_average"] is not None:
            direction = "above" if t["pct_vs_average"] >= 0 else "below"
            s += (
                f", {abs(t['pct_vs_average']):.0f}% {direction} the average for the same dates in {t['n_years']} other years"
                f" (rank {t['rank']} of {t['rank_of']})."
            )
        elif not t["complete"]:
            s += "; part of this period was not observed, so it is not compared with other years."
        else:
            s += f"; fewer than 3 other complete years ({t['n_years']}) are available for comparison."
        parts.append(s)
    if kf["busiest_month"]:
        b = kf["busiest_month"]["name"]
        typ = kf["typical_busiest_month"]
        parts.append(f"Busiest month: {b}" + (f" (usually {typ['name']})." if typ and typ["name"] != b else "."))
    u = kf["unusual"]
    if u["tested"]:
        if u["high"] or u["low"]:
            bits = []
            for n, word in ((u["high"], "high"), (u["low"], "low")):
                if n:
                    bits.append(f"{n} unusually {word} week{'s' if n != 1 else ''}")
            parts.append(f"{' and '.join(bits)} for the season (after controlling for multiple testing).")
        else:
            parts.append("No week was unusual for the season after controlling for multiple testing.")
    if kf["season"]:
        se = kf["season"]
        parts.append(f"Activity normally rises from {_week_label(se['start_week'])} and peaks in {_week_label(se['peak_week'])}.")
    return " ".join(parts)
