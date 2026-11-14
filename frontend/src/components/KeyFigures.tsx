import { fmt } from "../lib/format";
import { byPlatformOrder, PLATFORM_COLOR } from "../lib/scales";

interface Props {
  kf: any | null;
  loading: boolean;
  error: Error | null;
  year: number;
  years: number[];
  setYear: (y: number) => void;
  synthetic: boolean;
  onRetry: () => void;
}

function signed(p: number): string {
  const r = Math.round(p);
  return (r > 0 ? "+" : r < 0 ? "−" : "±") + Math.abs(r) + "%";
}

/** Headline numbers for one year of the current area, each compared with the
 *  same dates in the other years of the record. Text only (stat tiles); the
 *  only colour is the platform swatch beside each event count. */
export function KeyFigures({ kf, loading, error, year, years, setYear, synthetic, onRetry }: Props) {
  const stale = kf && kf.year !== year;
  return (
    <section className="keyfigs" aria-labelledby="kf-title" aria-busy={loading}>
      <div className="kf-head">
        <h2 id="kf-title">Key figures</h2>
        <label className="small">
          <span className="sr-only">Year</span>
          <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Year for key figures" style={{ width: 96 }}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
        {loading && <span className="small muted" role="status">{kf ? "Updating…" : "Computing key figures (the first time can take up to a minute)…"}</span>}
      </div>
      {error && !loading && (
        <div className="state error" style={{ padding: "6px 0" }}>
          {error.message} <button className="btn" onClick={onRetry}>Retry</button>
        </div>
      )}
      {kf && (
        <div style={{ opacity: stale ? 0.5 : 1 }}>
          <p className="kf-summary">{kf.summary}</p>
          <div className="kf-grid">
            <TotalTile kf={kf} />
            <div className="kf-tile">
              <div className="kf-label">Busiest month</div>
              <div className="kf-value">{kf.busiest_month ? kf.busiest_month.name : "–"}</div>
              <div className="kf-sub">
                {kf.typical_busiest_month
                  ? kf.busiest_month && kf.typical_busiest_month.name === kf.busiest_month.name
                    ? `As usual (${kf.typical_busiest_month.n_years}-year pattern)`
                    : `Usually ${kf.typical_busiest_month.name} (${kf.typical_busiest_month.n_years} complete years)`
                  : "No complete other years to compare"}
              </div>
            </div>
            <UnusualTile u={kf.unusual} />
            <div className="kf-tile">
              <div className="kf-label">Typical fire season</div>
              <div className="kf-value kf-value-s">{kf.season ? `${kf.season.start_label} → ${kf.season.end_label}` : "–"}</div>
              <div className="kf-sub">{kf.season ? `Usually peaks ${kf.season.peak_label}. From the other years, not a forecast.` : "Not enough history to describe a season."}</div>
            </div>
            <EventsTile events={kf.events} synthetic={synthetic} />
          </div>
        </div>
      )}
    </section>
  );
}

function TotalTile({ kf }: { kf: any }) {
  const t = kf.total;
  const [p0, p1] = t.period;
  const fullYear = p0.endsWith("-01-01") && p1.endsWith("-12-31");
  return (
    <div className="kf-tile kf-tile-wide">
      <div className="kf-label">{fullYear ? `Detections in ${kf.year}` : `Detections ${p0} to ${p1}`}</div>
      <div className="kf-value">
        {t.value === null ? "–" : fmt(t.value)}
        {t.pct_vs_average !== null && <span className="kf-delta"> {signed(t.pct_vs_average)}</span>}
      </div>
      <div className="kf-sub">
        {kf.basis.unit}
        {t.lo !== undefined && t.lo !== null ? ` (90% interval ${fmt(t.lo)}–${fmt(t.hi)})` : ""}.
      </div>
      <div className="kf-sub">
        {t.pct_vs_average !== null
          ? `vs average ${fmt(t.average)} for the same dates in ${t.n_years} other years · rank ${t.rank} of ${t.rank_of} (1 = most)`
          : !t.complete
            ? "Part of this period was not observed, so it is not compared with other years."
            : `Needs at least 3 other complete years to compare (${t.n_years} available).`}
      </div>
    </div>
  );
}

function UnusualTile({ u }: { u: any }) {
  return (
    <div className="kf-tile">
      <div className="kf-label">Unusual weeks</div>
      <div className="kf-value">
        {u.tested ? (
          <>
            {u.high} <span className="kf-unit">high</span> · {u.low} <span className="kf-unit">low</span>
          </>
        ) : "–"}
      </div>
      <div className="kf-sub">
        {u.tested
          ? `Of ${u.tested} weeks tested against the same week in other years, false discovery rate 5%.`
          : u.insufficient_history
            ? "Not enough other years to judge."
            : "No weeks could be tested."}
        {u.tested > 0 && !u.low_testable ? " Unusually low weeks need known coverage, which this data lacks." : ""}
      </div>
    </div>
  );
}

function EventsTile({ events, synthetic }: { events: any[]; synthetic: boolean }) {
  const link = events.length ? Array.from(new Set(events.map((e) => `${e.link_km} km (${e.instrument})`))).join(" or ") : "";
  return (
    <div className="kf-tile kf-tile-wide">
      <div className="kf-label">Estimated fire events</div>
      {events.length === 0 ? (
        <div className="kf-sub">No platform selected.</div>
      ) : (
        <table className="kf-events">
          <thead>
            <tr><th scope="col">Platform</th><th scope="col">Events</th><th scope="col">Other years’ average</th></tr>
          </thead>
          <tbody>
            {[...events].sort((a, b) => byPlatformOrder(a.platform, b.platform)).map((e) => (
              <tr key={e.platform}>
                <th scope="row"><span className="swatch" style={{ background: PLATFORM_COLOR[e.platform] }} aria-hidden="true" />{e.platform}</th>
                <td>{e.events === null ? <span className="muted" title="This platform did not cover the whole period">not covered</span> : fmt(e.events)}</td>
                <td>{e.average === null || !e.n_years ? "no other year" : `${fmt(e.average)} (${e.n_years} yr)`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="kf-sub">
        An estimate, not a count of reported fires: detections of one platform within {link} and one day of each other are grouped as one event.
        Each sensor sees fires differently, so compare a platform only with itself.
        {synthetic ? " In the synthetic demo, detections are scattered rather than spreading as real fires do, so events are close to detections." : ""}
      </div>
    </div>
  );
}
