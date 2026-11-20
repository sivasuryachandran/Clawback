"use client";
// The ten Under the hood panels (section 3.10). Read-only: nothing here sends anything but GETs.
import { Fragment, ReactNode, useEffect, useState } from "react";
import type { Candidate, Decision, Overview, PanelMeta, PactRow, PactVersion, QueryResult, Row } from "./types";

/* ---------- small helpers ---------- */

export function fmtTime(v: unknown): string {
  if (typeof v !== "string" || !v) return "";
  const iso = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(v) ? v.replace(" ", "T") + "Z" : v;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? v : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const TIMEY = /(_at|^ts$|^time$|^at$)$/;

/** Date and time: for things where the day matters (Persona freshness is counted in days). */
export function fmtDateTime(v: unknown): string {
  if (typeof v !== "string" || !v) return "";
  const iso = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(v) ? v.replace(" ", "T") + "Z" : v;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? v : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function Pill({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`uh-pill ${kind}`}>{children}</span>;
}

export function decisionPill(d: string) {
  const k = d === "ALLOW" ? "allow" : d === "ASK" ? "ask" : d === "BLOCK" ? "block" : "info";
  return <Pill kind={k}>{d}</Pill>;
}

export function Check({ ok }: { ok: boolean | null | undefined }) {
  if (ok === null || ok === undefined) return <span className="uh-check unk" title="no verifier available" aria-label="unknown">?</span>;
  return ok
    ? <span className="uh-check ok" aria-label="signature valid" title="signature valid">✓</span>
    : <span className="uh-check bad" aria-label="signature INVALID" title="signature INVALID">✗</span>;
}

export function Panel({ title, caption, meta, children }: { title: string; caption: string; meta?: PanelMeta; children: ReactNode }) {
  const bad = meta && meta.available === false;
  return (
    <section className="uh-panel" aria-labelledby={`p-${title.replace(/\W+/g, "-")}`}>
      <header>
        <h2 id={`p-${title.replace(/\W+/g, "-")}`}>{title}</h2>
        <p className="uh-caption">{caption}</p>
      </header>
      {bad ? (
        <p className="uh-unavail" role="status">Unavailable<small>{meta?.error}</small></p>
      ) : children}
    </section>
  );
}

export function Table({ rows, cols, empty = "Nothing yet.", mono }: { rows?: Row[]; cols?: string[]; empty?: string; mono?: string[] }) {
  if (!rows || rows.length === 0) return <p className="uh-empty">{empty}</p>;
  const columns = cols ?? Object.keys(rows[0]);
  return (
    <div className="uh-scroll">
      <table>
        <thead><tr>{columns.map((c) => <th key={c} scope="col">{c.replace(/_/g, " ")}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={(mono ?? []).includes(c) ? "m" : undefined}>
                  {TIMEY.test(c) ? fmtTime(r[c]) : cell(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- 1. Live gate trace ---------- */

export function GateTrace({ data }: { data: Overview["gate_trace"] }) {
  const [open, setOpen] = useState<number | null>(null);
  const rows: Decision[] = data.decisions ?? [];
  return (
    <Panel title="Live gate trace" caption="Guild · The Pact: the last 10 decisions. Click one to see every rule in order, with the values compared." meta={data}>
      {rows.length === 0 ? <p className="uh-empty">No decisions yet.</p> : (
        <div className="uh-scroll">
          <table>
            <thead><tr><th scope="col">time</th><th scope="col">item</th><th scope="col">action</th><th scope="col">decision</th><th scope="col">failed rule</th><th scope="col">proposal hash</th></tr></thead>
            <tbody>
              {rows.map((d) => {
                const isOpen = open === d.audit_id;
                return (
                  <Fragment key={d.audit_id}>
                    <tr>
                      <td className="m">{fmtTime(d.ts)}</td>
                      <td>
                        <button type="button" className="uh-row-btn" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : d.audit_id)}>
                          {isOpen ? "▾" : "▸"} {d.item ?? "(no item)"}
                        </button>
                      </td>
                      <td className="m">{d.action_type ?? ""}</td>
                      <td>{decisionPill(d.decision)}</td>
                      <td className="m">{d.failed_rule ?? "none"}</td>
                      <td className="m">{d.short_hash ?? ""}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={6}>
                          <ol className="uh-rules" aria-label="Rules in the order they were checked">
                            {d.rules.map((r, i) => (
                              <li key={i}>
                                <span className="uh-mono">{r.rule}</span>
                                <Pill kind={r.pass ? "ok" : "stop"}>{r.pass ? "PASS" : "BLOCK"}</Pill>
                                <span>
                                  {r.why && r.why !== "ok" ? <span>{r.why} </span> : null}
                                  <span className="vals">{r.values && Object.keys(r.values).length ? JSON.stringify(r.values) : ""}</span>
                                </span>
                              </li>
                            ))}
                          </ol>
                          <dl className="uh-kv">
                            <dt>Pact</dt><dd className="uh-mono">{d.pact_id} v{d.pact_version}</dd>
                            <dt>Proposal</dt><dd className="uh-mono">{d.proposal_id} · {d.proposal_hash}</dd>
                            {d.action_result ? (<><dt>Result</dt><dd className="uh-mono">{JSON.stringify(d.action_result)}</dd></>) : null}
                          </dl>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/* ---------- 2. Your Pact ---------- */

function PactBlock({ pact }: { pact: PactRow }) {
  // Follow the newest version (a re-signed Pact must appear on its own during the "change one rule" stunt)
  // unless the viewer has explicitly picked an older one.
  const [picked, setPicked] = useState<number | null>(null);
  const sel = picked !== null && pact.versions.some((x) => x.version === picked) ? picked : pact.latest_version;
  const v: PactVersion | undefined = pact.versions.find((x) => x.version === sel) ?? pact.versions[0];
  const o = pact.owner;
  return (
    <div style={{ marginBottom: 14 }}>
      <dl className="uh-kv">
        <dt>Pact</dt><dd className="uh-mono">{pact.pact_id}</dd>
        <dt>Owner</dt><dd>{o.name ?? o.id} <span className="uh-mono">({o.id})</span></dd>
        <dt>Persona</dt><dd><Pill kind={o.persona_status === "approved" ? "ok" : "ask"}>{o.persona_status ?? "none"}</Pill>{" "}
          <span className="uh-mono">verified {fmtDateTime(o.verified_at) || "never"} · re-checked {fmtDateTime(o.reverified_at) || "never"}{o.persona_inquiry_id ? ` · ${o.persona_inquiry_id}` : ""}</span></dd>
      </dl>
      <div className="uh-tabs" role="tablist" aria-label="Pact versions">
        {pact.versions.map((x) => (
          <button key={x.version} type="button" role="tab" aria-selected={x.version === sel}
            className={`uh-btn ${x.version === sel ? "on" : ""}`} onClick={() => setPicked(x.version === pact.latest_version ? null : x.version)}>
            v{x.version} <Check ok={x.signature_ok} />{x.is_latest ? " latest" : ""}{x.revoked ? " · paused" : ""}
          </button>
        ))}
      </div>
      {v && (
        <>
          <dl className="uh-kv">
            <dt>Signature (recomputed now)</dt><dd><Check ok={v.signature_ok} />{" "}
              {v.signature_ok === true ? "hash and signature check out" : v.signature_ok === false ? "INVALID: the saved text no longer matches its signature, so the gate would stop on R1" : "no verifier available"}</dd>
            <dt>Hash</dt><dd className="uh-mono">{v.hash}</dd>
            <dt>Created</dt><dd className="uh-mono">{fmtDateTime(v.created_at)}</dd>
            <dt>Status column</dt><dd className="uh-mono">{v.status_column} <span style={{ color: "var(--uh-mute)", fontFamily: "system-ui" }}>(display only; the gate never reads it)</span></dd>
          </dl>
          <pre aria-label={`Signed text of version ${v.version}`}>{v.pact_text}</pre>
        </>
      )}
    </div>
  );
}

export function PactPanel({ data }: { data: Overview["pact"] }) {
  const revs = data.revocations ?? [];
  return (
    <Panel title="Your Pact" caption="Persona · The Pact: the signed text, a live signature check (✓ / ✗), versions, signed pause records and Persona status." meta={data}>
      {(data.pacts ?? []).length === 0 ? <p className="uh-empty">No Pact signed yet.</p> : (data.pacts ?? []).map((p) => <PactBlock key={p.pact_id} pact={p} />)}
      <h3 style={{ fontSize: 15, margin: "14px 0 4px" }}>Pause records (signed, insert-only)</h3>
      {revs.length === 0 ? <p className="uh-empty">None. This Pact has not been paused or replaced.</p> : (
        <div className="uh-scroll"><table>
          <thead><tr><th scope="col">pact</th><th scope="col">version</th><th scope="col">when</th><th scope="col">reason</th><th scope="col">signature</th></tr></thead>
          <tbody>{revs.map((r, i) => (
            <tr key={i}><td className="m">{r.pact_id}</td><td>v{r.version}</td><td className="m">{fmtDateTime(r.revoked_at)}</td><td>{r.reason}</td><td><Check ok={r.signature_ok} /></td></tr>
          ))}</tbody></table></div>
      )}
      <p className="uh-caption">Verifier: {data.verifier}{data.verifier_found === false ? " (not found, so signatures show ?)" : ""}</p>
    </Panel>
  );
}

/* ---------- 3. Approvals and run-once ---------- */

export function ApprovalsPanel({ data }: { data: Overview["approvals_once"] }) {
  return (
    <Panel title="Approvals and run-once" caption="The Pact: each approval is bound to one proposal hash; each action runs once." meta={data}>
      <h3 style={{ fontSize: 15, margin: "4px 0" }}>Approvals</h3>
      <Table rows={data.approvals} cols={["id", "status", "amount_usd", "expires_at", "proposal_hash"]} mono={["proposal_hash"]} empty="No approvals yet." />
      <h3 style={{ fontSize: 15, margin: "14px 0 4px" }}>Executed actions (run-once guard)</h3>
      <Table rows={data.executed} cols={["action_type", "status", "amount_usd", "proposal_hash", "result"]} mono={["proposal_hash"]} empty="Nothing has run yet." />
      <p className="uh-caption">{data.note}</p>
    </Panel>
  );
}

/* ---------- 4. Policy (Senso) ---------- */

export function PolicyPanel({ data }: { data: Overview["policy"] }) {
  return (
    <Panel title="Policy" caption="Senso: every store-policy question, the sentence quoted back, its source and revision." meta={data}>
      <Table rows={data.checks} cols={["store", "question", "cited_text", "source_url", "senso_source_id", "revision", "fetched_at"]} mono={["source_url", "senso_source_id", "revision"]} empty="No policy checks yet." />
    </Panel>
  );
}

/* ---------- 5. Matching (SingleStore + SIE) ---------- */

type CandidatesResult = PanelMeta & { candidates?: Candidate[]; note?: string };

export function MatchingPanel({ data, api, mock }: { data: Overview["matching"]; api: string; mock: boolean }) {
  // The overview only lists the purchases and the SQL. Running the matcher costs a SIE embed + rerank
  // request, so candidates are fetched here, once on load and when you pick another purchase (never polled).
  const [sel, setSel] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const [res, setRes] = useState<CandidatesResult | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = sel ?? data.line_item_id ?? null;

  useEffect(() => {
    if (chosen === null) return;
    let live = true;
    setBusy(true);
    const qs = new URLSearchParams({ line_item_id: String(chosen) });
    if (mock) qs.set("mock", "1");
    fetch(`${api}/debug/matching?${qs}`, { cache: "no-store" })
      .then((r) => r.json() as Promise<CandidatesResult>)
      .then((j) => { if (live) setRes(j); })
      .catch((e) => { if (live) setRes({ available: false, error: String(e) }); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [chosen, api, mock, nonce]);

  const cands: Candidate[] = res?.candidates ?? [];
  return (
    <Panel title="Matching" caption="SingleStore + Superlinked: top candidates with vector similarity, SIE rerank score and the exact-match flag, plus the SQL." meta={data}>
      <div className="uh-form">
        <label htmlFor="uh-item">Purchase</label>
        <select id="uh-item" value={chosen ?? ""} onChange={(e) => setSel(Number(e.target.value))}>
          {(data.items ?? []).map((it) => (
            <option key={String(it.id)} value={Number(it.id)}>{String(it.title)} ({String(it.variant ?? "")}) · {String(it.store)}</option>
          ))}
        </select>
        <button type="button" className="uh-btn" disabled={busy} onClick={() => setNonce((n) => n + 1)}>{busy ? "Matching…" : "Run the matcher again"}</button>
      </div>
      {res?.available === false ? (
        <p className="uh-unavail" role="status">Unavailable<small>{res.error}</small></p>
      ) : cands.length === 0 ? <p className="uh-empty">{busy ? "Running the matcher…" : "No candidates."}</p> : (
        <div className="uh-scroll"><table>
          <thead><tr><th scope="col">store</th><th scope="col">item</th><th scope="col">similarity</th><th scope="col">rerank</th><th scope="col">exact match</th></tr></thead>
          <tbody>{cands.map((c, i) => (
            <tr key={i}>
              <td className="m">{c.store}</td>
              <td>{c.title} {c.variant ? `(${c.variant})` : ""} <span className="uh-mono">{c.sku}</span></td>
              <td className="m">{c.similarity?.toFixed(3)}</td><td className="m">{c.rerank_score?.toFixed(3)}</td>
              <td><Pill kind={c.exact ? "ok" : "stop"}>{c.exact ? "exact" : "not exact"}</Pill></td>
            </tr>))}</tbody></table></div>
      )}
      <pre aria-label="Matcher SQL">{data.sql}</pre>
      <p className="uh-caption">{data.sql_source}{res?.note ? ` · ${res.note}` : ""}</p>
    </Panel>
  );
}

/* ---------- 6. Live queries (SingleStore) ---------- */

export function LiveQueriesPanel({ data, api, mock }: { data: Overview["live_queries"]; api: string; mock: boolean }) {
  const [res, setRes] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [item, setItem] = useState("1");
  async function run(key: string, needsItem: boolean) {
    setBusy(key);
    try {
      const qs = new URLSearchParams();
      if (mock) qs.set("mock", "1");
      if (needsItem && item) qs.set("item", item);
      const r = await fetch(`${api}/debug/query/${key}?${qs}`, { cache: "no-store" });
      setRes((await r.json()) as QueryResult);
    } catch (e) {
      setRes({ key, label: key, sql: "", params: [], rows: [], error: String(e) });
    } finally { setBusy(null); }
  }
  return (
    <Panel title="Live queries" caption="SingleStore: saved read-only queries, run live against the database." meta={data}>
      <div className="uh-form">
        {(data.queries ?? []).map((q) => (
          <button key={q.key} type="button" className="uh-btn" disabled={busy !== null} onClick={() => run(q.key, q.needs_item)}>
            {busy === q.key ? "Running…" : q.label}
          </button>
        ))}
        <label htmlFor="uh-listing" className="uh-caption">Listing id (price history)</label>
        <input id="uh-listing" type="number" min={1} value={item} onChange={(e) => setItem(e.target.value)} style={{ width: 80 }} />
      </div>
      {res && (
        <div role="status">
          <p style={{ margin: "8px 0 0", fontWeight: 600 }}>{res.label} {res.sample ? <Pill kind="ask">SAMPLE</Pill> : null}</p>
          <pre aria-label="SQL">{res.sql}{res.params?.length ? `\n-- params: ${JSON.stringify(res.params)}` : ""}</pre>
          {res.error ? <p className="uh-unavail">Unavailable<small>{res.error}</small></p> : (
            <>
              <Table rows={res.rows} empty="The query returned no rows." mono={["ts", "seen_at"]} />
              <p className="uh-caption">{res.rows.length} row{res.rows.length === 1 ? "" : "s"} · {res.took_ms} ms</p>
            </>
          )}
        </div>
      )}
    </Panel>
  );
}

/* ---------- 7. Ingest (Airbyte + Arcee) ---------- */

export function IngestPanel({ data }: { data: Overview["ingest"] }) {
  return (
    <Panel title="Ingest" caption="Airbyte + Arcee: receipts pulled through the Airbyte Gmail connector, parse confidence, model, tokens, time." meta={data}>
      <h3 style={{ fontSize: 15, margin: "4px 0" }}>Receipts</h3>
      <Table rows={data.emails} cols={["id", "from_addr", "subject", "received_at", "parsed"]} empty="No receipts ingested yet." />
      <h3 style={{ fontSize: 15, margin: "14px 0 4px" }}>What was parsed</h3>
      <Table rows={data.line_items} cols={["store", "order_no", "title", "variant", "price_paid", "parse_confidence"]} empty="Nothing parsed yet." />
      <h3 style={{ fontSize: 15, margin: "14px 0 4px" }}>Parser runs</h3>
      <Table rows={(data.parse_events ?? []).map((e) => ({ at: e.at, model: e.props.model as string, tokens: e.props.tokens as number, latency_ms: e.props.latency_ms as number, confidence: e.props.confidence as number, cost_usd: e.props.cost_usd as number }))}
        empty="No parser runs reported since the server started." />
      <p className="uh-caption">{data.avg_cost_per_receipt_usd != null ? `Average cost per receipt: $${data.avg_cost_per_receipt_usd}` : "Cost per receipt: not reported yet"} · {data.note}</p>
    </Panel>
  );
}

/* ---------- 8. Safety ---------- */

export function SafetyPanel({ data }: { data: Overview["safety"] }) {
  return (
    <Panel title="Safety" caption="The Pact: demo mode, who may be emailed, and the last 20 outgoing messages." meta={data}>
      <dl className="uh-kv">
        <dt>Demo mode</dt><dd><Pill kind={data.demo_mode ? "ok" : "bad"}>{data.demo_mode ? "ON" : "OFF"}</Pill>{" "}
          <span className="uh-mono">DEMO_MODE={JSON.stringify(data.demo_mode_raw ?? "")}</span></dd>
        <dt>Allowed recipients</dt><dd className="uh-mono">{(data.allowed_recipients ?? []).length ? (data.allowed_recipients ?? []).join(", ") : "none set: nothing can be sent"}</dd>
        <dt>Support inbox</dt><dd className="uh-mono">{data.support_inbox ?? "not set"}</dd>
      </dl>
      {data.messages_error ? <p className="uh-unavail">Messages unavailable<small>{data.messages_error}</small></p>
        : <Table rows={data.messages} cols={["sent_at", "to_addr", "subject", "thread_id", "direction"]} mono={["thread_id"]} empty="No messages sent." />}
    </Panel>
  );
}

/* ---------- 9. Analytics ---------- */

export function AnalyticsPanel({ data }: { data: Overview["analytics"] }) {
  const s = data.status;
  const counts = Object.entries(data.today_counts ?? {});
  return (
    <Panel title="Analytics" caption="Amplitude: dashboard link and today's event counts." meta={data}>
      <dl className="uh-kv">
        <dt>Amplitude</dt><dd>{s?.enabled ? <Pill kind="ok">sending</Pill> : <Pill kind="ask">no API key: counting locally only</Pill>}{" "}
          <span className="uh-mono">sent {s?.sent ?? 0} · failed {s?.failed ?? 0}{s?.last_error ? ` · ${s.last_error}` : ""}</span></dd>
        <dt>Dashboard</dt><dd>{data.dashboard_url ? <a href={data.dashboard_url} target="_blank" rel="noreferrer noopener">Open the Amplitude dashboard</a> : <span className="uh-mono">AMPLITUDE_DASHBOARD_URL not set</span>}</dd>
      </dl>
      {counts.length === 0 ? <p className="uh-empty">No events yet today.</p> : (
        <div className="uh-scroll"><table><thead><tr><th scope="col">event</th><th scope="col">today</th></tr></thead>
          <tbody>{counts.map(([k, n]) => (<tr key={k}><td className="m">{k}</td><td>{n}</td></tr>))}</tbody></table></div>
      )}
    </Panel>
  );
}

/* ---------- 10. Tests ---------- */

export function TestsPanel({ data }: { data: Overview["tests"] }) {
  return (
    <Panel title="Tests" caption="The last pytest -q run, written to a file by the test run." meta={data}>
      {data.ran ? (
        <>
          <p className="uh-big">{data.passed} passed{data.failed ? `, ${data.failed} failed` : ""}</p>
          <p className="uh-caption">{data.duration_s}s · finished {fmtTime(data.finished_at)}</p>
        </>
      ) : <p className="uh-empty">{data.note}</p>}
    </Panel>
  );
}
