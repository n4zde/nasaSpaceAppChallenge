import { useMemo, useState } from "react";
import type { UiState } from "./Rail";

type Role = "resp" | "fire" | "land" | "sci";
const ROLES: [Role, string][] = [["resp", "Emergency responder"], ["fire", "Firefighter"], ["land", "Land manager"], ["sci", "Scientist"]];
const LEVELS = ["Normal", "Watch", "Alert", "Critical"];
const ICONS = ["OK", "!", "!!", "!!!"];
const BG = ["#2e7d32", "#f2c744", "#e8730c", "#c62828"];
const FG = ["#fff", "#1b1f1a", "#fff", "#fff"];
const SEASON = ["Quiet", "Building", "Active", "Peak"];
const ACTIONS: Record<Role, string[]> = {
  resp: ["Routine monitoring.", "Check each new satellite pass and confirm mutual-aid contacts.", "Pre-position resources and brief duty officers.", "Activate surge plans and warn neighbouring districts."],
  fire: ["Normal readiness.", "Check crew rosters and equipment.", "Expect several simultaneous incidents; stage crews.", "Expect a high incident load; review shift cover under local protocol."],
  land: ["No change to burning plans.", "Review local burn rules before any burning.", "Defer planned burns and alert local farmers.", "Suspend planned burns and coordinate with authorities."],
  sci: ["Within the baseline range.", "Above the median; the baseline sample is small.", "At or above the 80th percentile of baseline weeks.", "Exceeds every baseline week; check observation coverage before concluding."],
};
const MIN = 5; // fewer detections than this are never flagged

const median = (a: number[]) => (a.length ? (a[(a.length - 1) >> 1] + a[a.length >> 1]) / 2 : 0);
const idxOf = (ws: string) => {
  const d = new Date(ws + "T00:00:00Z");
  return Math.min(51, Math.floor((+d - Date.UTC(d.getUTCFullYear(), 0, 1)) / 864e5 / 7));
};
function level(v: number, p: number[]): number {
  if (!p.length || v < MIN || v <= median(p)) return 0;
  if (v > p[p.length - 1]) return 3;
  return p.filter((x) => x <= v).length / p.length >= 0.8 ? 2 : 1;
}

interface Wk { ws: string; year: number; idx: number; v: number }
interface Props { rows: any[]; h: any | null; ui: UiState; selected: string | null; onPick: (ws: string) => void }

export function AlertsView({ rows, h, ui, selected, onPick }: Props) {
  const [role, setRole] = useState<Role>("resp");
  const [basis, setBasis] = useState("");
  const [yr, setYr] = useState<number | null>(null);

  const options: string[][] = [...(h ? [["harmonized", `Harmonised (${ui.reference} scale)`]] : []), ...ui.platforms.map((p) => [p, `${p} raw`])];
  const useBasis = options.some((o) => o[0] === basis) ? basis : options[0]?.[0];

  const weeks: Wk[] = useMemo(() => {
    let out: { ws: string; v: number }[] = [];
    if (useBasis === "harmonized" && h) {
      out = h.series
        .filter((r: any) => r.window_start >= ui.dateStart && r.window_start <= ui.dateEnd && r.harmonized != null)
        .map((r: any) => ({ ws: r.window_start, v: Math.round(r.harmonized) }));
    } else {
      out = rows.filter((r: any) => r.platform === useBasis && r.status !== "not_queried").map((r: any) => ({ ws: r.window_start, v: Number(r.n_quality ?? 0) }));
    }
    return out.map((o) => ({ ...o, year: Number(o.ws.slice(0, 4)), idx: idxOf(o.ws) })).sort((a, b) => a.ws.localeCompare(b.ws));
  }, [rows, h, useBasis, ui.dateStart, ui.dateEnd]);

  const years = useMemo(() => {
    const c: Record<number, number> = {};
    weeks.forEach((w) => { c[w.year] = (c[w.year] || 0) + 1; });
    return Object.keys(c).map(Number).filter((y) => c[y] >= 30).sort((a, b) => a - b);
  }, [weeks]);
  const cy = yr !== null && years.includes(yr) ? yr : years[years.length - 1];

  const pool = (idx: number, excl: number): number[] => {
    const out: number[] = [];
    for (const w of weeks) if (w.year !== excl && years.includes(w.year) && Math.abs(w.idx - idx) <= 2) out.push(w.v);
    return out.sort((a, b) => a - b);
  };
  const evalRows = useMemo(
    () => weeks.filter((w) => w.year === cy).map((w) => { const p = pool(w.idx, cy); return { ...w, p, L: level(w.v, p) }; }),
    [weeks, cy, years],
  );
  const season = useMemo(() => Array.from({ length: 52 }, (_, i) => { const p = pool(i, -1); return p.length ? p.reduce((s, x) => s + x, 0) / p.length : 0; }), [weeks, years]);
  const smax = Math.max(1, ...season);
  const cls = (x: number) => (x / smax >= 0.7 ? 3 : x / smax >= 0.35 ? 2 : x / smax >= 0.1 ? 1 : 0);

  if (years.length < 2) {
    return <div className="state">Alerts need at least two years of weekly data to build a baseline. Widen the date range in the left panel (Show all).</div>;
  }
  const cur = evalRows.find((r) => r.ws === selected) || evalRows[evalRows.length - 1];
  const alerts = evalRows.filter((r) => r.L >= 2);
  const roleLabel = ROLES.find((r) => r[0] === role)![1];

  return (
    <div>
      <div className="note warn small">
        Alerts compare each week with the same season in the other years of the selected range. They are historical-pattern signals, not a fire-risk forecast.
        The data have no observation-coverage information, so this is a research estimate.
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", margin: "12px 0" }}>
        {ROLES.map(([k, l]) => (
          <button key={k} className="btn" aria-pressed={role === k} onClick={() => setRole(k)} style={role === k ? { outline: "2px solid var(--ink)", fontWeight: 700 } : undefined}>{l}</button>
        ))}
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <select aria-label="Series used for alerts" value={useBasis} onChange={(e) => setBasis(e.target.value)} style={{ width: 240 }}>
          {options.map((o) => <option key={o[0]} value={o[0]}>{o[1]}</option>)}
        </select>
        <select aria-label="Year to check" value={cy} onChange={(e) => setYr(Number(e.target.value))} style={{ width: 160 }}>
          {years.map((y) => <option key={y} value={y}>Check {y}</option>)}
        </select>
        <span className="small muted">{ui.aoiLabel}. Baseline: the other {years.length - 1} year(s) with data ({years.filter((y) => y !== cy).join(", ")}).</span>
      </div>

      {cur && (
        <>
          <div role="status" style={{ background: BG[cur.L], color: FG[cur.L], padding: 16, borderRadius: 10, display: "flex", gap: 14, alignItems: "center" }}>
            <b style={{ fontSize: 28, minWidth: 56, textAlign: "center" }}>{ICONS[cur.L]}</b>
            <div>
              <div style={{ fontSize: 24, fontWeight: 800 }}>{LEVELS[cur.L]}</div>
              <div>
                Week of {cur.ws}: <b>{cur.v}</b> detections vs typical {median(cur.p)} (range {cur.p[0]} to {cur.p[cur.p.length - 1]}).
                {cur.v < MIN ? " Too few detections to flag." : ""}
              </div>
            </div>
          </div>
          <p><b>{roleLabel}:</b> {ACTIONS[role][cur.L]}</p>
          <p className="small">
            <b>Next 4 weeks, typical:</b>{" "}
            {[1, 2, 3, 4].map((k) => { const i = (cur.idx + k) % 52; return `week ${i + 1} ${SEASON[cls(season[i])]} (about ${Math.round(season[i])})`; }).join("; ")}.
          </p>
        </>
      )}

      <h3>Alert log for {cy}</h3>
      <p className="small muted">{alerts.length} Alert or Critical week(s) out of {evalRows.length}. Click a week to show it on the calendar and map.</p>
      {alerts.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th align="left">Week of</th><th align="left">Level</th><th align="left">Count vs typical</th><th align="left">Suggested action ({roleLabel})</th></tr></thead>
            <tbody>
              {alerts.slice(0, 40).map((r) => (
                <tr key={r.ws} style={{ borderTop: "1px solid var(--rule)" }}>
                  <td><button className="btn" onClick={() => onPick(r.ws)}>{r.ws}</button></td>
                  <td><b>{ICONS[r.L]} {LEVELS[r.L]}</b></td>
                  <td>{r.v} vs {median(r.p)}</td>
                  <td>{ACTIONS[role][r.L]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {role === "sci" && cur && (
        <p className="small muted">
          Method: baseline = weekly values from the other years, same week of year plus or minus 2 weeks ({cur.p.length} values here). Watch: above the median.
          Alert: at or above the 80th percentile. Critical: above every baseline week. Weeks with fewer than {MIN} detections are never flagged.
          Few baseline years make these thresholds provisional. Sensor drift or import differences between years can look like unusual activity.
        </p>
      )}
    </div>
  );
}