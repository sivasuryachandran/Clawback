"use client";
/**
 * Under the hood (Teammate C; section 3.10): one scrolling page for judges. Every technical detail,
 * grouped in panels, each captioned with the sponsor it shows. READ-ONLY: it only ever sends GETs.
 * If a panel's query fails, that panel says "Unavailable" and the rest still load.
 *
 *   /under-the-hood          live data from GET /debug/overview
 *   /under-the-hood?mock=1   labelled SAMPLE data (development only)
 */
import { useCallback, useEffect, useRef, useState } from "react";
import "./under-the-hood.css";
import type { Overview } from "./types";
import {
  AnalyticsPanel, ApprovalsPanel, GateTrace, IngestPanel, LiveQueriesPanel,
  MatchingPanel, PactPanel, PolicyPanel, SafetyPanel, TestsPanel, fmtTime,
} from "./panels";

const API: string = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const REFRESH_MS = 5000;

export default function UnderTheHood() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);
  const [ready, setReady] = useState(false);
  const [mock, setMock] = useState(false);
  const inFlight = useRef(false);

  useEffect(() => {
    setMock(new URLSearchParams(window.location.search).get("mock") === "1");
    setReady(true);
  }, []);

  const load = useCallback(async () => {
    if (inFlight.current) return;               // never stack requests if the API is slow
    inFlight.current = true;
    try {
      const qs = new URLSearchParams();
      if (mock) qs.set("mock", "1");
      const r = await fetch(`${API}/debug/overview?${qs}`, { cache: "no-store" });
      if (r.status === 403) {
        const j = (await r.json().catch(() => ({}))) as { detail?: string };
        setData(null);
        setError(j.detail ?? "Under the hood is only available on the demo laptop.");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData((await r.json()) as Overview);
      setError(null);
    } catch (e) {
      setError(`Can't reach the API at ${API} (${e instanceof Error ? e.message : String(e)}). Is the backend running?`);
    } finally {
      inFlight.current = false;
    }
  }, [mock]);

  useEffect(() => { if (ready) void load(); }, [ready, load]);
  useEffect(() => {
    if (!ready || !auto) return;
    const id = setInterval(() => { if (document.visibilityState === "visible") void load(); }, REFRESH_MS);
    return () => clearInterval(id);
  }, [ready, auto, load]);

  return (
    <div className="uh">
      <main className="uh-wrap">
        <div className="uh-top">
          <div>
            <a className="uh-back" href="/">← Back to Clawback</a>
            <h1>Under the hood</h1>
            <p className="uh-sub">How Clawback decides, step by step. Read-only: nothing on this page changes anything.</p>
          </div>
          <div className="uh-controls">
            <label><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Refresh every {REFRESH_MS / 1000}s</label>
            <button type="button" className="uh-btn" onClick={() => void load()}>Refresh now</button>
            <span aria-live="polite">{data ? `Updated ${fmtTime(data.generated_at)}` : "Loading…"}</span>
          </div>
        </div>

        {data?.sample && <p className="uh-banner" role="alert">{data.sample_note ?? "SAMPLE DATA: nothing below is from the live system."}</p>}
        {error && <div className="uh-error" role="alert">{error}</div>}

        {data && (
          <div className="uh-grid">
            <GateTrace data={data.gate_trace} />
            <PactPanel data={data.pact} />
            <div className="uh-grid two">
              <ApprovalsPanel data={data.approvals_once} />
              <PolicyPanel data={data.policy} />
            </div>
            <MatchingPanel data={data.matching} api={API} mock={mock} />
            <LiveQueriesPanel data={data.live_queries} api={API} mock={mock} />
            <IngestPanel data={data.ingest} />
            <div className="uh-grid two">
              <SafetyPanel data={data.safety} />
              <div className="uh-grid">
                <AnalyticsPanel data={data.analytics} />
                <TestsPanel data={data.tests} />
              </div>
            </div>
          </div>
        )}

        {data && (
          <p className="uh-foot">
            Mounted routers: {Object.entries(data.routers).map(([k, v]) => `${k} (${v.startsWith("mounted") ? "ok" : v})`).join(" · ") || "unknown"}
          </p>
        )}
      </main>
    </div>
  );
}
