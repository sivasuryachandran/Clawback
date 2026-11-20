/* Under the hood (Teammate C; section 3.10): one scrolling page for judges. Every technical detail, grouped in panels,
 * each captioned with the sponsor it shows. READ-ONLY: it only ever sends GETs.
 * If a panel's query fails, that panel says "Unavailable" and the rest still load.
 *
 * Served by the API itself at /under-the-hood/ (same origin: no CORS, no port to configure). No build step.
 *   /under-the-hood/            live data from GET /debug/overview
 *   /under-the-hood/?mock=1     labelled SAMPLE data (development only)
 *   /under-the-hood/?api=URL    read from another API origin
 * Everything from the database or from an email is written with textContent, never innerHTML.
 */
(function () {
  "use strict";
  var qs = new URLSearchParams(location.search);
  var MOCK = qs.get("mock") === "1";
  var API = (qs.get("api") || location.origin).replace(/\/$/, "");
  var HOME = qs.get("home") || (location.protocol + "//" + location.hostname + ":3000/");
  var REFRESH_MS = 5000;

  var S = {
    data: null, error: null, auto: true,
    open: {},            // gate-trace rows expanded, by audit id
    picked: {},          // Pact version the viewer picked, by pact id (absent = follow the newest)
    matchSel: null, match: null, matchBusy: false, matchGen: 0,
    listingId: "1", liveRes: null, liveBusy: null,
  };
  var root = document.getElementById("root");
  var inFlight = false;

  /* ---------- tiny DOM helper ---------- */
  function h(tag, props) {
    var el = document.createElement(tag);
    var kids = Array.prototype.slice.call(arguments, 2);
    for (var k in props || {}) {
      var v = props[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else if (k === "checked") el.checked = !!v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    kids.forEach(function add(c) {
      if (c === null || c === undefined || c === false) return;
      if (Array.isArray(c)) { c.forEach(add); return; }
      el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
    });
    return el;
  }

  /* ---------- formatting ---------- */
  function toDate(v) {
    if (typeof v !== "string" || !v) return null;
    var iso = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(v) ? v.replace(" ", "T") + "Z" : v;
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }
  function fmtTime(v) {
    var d = toDate(v);
    return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : (v || "");
  }
  function fmtDT(v) {  // date + time: for things where the day matters (Persona freshness is counted in days)
    var d = toDate(v);
    return d ? d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : (v || "");
  }
  function cell(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }
  var TIMEY = /(_at|^ts$|^time$|^at$)$/;
  var WIDE = /^(why|cited_text|question|answer|result|subject|title|offered_title)$/;   // sentences get room instead of a sliver

  function pill(kind, text) { return h("span", { class: "uh-pill " + kind }, text); }
  function decisionPill(d) {
    return pill(d === "ALLOW" ? "allow" : d === "ASK" ? "ask" : d === "BLOCK" ? "block" : "info", d);
  }
  function check(ok) {
    if (ok === null || ok === undefined) return h("span", { class: "uh-check unk", title: "no verifier available", "aria-label": "unknown" }, "?");
    return ok ? h("span", { class: "uh-check ok", title: "signature valid", "aria-label": "signature valid" }, "\u2713")
              : h("span", { class: "uh-check bad", title: "signature INVALID", "aria-label": "signature INVALID" }, "\u2717");
  }

  function panel(title, caption, meta, body) {
    var id = "p-" + title.replace(/\W+/g, "-");
    var bad = meta && meta.available === false;
    return h("section", { class: "uh-panel", "aria-labelledby": id },
      h("header", null, h("h2", { id: id }, title), h("p", { class: "uh-caption" }, caption)),
      bad ? h("p", { class: "uh-unavail", role: "status" }, "Unavailable", h("small", null, meta.error || "")) : body());
  }

  function table(rows, cols, opts) {
    opts = opts || {};
    if (!rows || !rows.length) return h("p", { class: "uh-empty" }, opts.empty || "Nothing yet.");
    cols = cols || Object.keys(rows[0]);
    var mono = opts.mono || [];
    return h("div", { class: "uh-scroll" }, h("table", null,
      h("thead", null, h("tr", null, cols.map(function (c) { return h("th", { scope: "col" }, c.replace(/_/g, " ")); }))),
      h("tbody", null, rows.map(function (r) {
        return h("tr", null, cols.map(function (c) {
          var isTime = TIMEY.test(c);
          return h("td", { class: isTime ? "m t" : WIDE.test(c) ? "w" : mono.indexOf(c) >= 0 ? "m" : null }, isTime ? fmtTime(r[c]) : cell(r[c]));
        }));
      }))));
  }

  /* ---------- 1. Live gate trace ---------- */
  function gateTrace(d) {
    return panel("Live gate trace", "Guild \u00b7 The Pact: the last 10 decisions. Click one to see every rule in order, with the values compared.", d, function () {
      var rows = d.decisions || [];
      if (!rows.length) return h("p", { class: "uh-empty" }, "No decisions yet.");
      var body = [];
      rows.forEach(function (x) {
        var isOpen = !!S.open[x.audit_id];
        body.push(h("tr", null,
          h("td", { class: "m" }, fmtTime(x.ts)),
          h("td", null, h("button", { type: "button", class: "uh-row-btn", "aria-expanded": String(isOpen),
            onclick: function () { S.open[x.audit_id] = !isOpen; render(true); } }, (isOpen ? "\u25be " : "\u25b8 ") + (x.item || "(no item)"))),
          h("td", { class: "m" }, x.action_type || ""), h("td", null, decisionPill(x.decision)),
          h("td", { class: "m" }, x.failed_rule || "none"), h("td", { class: "m" }, x.short_hash || "")));
        if (isOpen) {
          body.push(h("tr", null, h("td", { colspan: "6" },
            h("ol", { class: "uh-rules", "aria-label": "Rules in the order they were checked" }, (x.rules || []).map(function (r) {
              return h("li", null, h("span", { class: "uh-mono" }, r.rule), pill(r.pass ? "ok" : "stop", r.pass ? "PASS" : "BLOCK"),
                h("span", null, r.why && r.why !== "ok" ? r.why + " " : "",
                  h("span", { class: "vals" }, r.values && Object.keys(r.values).length ? JSON.stringify(r.values) : "")));
            })),
            h("dl", { class: "uh-kv" },
              h("dt", null, "Pact"), h("dd", { class: "uh-mono" }, (x.pact_id || "") + " v" + (x.pact_version == null ? "" : x.pact_version)),
              h("dt", null, "Proposal"), h("dd", { class: "uh-mono" }, (x.proposal_id || "") + " \u00b7 " + (x.proposal_hash || "")),
              x.action_result ? [h("dt", null, "Result"), h("dd", { class: "uh-mono" }, JSON.stringify(x.action_result))] : null))));
        }
      });
      return h("div", { class: "uh-scroll" }, h("table", null,
        h("thead", null, h("tr", null, ["time", "item", "action", "decision", "failed rule", "proposal hash"].map(function (c) { return h("th", { scope: "col" }, c); }))),
        h("tbody", null, body)));
    });
  }

  /* ---------- 2. Your Pact ---------- */
  function pactBlock(p) {
    var picked = S.picked[p.pact_id];
    var sel = p.versions.some(function (v) { return v.version === picked; }) ? picked : p.latest_version;   // follow the newest unless one was picked
    var v = p.versions.filter(function (x) { return x.version === sel; })[0] || p.versions[0];
    var o = p.owner || {};
    return h("div", { style: "margin-bottom:14px" },
      h("dl", { class: "uh-kv" },
        h("dt", null, "Pact"), h("dd", { class: "uh-mono" }, p.pact_id),
        h("dt", null, "Owner"), h("dd", null, (o.name || o.id || "") + " ", h("span", { class: "uh-mono" }, "(" + (o.id || "") + ")")),
        h("dt", null, "Persona"), h("dd", null, pill(o.persona_status === "approved" ? "ok" : "ask", o.persona_status || "none"), " ",
          h("span", { class: "uh-mono" }, "verified " + (fmtDT(o.verified_at) || "never") + " \u00b7 re-checked " + (fmtDT(o.reverified_at) || "never") +
            (o.persona_inquiry_id ? " \u00b7 " + o.persona_inquiry_id : "")))),
      h("div", { class: "uh-tabs", role: "tablist", "aria-label": "Pact versions" }, p.versions.map(function (x) {
        return h("button", { type: "button", role: "tab", "aria-selected": String(x.version === sel), class: "uh-btn" + (x.version === sel ? " on" : ""),
          onclick: function () { S.picked[p.pact_id] = x.version === p.latest_version ? null : x.version; render(true); } },
          "v" + x.version + " ", check(x.signature_ok), (x.is_latest ? " latest" : "") + (x.revoked ? " \u00b7 paused" : ""));
      })),
      v ? [h("dl", { class: "uh-kv" },
        h("dt", null, "Signature (recomputed now)"), h("dd", null, check(v.signature_ok), " ",
          v.signature_ok === true ? "hash and signature check out" :
          v.signature_ok === false ? "INVALID: the saved text no longer matches its signature, so the gate would stop on R1" : "no verifier available"),
        h("dt", null, "Hash"), h("dd", { class: "uh-mono" }, v.hash || ""),
        h("dt", null, "Created"), h("dd", { class: "uh-mono" }, fmtDT(v.created_at)),
        h("dt", null, "Status column"), h("dd", { class: "uh-mono" }, (v.status_column || "") + " (display only; the gate never reads it)")),
        h("pre", { "aria-label": "Signed text of version " + v.version }, v.pact_text)] : null);
  }
  function pactPanel(d) {
    return panel("Your Pact", "Persona \u00b7 The Pact: the signed text, a live signature check (\u2713 / \u2717), versions, signed pause records and Persona status.", d, function () {
      var revs = d.revocations || [];
      return [
        (d.pacts || []).length ? d.pacts.map(pactBlock) : h("p", { class: "uh-empty" }, "No Pact signed yet."),
        h("h3", { style: "font-size:15px;margin:14px 0 4px" }, "Pause records (signed, insert-only)"),
        revs.length ? h("div", { class: "uh-scroll" }, h("table", null,
          h("thead", null, h("tr", null, ["pact", "version", "when", "reason", "signature"].map(function (c) { return h("th", { scope: "col" }, c); }))),
          h("tbody", null, revs.map(function (r) {
            return h("tr", null, h("td", { class: "m" }, r.pact_id), h("td", null, "v" + r.version), h("td", { class: "m" }, fmtDT(r.revoked_at)),
              h("td", null, r.reason || ""), h("td", null, check(r.signature_ok)));
          })))) : h("p", { class: "uh-empty" }, "None. This Pact has not been paused or replaced."),
        h("p", { class: "uh-caption" }, "Verifier: " + (d.verifier || "") + (d.verifier_found === false ? " (not found, so signatures show ?)" : "")),
      ];
    });
  }

  /* ---------- 3-4. Approvals + Policy ---------- */
  function approvalsPanel(d) {
    return panel("Approvals and run-once", "The Pact: each approval is bound to one proposal hash; each action runs once.", d, function () {
      return [h("h3", { style: "font-size:15px;margin:4px 0" }, "Approvals"),
        table(d.approvals, ["id", "status", "amount_usd", "expires_at", "proposal_hash"], { mono: ["proposal_hash"], empty: "No approvals yet." }),
        h("h3", { style: "font-size:15px;margin:14px 0 4px" }, "Executed actions (run-once guard)"),
        table(d.executed, ["action_type", "status", "amount_usd", "proposal_hash", "result"], { mono: ["proposal_hash"], empty: "Nothing has run yet." }),
        h("p", { class: "uh-caption" }, d.note || "")];
    });
  }
  function policyPanel(d) {
    return panel("Policy", "Senso: every store-policy question, the sentence quoted back, its source and revision.", d, function () {
      return table(d.checks, ["store", "question", "cited_text", "source_url", "senso_source_id", "revision", "fetched_at"],
        { mono: ["source_url", "senso_source_id", "revision"], empty: "No policy checks yet." });
    });
  }

  /* ---------- 5. Matching (fetched on demand, never polled: each run is a SIE embed + rerank request) ---------- */
  function loadMatch(chosen) {
    var gen = ++S.matchGen;
    S.matchBusy = true; render(true);
    var q = new URLSearchParams({ line_item_id: String(chosen) });
    if (MOCK) q.set("mock", "1");
    fetch(API + "/debug/matching?" + q, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (gen === S.matchGen) S.match = j; })
      .catch(function (e) { if (gen === S.matchGen) S.match = { available: false, error: String(e) }; })
      .then(function () { if (gen === S.matchGen) { S.matchBusy = false; render(true); } });
  }
  function matchingPanel(d) {
    var chosen = S.matchSel !== null ? S.matchSel : d.line_item_id;
    if (d.available !== false && chosen !== null && chosen !== undefined && S.match === null && !S.matchBusy) setTimeout(function () { loadMatch(chosen); }, 0);
    return panel("Matching", "SingleStore + Superlinked: top candidates with vector similarity, SIE rerank score and the exact-match flag, plus the SQL.", d, function () {
      var res = S.match, cands = (res && res.candidates) || [];
      var select = h("select", { id: "uh-item", onchange: function (e) { S.matchSel = Number(e.target.value); S.match = null; loadMatch(S.matchSel); } },
        (d.items || []).map(function (it) {
          return h("option", { value: String(it.id), selected: Number(it.id) === Number(chosen) }, it.title + " (" + (it.variant || "") + ") \u00b7 " + it.store);
        }));
      var result;
      if (res && res.available === false) result = h("p", { class: "uh-unavail", role: "status" }, "Unavailable", h("small", null, res.error || ""));
      else if (!cands.length) result = h("p", { class: "uh-empty" }, S.matchBusy ? "Running the matcher\u2026" : "No candidates.");
      else result = h("div", { class: "uh-scroll" }, h("table", null,
        h("thead", null, h("tr", null, ["store", "item", "similarity", "rerank", "exact match"].map(function (c) { return h("th", { scope: "col" }, c); }))),
        h("tbody", null, cands.map(function (c) {
          return h("tr", null, h("td", { class: "m" }, c.store || ""),
            h("td", null, (c.title || "") + (c.variant ? " (" + c.variant + ")" : "") + " ", h("span", { class: "uh-mono" }, c.sku || "")),
            h("td", { class: "m" }, c.similarity != null ? Number(c.similarity).toFixed(3) : ""), h("td", { class: "m" }, c.rerank_score != null ? Number(c.rerank_score).toFixed(3) : ""),
            h("td", null, pill(c.exact ? "ok" : "stop", c.exact ? "exact" : "not exact")));
        }))));
      return [h("div", { class: "uh-form" }, h("label", { for: "uh-item" }, "Purchase"), select,
        h("button", { type: "button", class: "uh-btn", disabled: S.matchBusy, onclick: function () { loadMatch(chosen); } }, S.matchBusy ? "Matching\u2026" : "Run the matcher again")),
        result, h("pre", { "aria-label": "Matcher SQL" }, d.sql || ""),
        h("p", { class: "uh-caption" }, (d.sql_source || "") + (res && res.note ? " \u00b7 " + res.note : ""))];
    });
  }

  /* ---------- 6. Live queries ---------- */
  function runQuery(key, needsItem) {
    S.liveBusy = key; render(true);
    var q = new URLSearchParams();
    if (MOCK) q.set("mock", "1");
    if (needsItem && S.listingId) q.set("item", S.listingId);
    fetch(API + "/debug/query/" + key + "?" + q, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) { S.liveRes = j; })
      .catch(function (e) { S.liveRes = { key: key, label: key, sql: "", params: [], rows: [], error: String(e) }; })
      .then(function () { S.liveBusy = null; render(true); });
  }
  function livePanel(d) {
    return panel("Live queries", "SingleStore: saved read-only queries, run live against the database.", d, function () {
      var res = S.liveRes;
      return [h("div", { class: "uh-form" },
        (d.queries || []).map(function (q) {
          return h("button", { type: "button", class: "uh-btn", disabled: S.liveBusy !== null, onclick: function () { runQuery(q.key, q.needs_item); } },
            S.liveBusy === q.key ? "Running\u2026" : q.label);
        }),
        h("label", { for: "uh-listing", class: "uh-caption" }, "Listing id (price history)"),
        h("input", { id: "uh-listing", type: "number", min: "1", value: S.listingId, style: "width:80px",
          oninput: function (e) { S.listingId = e.target.value; } })),
        res ? h("div", { role: "status" },
          h("p", { style: "margin:8px 0 0;font-weight:600" }, res.label + " ", res.sample ? pill("ask", "SAMPLE") : null),
          h("pre", { "aria-label": "SQL" }, (res.sql || "") + (res.params && res.params.length ? "\n-- params: " + JSON.stringify(res.params) : "")),
          res.error ? h("p", { class: "uh-unavail" }, "Unavailable", h("small", null, res.error)) :
            [table(res.rows, null, { mono: ["ts", "seen_at"], empty: "The query returned no rows." }),
             h("p", { class: "uh-caption" }, res.rows.length + " row" + (res.rows.length === 1 ? "" : "s") + " \u00b7 " + res.took_ms + " ms")]) : null];
    });
  }

  /* ---------- 7-10. Ingest, Safety, Analytics, Tests ---------- */
  function ingestPanel(d) {
    return panel("Ingest", "Airbyte + Arcee: receipts pulled through the Airbyte Gmail connector, parse confidence, model, tokens, time.", d, function () {
      var runs = (d.parse_events || []).map(function (e) {
        return { at: e.at, model: e.props.model, tokens: e.props.tokens, latency_ms: e.props.latency_ms, confidence: e.props.confidence, cost_usd: e.props.cost_usd };
      });
      return [h("h3", { style: "font-size:15px;margin:4px 0" }, "Receipts"),
        table(d.emails, ["id", "from_addr", "subject", "received_at", "parsed"], { empty: "No receipts ingested yet." }),
        h("h3", { style: "font-size:15px;margin:14px 0 4px" }, "What was parsed"),
        table(d.line_items, ["store", "order_no", "title", "variant", "price_paid", "parse_confidence"], { empty: "Nothing parsed yet." }),
        h("h3", { style: "font-size:15px;margin:14px 0 4px" }, "Parser runs"),
        table(runs, null, { empty: "No parser runs reported since the server started." }),
        h("p", { class: "uh-caption" }, (d.avg_cost_per_receipt_usd != null ? "Average cost per receipt: $" + d.avg_cost_per_receipt_usd : "Cost per receipt: not reported yet") + " \u00b7 " + (d.note || ""))];
    });
  }
  function safetyPanel(d) {
    return panel("Safety", "The Pact: demo mode, who may be emailed, and the last 20 outgoing messages.", d, function () {
      var allowed = d.allowed_recipients || [];
      return [h("dl", { class: "uh-kv" },
        h("dt", null, "Demo mode"), h("dd", null, pill(d.demo_mode ? "ok" : "bad", d.demo_mode ? "ON" : "OFF"), " ", h("span", { class: "uh-mono" }, "DEMO_MODE=" + JSON.stringify(d.demo_mode_raw || ""))),
        h("dt", null, "Allowed recipients"), h("dd", { class: "uh-mono" }, allowed.length ? allowed.join(", ") : "none set: nothing can be sent"),
        h("dt", null, "Support inbox"), h("dd", { class: "uh-mono" }, d.support_inbox || "not set")),
        d.messages_error ? h("p", { class: "uh-unavail" }, "Messages unavailable", h("small", null, d.messages_error)) :
          table(d.messages, ["sent_at", "to_addr", "subject", "thread_id", "direction"], { mono: ["thread_id"], empty: "No messages sent." })];
    });
  }
  function analyticsPanel(d) {
    return panel("Analytics", "Amplitude: dashboard link and today's event counts.", d, function () {
      var s = d.status || {}, counts = Object.keys(d.today_counts || {});
      return [h("dl", { class: "uh-kv" },
        h("dt", null, "Amplitude"), h("dd", null, s.enabled ? pill("ok", "sending") : pill("ask", "no API key: counting locally only"), " ",
          h("span", { class: "uh-mono" }, "sent " + (s.sent || 0) + " \u00b7 failed " + (s.failed || 0) + (s.last_error ? " \u00b7 " + s.last_error : ""))),
        h("dt", null, "Dashboard"), h("dd", null, d.dashboard_url ? h("a", { href: d.dashboard_url, target: "_blank", rel: "noreferrer noopener" }, "Open the Amplitude dashboard") :
          h("span", { class: "uh-mono" }, "AMPLITUDE_DASHBOARD_URL not set"))),
        counts.length ? h("div", { class: "uh-scroll" }, h("table", null, h("thead", null, h("tr", null, h("th", { scope: "col" }, "event"), h("th", { scope: "col" }, "today"))),
          h("tbody", null, counts.map(function (k) { return h("tr", null, h("td", { class: "m" }, k), h("td", null, String(d.today_counts[k]))); })))) :
          h("p", { class: "uh-empty" }, "No events yet today.")];
    });
  }
  function testsPanel(d) {
    return panel("Tests", "The last pytest -q run, written to a file by the test run.", d, function () {
      return d.ran ? [h("p", { class: "uh-big" }, d.passed + " passed" + (d.failed ? ", " + d.failed + " failed" : "")),
        h("p", { class: "uh-caption" }, d.duration_s + "s \u00b7 finished " + fmtTime(d.finished_at))] : h("p", { class: "uh-empty" }, d.note || "");
    });
  }

  /* ---------- page ---------- */
  function build() {
    var d = S.data;
    var top = h("div", { class: "uh-top" },
      h("div", null, h("a", { class: "uh-back", href: HOME }, "\u2190 Back to Clawback"), h("h1", null, "Under the hood"),
        h("p", { class: "uh-sub" }, "How Clawback decides, step by step. Read-only: nothing on this page changes anything.")),
      h("div", { class: "uh-controls" },
        h("label", null, h("input", { type: "checkbox", checked: S.auto, onchange: function (e) { S.auto = e.target.checked; } }), " Refresh every " + REFRESH_MS / 1000 + "s"),
        h("button", { type: "button", class: "uh-btn", onclick: function () { load(true); } }, "Refresh now"),
        h("span", { "aria-live": "polite" }, d ? "Updated " + fmtTime(d.generated_at) : "Loading\u2026")));
    var out = [top];
    if (d && d.sample) out.push(h("p", { class: "uh-banner", role: "alert" }, d.sample_note || "SAMPLE DATA: nothing below is from the live system."));
    if (S.error) out.push(h("div", { class: "uh-error", role: "alert" }, S.error));
    if (d) {
      out.push(h("div", { class: "uh-grid" },
        gateTrace(d.gate_trace), pactPanel(d.pact),
        h("div", { class: "uh-grid two" }, approvalsPanel(d.approvals_once), policyPanel(d.policy)),
        matchingPanel(d.matching), livePanel(d.live_queries), ingestPanel(d.ingest),
        h("div", { class: "uh-grid two" }, safetyPanel(d.safety), h("div", { class: "uh-grid" }, analyticsPanel(d.analytics), testsPanel(d.tests)))));
      var routers = Object.keys(d.routers || {}).map(function (k) { var v = d.routers[k]; return k + " (" + (/^(mounted|attached)/.test(v) ? "ok" : v) + ")"; });
      out.push(h("p", { class: "uh-foot" }, "Mounted routers: " + (routers.join(" \u00b7 ") || "unknown")));
    }
    return out;
  }
  function render(fromUser) {
    var a = document.activeElement;
    // never rebuild the page under a field someone is typing in; the next refresh picks the change up
    if (!fromUser && a && root.contains(a) && /^(INPUT|SELECT)$/.test(a.tagName) && a.type !== "checkbox") return;
    var y = window.scrollY;
    root.replaceChildren.apply(root, build());
    window.scrollTo(0, y);
  }
  function load(force) {
    if (inFlight && !force) return;            // never stack requests if the API is slow
    inFlight = true;
    var q = new URLSearchParams();
    if (MOCK) q.set("mock", "1");
    fetch(API + "/debug/overview?" + q, { cache: "no-store" })
      .then(function (r) {
        if (r.status === 403) {
          return r.json().catch(function () { return {}; }).then(function (j) { S.data = null; S.error = j.detail || "Under the hood is only available on the demo laptop."; });
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json().then(function (j) { S.data = j; S.error = null; });
      })
      .catch(function (e) { S.error = "Can't reach the API at " + API + " (" + (e && e.message ? e.message : e) + "). Is the backend running?"; })
      .then(function () { inFlight = false; render(!!force); });   // a polled refresh (force=false) waits while a field has focus
  }

  render(true);
  load(true);
  setInterval(function () { if (S.auto && document.visibilityState === "visible") load(false); }, REFRESH_MS);
})();
