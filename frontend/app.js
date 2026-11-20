// Clawback UI (section 3.10). No build step: serve with `python3 -m http.server 3000`.
const API = localStorage.getItem("clawback_api") || "http://localhost:8001";
const USER = "u_demo";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (n) => "$" + Number(n || 0).toFixed(Number(n) % 1 ? 2 : 0);
const safeGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const safeSet = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

async function api(path, opts = {}) {
  const r = await fetch(API + path, { ...opts, headers: { "Content-Type": "application/json", "X-User-Id": USER, ...(opts.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || "Something went wrong on our side. Nothing was sent or bought.");
  return body;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 3200); }

// ---- plain language -------------------------------------------------------
const RULE_TEXT = {
  R0: () => "Something didn't add up, so we didn't act.",
  R1: () => "Please verify it's you again before Clawback can act.",
  A1: () => "Please verify it's you again before Clawback can act.",
  R2: () => "You haven't allowed Clawback to do this.",
  R3: () => "You haven't allowed Clawback at this store.",
  R4: () => "This item is final sale (or a type you excluded).",
  R5: (v, a) => `The cheaper one isn't the same item: you bought ${v.bought || a.item_title}, this is ${v.offered || "a different one"}.` + (v.same_store === false ? ` ${cap(a.store)} only matches its own prices.` : ""),
  R6: () => "We couldn't confirm the store's current policy.",
  R6b: (v, a) => `${cap(a.store)} doesn't take this kind of request this way.`,
  R6c: (v, a) => `${cap(a.store)} doesn't take this kind of request this way.`,
  R7: () => "The window to ask has closed (or closes too soon).",
  R8: (v) => `Not worth it: you'd only save ${usd(v.net)}; your minimum is ${usd(v.min)}.`,
  R8a: () => "The price just changed; we'll check again.",
  R9: () => "That would go over your spending limit.",
  R10: () => "We only write to the store's official support (or to you).",
  R11: () => "We've already asked about this order.",
  R13: () => "We only reply in the same conversation, up to 3 times.",
  R12: (v) => `This is above your ${usd(v.ask)} ask-first limit.`,
};
const STORE = { mockstore: "Mock Store", bestbuy: "Best Buy", target: "Target" };
const cap = (s) => (s ? STORE[String(s).toLowerCase()] || s[0].toUpperCase() + s.slice(1) : "the store");
function ruleSentence(a) {
  const f = (a.rule_results || []).find((r) => !r.pass);
  const rule = a.failed_rule || (f && f.rule);
  if (!rule) return "Everything checked out.";
  const v = (f && f.values) || {};
  if (rule === "R12") v.ask = v.ask ?? v.ask_first;
  return (RULE_TEXT[rule] || RULE_TEXT.R0)(v, a);
}
const name = (a) => [a.item_title, a.variant].filter(Boolean).join(" · ");
function pill(a) {
  if (a.kind === "deadline" && a.decision === "ALLOW") return ["Return closes soon", "amber"];
  if (a.decision === "ALLOW") return a.action_type === "claim_packet" ? ["Ready to send", "green"] : ["Got money back", "green"];
  if (a.decision === "ASK") return ["Needs your OK", "amber"];
  if (a.decision === "BLOCK") return ["Stopped by your rules", "gray"];
  return ["Watching", "gray"];
}
function sentence(a) {
  const n = a.item_title || "your item", drop = a.drop_usd ?? (a.price_paid - a.found_price);
  if (a.decision === "ALLOW" && a.action_type === "price_adjust_email")
    return a.followup_sent ? `Mock Store asked for proof; we sent your receipt and the new price.` : `We asked ${cap(a.store)} for <b>${usd(drop)} back</b> on your ${esc(n)}. Price dropped from ${usd(a.price_paid)} to ${usd(a.found_price)}.`;
  if (a.decision === "ALLOW" && a.action_type === "claim_packet") return `${cap(a.store)} doesn't take requests by email. We've put everything together and emailed it to you, ready to paste into ${cap(a.store)}'s chat.`;
  if (a.decision === "ALLOW" && a.action_type === "deadline_alert") return `Last day to return your ${esc(n)} is ${esc(a.window_ends || ((a.rule_results || []).find((r) => r.rule === "R7" && r.values && r.values.window_ends) || { values: {} }).values.window_ends || "soon")}.`;
  if (a.decision === "ALLOW" && a.action_type === "rebuy_return") return `We bought your ${esc(n)} again for ${usd(a.found_price)} and returned the old one.`;
  if (a.decision === "ASK") return `Allow Clawback to buy your ${esc(n)} again at ${esc(cap(a.store))} for ${usd(a.found_price)} and return the one you have? You'd save <b>${usd(drop)}</b>. ${esc(ruleSentence(a))}`;
  if (a.decision === "BLOCK") return `<b>We didn't act.</b> ${esc(ruleSentence(a))}`;
  return `Watching the price of your ${esc(n)}.`;
}
const RULE_LABEL = { R0: "Request matches your records", R1: "Pact is signed, current and you're verified", A1: "Approval is valid", R2: "Action allowed by your Pact", R3: "Store allowed", R4: "Not final sale or excluded",
  R5: "Same item", R6: "Store policy confirmed", R6b: "Store offers price adjustments", R6c: "Store takes this kind of request", R7: "Window still open", R8: "Worth it", R8a: "Price is fresh",
  R9: "Within spending limits", R10: "Safe recipient", R11: "First request on this order", R12: "Under your ask-first limit", R13: "Reply rules" };
const SHOW = { net: "saves $", min: "min $", paid: "paid $", price: "now $", days_left: "days left ", window_ends: "closes ", bought: "bought ", offered: "offered ", cap: "cap $", month_committed: "this month $", ask: "ask-first $", to: "to " };
function groupRules(results) {              // the gate logs several checks per rule; show each rule once
  const by = new Map();
  (results || []).forEach((r) => { const g = by.get(r.rule) || { rule: r.rule, pass: true, values: {} }; g.pass = g.pass && r.pass; Object.assign(g.values, r.values || {}); by.set(r.rule, g); });
  return [...by.values()].filter((r) => !r.rule.startsWith("skipped"));
}
function fmtVals(v, passed) { const parts = Object.entries(v || {}).filter(([k, x]) => SHOW[k] && x != null && typeof x !== "object" && (!passed || !["bought", "offered"].includes(k))).map(([k, x]) => `${SHOW[k]}${x}`); return parts.length ? "  (" + parts.join(", ") + ")" : ""; }
function whyPanel(a) {
  const ago = a.seen_at ? timeAgo(a.seen_at) : "a moment";
  const lines = [`<p>🧾 You bought <b>${esc(name(a))}</b> for <b>${usd(a.price_paid)}</b> on ${esc(a.order_date || "")}.</p>`];
  if (a.found_price != null) lines.push(`<p>🏷️ We found it for <b>${usd(a.found_price)}</b> at ${esc(cap(a.found_store || a.store))} (${esc(ago)} ago).</p>`);
  if (a.cited_text) lines.push(`<p>📄 ${esc(cap(a.store))}'s policy says: “${esc(a.cited_text)}” ${a.source_url ? `<a href="${esc(a.source_url)}" target="_blank" rel="noopener">source</a>` : ""}</p>`);
  lines.push(`<p>✅ Your rules: ${esc(a.decision === "ALLOW" ? "Everything checked out." : ruleSentence(a))}</p>`);
  const det = groupRules(a.rule_results).map((r) => `${r.pass ? "✓" : "✗"} ${RULE_LABEL[r.rule] || r.rule}${fmtVals(r.values, r.pass)}`).join("\n");
  return `<div class="why">${lines.join("")}<div class="det">${esc(det)}</div></div>`;
}
function timeAgo(ts) {
  const s = Math.max(0, (Date.now() - new Date(String(ts).replace(" ", "T") + (/[zZ+]/.test(String(ts)) ? "" : "Z"))) / 1000);
  return s < 90 ? "moments" : s < 5400 ? Math.round(s / 60) + " min" : Math.round(s / 3600) + " hours";
}
function card(a) {
  const [label, color] = pill(a);
  const id = "w" + (a.audit_id ?? a.opportunity_id ?? Math.random());
  const drop = a.drop_usd ?? (a.found_price != null ? a.price_paid - a.found_price : null);
  const prices = a.found_price != null && a.kind !== "deadline" ? `<div class="prices"><span>Paid <b>${usd(a.price_paid)}</b></span><span>→</span><span>Now <b class="good">${usd(a.found_price)}</b></span>${drop > 0 ? `<span class="save">−${usd(drop)}</span>` : ""}</div>` : "";
  return `<div class="card ${a.decision === "ASK" ? "ask" : ""}" data-id="${esc(id)}">
    <div class="item-top"><div><div class="item-title">${esc(a.item_title || "Your purchase")}</div><div class="item-meta">${esc(cap(a.store))}${a.variant ? " · " + esc(a.variant) : ""}</div></div><span class="pill ${color}">${label}</span></div>
    <p class="item-text">${sentence(a)}</p>${prices}
    ${a.decision === "ASK" && a.approval_id ? `<div class="row"><button data-approve="${esc(a.approval_id)}">Approve and buy</button><button class="ghost" data-decline="${esc(a.approval_id)}">Not now</button></div>` : ""}
    ${a.decision === "ALLOW" && a.action_type === "claim_packet" ? `<div class="row"><button data-copy="${esc(a.chat_script || "")}">Copy message for ${esc(cap(a.store))} chat</button></div>` : ""}
    ${a.kind === "deadline" && a.decision === "ALLOW" ? `<div class="row"><button class="ghost" data-remind>Remind me</button></div>` : ""}
    <div class="row" style="margin-top:8px"><button class="link" data-why="${esc(id)}">Why?</button></div><div data-slot></div>
  </div>`;
}

// ---- screens --------------------------------------------------------------
let activityCache = [];
async function loadActivity() { try { activityCache = await api("/activity"); } catch { /* keep last */ } return activityCache; }

async function purchaseCards() {
  const acts = await loadActivity();
  const latest = new Map();                       // one card per purchase (newest decision wins)
  acts.forEach((a) => { const k = a.line_item_id ?? a.opportunity_id; if (!latest.has(k)) latest.set(k, a); });
  return { acts, cards: [...latest.values()] };
}

async function money() {
  const { acts, cards } = await purchaseCards();
  const back = backTotal(acts);
  const ask = cards.filter((c) => c.decision === "ASK");
  const goal = Number(safeGet("clawback_goal") || 100), pct = Math.max(0, Math.min(1, goal ? back / goal : 0));
  const stopped = cards.filter((c) => c.decision === "BLOCK").length, done = cards.filter((c) => c.decision === "ALLOW").length;
  let daily = null; try { daily = (await api("/schedules")).tasks.find((t) => t.kind === "daily_summary" && t.last_result); } catch {}
  let stoppedCount = acts.filter((x) => x.decision === "BLOCK").length;
  try { const st = await api("/stats"); if (st && st.stopped != null) stoppedCount = st.stopped; } catch {}
  const R = 26, C = 2 * Math.PI * R;
  return `<div class="home-copy"><h1>You've got <span class="big">${usd(back)}</span> back</h1><p class="sub">Clawback watches your purchases and only acts inside your Pact.</p><p class="stopline"><b>${stoppedCount}</b> unsafe action${stoppedCount === 1 ? "" : "s"} stopped</p></div>
    <div class="stats">
      <a class="stat green" href="#activity"><b>${usd(back)}</b><span>Got back</span></a>
      <a class="stat ${ask.length ? "amber" : ""}" href="#needs"><b>${ask.length}</b><span>Needs your OK</span></a>
      <a class="stat" href="#purchases"><b>${cards.length}</b><span>Purchases watched</span></a>
    </div>
    <div class="grid2">
      <div class="card goal"><svg width="64" height="64" viewBox="0 0 64 64"><circle cx="32" cy="32" r="${R}" fill="none" stroke="#ffffff14" stroke-width="6"/><circle cx="32" cy="32" r="${R}" fill="none" stroke="url(#gg)" stroke-width="6" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - pct)}" transform="rotate(-90 32 32)"/><defs><linearGradient id="gg"><stop offset="0" stop-color="#34d399"/><stop offset="1" stop-color="#38bdf8"/></linearGradient></defs></svg>
        <div><div class="item-title">Monthly goal: ${usd(goal)}</div><div class="item-meta">${usd(back)} so far · ${Math.round(pct * 100)}%</div><button class="link" data-goal>Change goal</button></div></div>
      <div class="card"><div class="item-title">Summary</div><p class="item-text" style="margin-bottom:0">${esc(daily ? daily.last_result : `Done ${done}, waiting for you ${ask.length}, stopped by your rules ${stopped}.`)}</p></div>
    </div>
    ${ask.length ? `<a class="card row-link" href="#needs"><span><b>${ask.length} thing${ask.length > 1 ? "s" : ""} need${ask.length > 1 ? "" : "s"} your OK</b><br><span class="item-meta">${esc(ask[0].item_title)}: tap to review</span></span><span class="chev">›</span></a>` : ""}`;
}
function backTotal(acts) {
  const seen = new Set();                          // one money event per proposal, even if it was logged twice
  return acts.filter((a) => a.decision === "ALLOW" && ["price_adjust_email", "rebuy_return"].includes(a.action_type) && !seen.has(a.proposal_id) && seen.add(a.proposal_id))
    .reduce((s, a) => s + Number(a.drop_usd ?? (a.price_paid - a.found_price) ?? 0), 0);
}
async function needs() {
  const { cards } = await purchaseCards();
  const ask = cards.filter((c) => c.decision === "ASK");
  return `<div class="ph"><div><h1>Needs your OK</h1><p class="sub">Clawback never spends without a tap from you.</p></div></div>${ask.length ? `<div class="cards-grid">${ask.map(card).join("")}</div>` : `<div class="card"><p>Nothing needs your OK right now.</p></div>`}`;
}

async function purchases() {
  const { cards } = await purchaseCards();
  return `<div class="ph"><div><h1>Purchases</h1><p class="sub">Everything Clawback is watching for you.</p></div></div>
    ${cards.length ? `<div class="cards-grid">${cards.map(card).join("")}</div>` : `<p class="sub">Nothing yet. We'll show purchases here as soon as we see them.</p>`}`;
}

async function about() {
  const li = (arr) => arr.map((t) => `<li>${t}</li>`).join("");
  return `<a class="link back" href="#money">‹ Money back</a>
    <div class="about-hero"><img src="logo.svg" alt="" width="72" height="72"><h1>About Clawback</h1>
    <p class="sub">The agent that gets your money back after you buy, and only inside a Pact you signed.</p></div>

    <div class="about-cols">
    <h2>The one-minute version</h2>
    <div class="card"><p>You buy something. A week later it's cheaper. Normally you'd never notice, and if you did, you'd never write the email. Clawback notices, checks the store's real policy, and asks for the difference, but only if your rules say it may.</p></div>

    <h2>Who decides?</h2>
    <div class="card"><p><b>The AI proposes. Plain code decides.</b> The AI can suggest "ask for $34 back". It can't send, buy or spend anything. A small rulebook, with no AI in it, checks every suggestion against your signed Pact and says Done, Needs your OK, or Stopped.</p>
    <p class="sub" style="margin:8px 0 0">Think of the AI as an enthusiastic intern and the rulebook as the manager with the stamp.</p></div>

    <h2>Things Clawback will never do</h2>
    <div class="card"><ul class="fun">${li([
      "Approve a purchase because you asked it nicely (or loudly, or in an <i>official-sounding</i> email).",
      "Write to a store's real support inbox during the demo. It only emails addresses on a tiny allow-list.",
      "Spend money twice because you double-clicked. We tried. The second click gets a polite 409.",
      "Trust a Pact someone edited. Change one number and the signature fails.",
      "Compare a Black backpack to a Blue one and call it a bargain.",
      "Chase a $6 saving. Your minimum is $10. Clawback has standards.",
      "Argue with a store rep for more than 3 follow-ups. It knows when to leave a conversation.",
      "Keep going after you press Pause. Pausing is one tap, and it wins every time."])}</ul></div>

    <h2>Fun facts</h2>
    <div class="card"><ul class="fun">${li([
      "Black Friday is a week after this hackathon, which is when prices drop right after people buy. Convenient, and completely on purpose.",
      "The sphere on the home screen is borrowed from our previous project, Pact. It was wearing purple; we made it go green for money.",
      "\"Stopped by your rules\" is the good news. Every stop is a mistake that didn't happen.",
      "Voice can explain things and pause Clawback. It can never approve a purchase. Even if you shout."])}</ul></div>

    <h2>Honest fine print</h2>
    <div class="card"><p>This is a hackathon demo. The mock store, its prices and its support rep are simulated, and Target and Best Buy prices are cached real values. Nothing here sends mail to a real retailer or buys anything real.</p></div>

    <h2>Made by</h2>
    <div class="card"><p>Siva, plus three teammates, in one day. Built for the Personal Agents Hackathon.</p></div></div>`;
}

let schedForm = { kind: "price_watch", schedule_type: "interval", interval_min: 15, at_time: "08:00" };
const KIND_HELP = { price_watch: "Looks for price drops on your purchases. Your Pact still decides what happens.", daily_summary: "A short recap of what Clawback did.", deadline_check: "Warns you before a return window closes." };
const when = (ts) => (ts ? String(ts).slice(5, 16).replace("-", "/") : "not yet");
let calMonth = new Date().toISOString().slice(0, 7), calDay = null;
async function calendarHtml() {
  const cal = await api("/calendar?month=" + calMonth);
  const [y, m] = calMonth.split("-").map(Number), first = new Date(y, m - 1, 1), days = new Date(y, m, 0).getDate(), lead = first.getDay();
  const by = {}; cal.events.forEach((e) => (by[e.date] ||= []).push(e));
  const sel = calDay && calDay.startsWith(calMonth) ? calDay : (cal.today.startsWith(calMonth) ? cal.today : calMonth + "-01");
  let cells = "";
  for (let i = 0; i < lead; i++) cells += `<span class="cd empty"></span>`;
  for (let d = 1; d <= days; d++) {
    const iso = `${calMonth}-${String(d).padStart(2, "0")}`, evs = by[iso] || [], kinds = [...new Set(evs.map((e) => e.kind))];
    cells += `<button class="cd ${iso === cal.today ? "today" : ""} ${iso === sel ? "sel" : ""}" data-cal-day="${iso}"><b>${d}</b><span class="dots">${kinds.map((k) => `<i class="dot ${k}"></i>`).join("")}</span></button>`;
  }
  const list = (by[sel] || []).map((e) => `<p class="ev"><i class="dot ${e.kind}"></i> ${esc(e.title)}</p>`).join("") || `<p class="sub" style="margin:6px 0">Nothing on this day.</p>`;
  const title = first.toLocaleString("en-US", { month: "long", year: "numeric" });
  return `<div class="card"><div class="calhead"><button class="ghost" data-cal-nav="-1">‹</button><b>${title}</b><button class="ghost" data-cal-nav="1">›</button></div>
    <div class="calgrid">${["S", "M", "T", "W", "T", "F", "S"].map((x) => `<span class="cdw">${x}</span>`).join("")}${cells}</div>
    <div class="legend"><span><i class="dot schedule"></i>Schedule</span><span><i class="dot deadline"></i>Deadline</span><span><i class="dot activity"></i>What Clawback did</span></div>
    <div class="dayev"><b>${new Date(sel + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}</b>${list}</div></div>`;
}
async function schedules() {
  const { kinds, tasks } = await api("/schedules");
  const calendar = await calendarHtml().catch(() => "");
  const opts = Object.entries(kinds).map(([k, v]) => `<option value="${k}" ${schedForm.kind === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  return `<div class="ph"><div><h1>Schedule</h1><p class="sub">Your calendar: what Clawback does on a timer, deadlines coming up, and what it already did. Schedules can look and remind. They can't spend.</p></div></div>
    <div class="sched-layout"><div>${calendar}</div><div class="manage"><h2 style="margin-top:0">Manage schedules</h2>
    <div class="card"><b>New schedule</b>
      <label class="f"><span>Task</span><select data-sf="kind">${opts}</select></label>
      <p class="sub" style="margin:4px 0">${esc(KIND_HELP[schedForm.kind])}</p>
      <label class="f"><span>How often</span><select data-sf="schedule_type"><option value="interval" ${schedForm.schedule_type === "interval" ? "selected" : ""}>Every few minutes</option><option value="daily" ${schedForm.schedule_type === "daily" ? "selected" : ""}>Once a day</option></select></label>
      ${schedForm.schedule_type === "interval" ? `<label class="f"><span>Every (minutes)</span><input type="number" min="1" data-sf="interval_min" value="${schedForm.interval_min}"></label>` : `<label class="f"><span>At</span><input type="time" data-sf="at_time" value="${schedForm.at_time}"></label>`}
      <div class="row"><button data-sched-add>Add schedule</button></div></div>
    ${tasks.map((t) => `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><div><b>${esc(t.name)}</b><br><span class="sub" style="margin:0">${esc(t.when)}</span></div>
      <label class="f" style="border:0;padding:0"><input type="checkbox" data-sched-toggle="${t.id}" ${t.enabled ? "checked" : ""}></label></div>
      <p class="sub" style="margin:8px 0 0">Next: ${t.enabled ? esc(when(t.next_run)) : "paused"} · Last: ${esc(when(t.last_run))}</p>
      ${t.last_result ? `<p style="margin:6px 0 0"><span class="pill ${t.last_status === "ok" ? "green" : t.last_status === "skipped" ? "amber" : "gray"}">${esc(t.last_status)}</span> ${esc(t.last_result)}</p>` : ""}
      <div class="row"><button class="ghost" data-sched-run="${t.id}">Run now</button><button class="ghost" data-sched-del="${t.id}">Delete</button></div></div>`).join("")}</div></div>`;
}

async function activity() {
  const acts = await loadActivity(), f = activity.filter || "All";
  const map = { Done: "ALLOW", "Needs your OK": "ASK", Stopped: "BLOCK" };
  const shown = acts.filter((a) => f === "All" || a.decision === map[f]);
  const days = new Map(); shown.forEach((a) => { const d = String(a.ts || "").slice(0, 10); (days.get(d) || days.set(d, []).get(d)).push(a); });
  const plain = (h) => { const d = document.createElement("div"); d.innerHTML = h; return d.textContent; };
  const row = (a) => { const id = "w" + (a.audit_id ?? a.opportunity_id), [label, color] = pill(a);
    return `<div class="tl-item"><button class="tl-row" data-why="${esc(id)}" data-id="${esc(id)}"><span class="dotc ${color}"></span><span><span class="t">${esc(a.item_title)} · ${label}</span><br><span class="d">${esc(plain(sentence(a)))}</span></span><time>${esc(String(a.ts || "").slice(11, 16))}</time></button><div data-slot></div></div>`; };
  return `<div class="ph"><div><h1>Activity</h1><p class="sub">Everything Clawback did, newest first.</p></div></div>
    <div class="chips">${["All", "Done", "Needs your OK", "Stopped"].map((c) => `<button class="chip ${c === f ? "on" : ""}" data-chip="${c}">${c}</button>`).join("")}</div>
    ${[...days].map(([d, list]) => `<div class="tl-day">${esc(new Date(d + "T00:00").toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" }))}</div><div class="tl">${list.map(row).join("")}</div>`).join("") || `<p class="sub">Nothing here yet.</p>`}`;
}

const FIELDS = { price_adjust: true, rebuy: true, min_net_savings: 10, ask_first_above: 150, max_rebuy_price_usd: 250, monthly_cap: 300, skip_clearance_and_gift_cards: true };
let form = { ...FIELDS }, editing = false;
async function pact() {
  const [p, who] = await Promise.all([api("/pact"), api("/persona/status").catch(() => ({ persona_status: "none" }))]);
  if (p.signed && !editing) {
    const L = p.limits;
    const kv = (k, v) => `<div>${k}</div><div>${v}</div>`;
    return `<div class="ph"><div><h1>Your Pact</h1><p class="sub">The rules Clawback can never go beyond.</p></div><span class="pill ${p.paused || !p.current ? "gray" : "green"}">${p.paused ? "Paused" : "Active"} · version ${esc(p.version)} · ${esc(p.short_hash)}</span></div>
      <div class="grid2"><div class="card"><div class="item-title">Your limits</div><div class="kv" style="margin-top:6px">${kv("Only act if I save at least", usd(L.min_net_savings))}${kv("Ask me first above", usd(L.ask_first_above))}${kv("Most at once", usd(L.max_rebuy_price_usd))}${kv("Per month", usd(L.monthly_cap))}${kv("Stores", L.stores.map(cap).join(", "))}${kv("Valid until", esc(p.valid_until))}</div></div>
      <div class="card"><div class="item-title">In plain words</div>
      <p>Clawback may ask ${L.stores.map(cap).join(", ")} for price adjustments${L.allowed_actions.includes("rebuy_return") ? " and may buy again and return" : ""}, only when you save at least ${usd(L.min_net_savings)}, never over ${usd(L.max_rebuy_price_usd)} at once or ${usd(L.monthly_cap)} a month. It asks you first above ${usd(L.ask_first_above)}. Valid until ${esc(p.valid_until)}.</p>
      
      <div class="row"><button class="ghost" data-edit>Edit limits</button>${p.paused ? "" : `<button class="ghost" data-pause>Pause Clawback</button>`}</div></div></div>`;
  }
  const verified = who.persona_status === "approved";
  const step = !verified ? 1 : (pact.step || 2);
  const steps = `<div class="steps">${[1, 2, 3].map((i) => `<i class="${i <= step ? "on" : ""}"></i>`).join("")}</div>`;
  if (step === 1) return `<h1>Verify it's you</h1>${steps}<p class="sub">Clawback will only act for a verified person.</p>
    <div class="card"><p>${who.persona_status === "pending" ? "Waiting for your verification to finish…" : "It takes about a minute."}</p><div class="row"><button data-verify>Verify with Persona</button></div></div>`;
  const num = (k, label) => `<label class="f"><span>${label}</span><input type="number" min="0" data-f="${k}" value="${form[k]}"></label>`;
  const tog = (k, label) => `<label class="f"><span>${label}</span><input type="checkbox" data-f="${k}" ${form[k] ? "checked" : ""}></label>`;
  if (step === 2) return `<h1>Set your limits</h1>${steps}${tog("price_adjust", "Ask the store for money back when prices drop")}${tog("rebuy", "Buy again and return when that saves more")}
    ${num("min_net_savings", "Only act if I save at least $")}${num("ask_first_above", "Ask me first above $")}${num("max_rebuy_price_usd", "Never spend more than $ at once")}${num("monthly_cap", "$ a month max")}${tog("skip_clearance_and_gift_cards", "Skip clearance and gift cards")}
    <div class="row"><button data-next>Continue</button></div>`;
  return `<h1>Review and sign</h1>${steps}<div class="card"><p>Clawback may ask Mock Store, Target and Best Buy for price adjustments${form.rebuy ? " and may buy again and return" : ""}, only when you save at least ${usd(form.min_net_savings)}, never over ${usd(form.max_rebuy_price_usd)} at once or ${usd(form.monthly_cap)} a month, and it asks you first above ${usd(form.ask_first_above)}. Valid for 90 days.</p></div>
    <div class="row"><button data-sign>Sign my Pact</button><button class="ghost" data-back>Back</button></div>`;
}

function renderSide() {
  const side = $("#side"); if (!side) return;
  document.querySelectorAll("[data-id]").forEach((c) => c.classList.toggle("sel", wide() && c.dataset.id === selected));
  if (["pact", "about", "money", "schedules"].includes(tab)) { side.innerHTML = ""; return; }
  const a = selected && activityCache.find((x) => "w" + (x.audit_id ?? x.opportunity_id) === selected);
  side.innerHTML = a
    ? `<div class="card"><span class="pill ${pill(a)[1]}">${pill(a)[0]}</span><h2 style="margin:10px 0 2px">${esc(name(a))}</h2>${whyPanel(a)}</div>`
    : `<div class="card"><h2 style="margin-top:0">Why?</h2><p class="hint">Choose <b>Why?</b> on any card to see exactly what Clawback saw, what the store's policy says, and which of your rules decided it.</p></div>`;
}

// ---- wiring ---------------------------------------------------------------
const screens = { money, needs, purchases, schedules, pact, activity, about };
let tab = "money", busy = false, orbHold = null, selected = null;
const wide = () => matchMedia("(min-width:960px)").matches;
async function render(keepScroll) {
  if (busy) return; busy = true;
  const y = scrollY;
  try { $("#view").innerHTML = await screens[tab](); } catch (e) { $("#view").innerHTML = `<p class="sub">${esc(e.message)}</p>`; }
  document.body.classList.toggle("home", tab === "money");
  document.body.classList.toggle("has-side", ["needs", "purchases", "activity"].includes(tab));
  document.title = ({ money: "Home", needs: "Needs your OK", purchases: "Purchases", activity: "Activity", schedules: "Schedule", pact: "Your Pact", about: "About" }[tab] || "Clawback") + " · Clawback";
  refreshPactChip();
  document.body.classList.toggle("mini", tab !== "money");
  if (tab === "money") { $("#ask").hidden = false; } else if (!document.body.classList.contains("ask-open")) { $("#ask").hidden = true; }
  const pending = activityCache.filter((x) => x.decision === "ASK" && x.approval_id).length;
  const bd = $("#badge"); bd.hidden = !pending; bd.textContent = pending;
  renderSide();
  if (window.Orb && !window.Orb.busy) Orb.set(orbHold || (activityCache.some((a) => a.decision === "ASK" && a.approval_id) ? "ask" : "idle"));
  document.querySelectorAll("nav a, .rail-foot .links a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  if (keepScroll) scrollTo(0, y);
  busy = false;
}
function route() { tab = (location.hash || "#money").slice(1); if (!screens[tab]) tab = "money"; render(); }
addEventListener("hashchange", route);
const detailBoxes = [$("#details"), $("#details2")];
detailBoxes.forEach((b) => { b.checked = safeGet("clawback_details") === "1"; b.addEventListener("change", (e) => { detailBoxes.forEach((x) => (x.checked = e.target.checked)); document.body.classList.toggle("details", e.target.checked); safeSet("clawback_details", e.target.checked ? "1" : "0"); }); });
document.body.classList.toggle("details", $("#details").checked);
async function refreshPactChip() {
  const chip = $("#pactchip"); if (!chip) return;
  try { const p = await api("/pact"); chip.hidden = !p.signed; chip.className = "pactchip" + (p.paused ? " paused" : ""); chip.innerHTML = `<i></i>${p.paused ? "Pact paused" : "Pact active"}`; } catch { chip.hidden = true; }
}

document.addEventListener("change", async (e) => {
  const k = e.target.dataset.sf, tg = e.target.dataset.schedToggle;
  try {
    if (k) { schedForm[k] = e.target.type === "number" ? Number(e.target.value) : e.target.value; if (k === "kind" || k === "schedule_type") render(true); }
    else if (tg) { await api("/schedules/" + tg + "/toggle", { method: "POST", body: JSON.stringify({ enabled: e.target.checked }) }); render(true); }
  } catch (err) { toast(err.message); }
});
document.addEventListener("input", (e) => { const k = e.target.dataset.f; if (k) form[k] = e.target.type === "checkbox" ? e.target.checked : Number(e.target.value); });
document.addEventListener("click", async (e) => {
  const t = e.target.closest("button"); if (!t) return;
  const d = t.dataset;
  try {
    if (d.why && wide()) { selected = selected === d.why ? null : d.why; renderSide(); }
    else if (d.why) { const slot = t.closest(".card, .tl-item").querySelector("[data-slot]"); const a = activityCache.find((x) => "w" + (x.audit_id ?? x.opportunity_id) === d.why);
      slot.innerHTML = slot.innerHTML ? "" : (a ? whyPanel(a) : ""); }
    else if (d.approve) { t.disabled = true; const r = await api("/approvals/" + d.approve, { method: "POST", body: JSON.stringify({ decision: "approve" }) });
      if (window.Orb) { orbHold = r.status === "done" ? "done" : "stopped"; setTimeout(() => { orbHold = null; render(true); }, 4000); }
      toast(r.status === "done" ? "Done. We bought it again and returned the old one." : r.status === "stopped" ? "Stopped by your rules. Nothing was bought." : "Already handled."); render(true); }
    else if (d.decline) { await api("/approvals/" + d.decline, { method: "POST", body: JSON.stringify({ decision: "decline" }) }); toast("No problem. Nothing was bought."); render(true); }
    else if (d.copy !== undefined) { await navigator.clipboard.writeText(d.copy); toast("Copied."); }
    else if (d.chip) { activity.filter = d.chip; render(); }
    else if ("goal" in d) { const v = prompt("Monthly goal in dollars", safeGet("clawback_goal") || "100"); if (v && Number(v) > 0) { safeSet("clawback_goal", String(Number(v))); render(true); } }
    else if ("schedAdd" in d) { await api("/schedules", { method: "POST", body: JSON.stringify(schedForm) }); toast("Schedule added."); render(true); }
    else if (d.calNav) { const [y, m] = calMonth.split("-").map(Number), n = new Date(y, m - 1 + Number(d.calNav), 1); calMonth = n.getFullYear() + "-" + String(n.getMonth() + 1).padStart(2, "0"); calDay = null; render(); }
    else if (d.calDay) { calDay = d.calDay; render(true); }
    else if (d.schedRun) { t.disabled = true; const r = await api("/schedules/" + d.schedRun + "/run", { method: "POST" }); toast(r.result); render(true); }
    else if (d.schedDel) { if (confirm("Delete this schedule?")) { await api("/schedules/" + d.schedDel, { method: "DELETE" }); render(true); } }
    else if ("remind" in d) toast("We'll remind you.");
    else if ("verify" in d) await startPersona();
    else if ("next" in d) { pact.step = 3; render(); }
    else if ("back" in d) { pact.step = 2; render(); }
    else if ("sign" in d) { t.disabled = true; await api("/pact", { method: "POST", body: JSON.stringify(form) }); editing = false; pact.step = 0; toast("Your Pact is signed."); render(); }
    else if ("edit" in d) { editing = true; pact.step = 2; render(); }
    else if ("pause" in d) { await api("/pact/pause", { method: "POST" }); toast("Clawback is paused."); render(); }
  } catch (err) { toast(err.message); if (t) t.disabled = false; }
});

async function startPersona() {
  const r = await api("/persona/inquiry", { method: "POST" });
  if (!window.Persona) await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "https://cdn.withpersona.com/dist/persona-v5.1.2.js"; s.onload = res; s.onerror = () => rej(new Error("Couldn't load verification.")); document.head.appendChild(s); });
  new Persona.Client({ templateId: r.template_id, inquiryId: r.inquiry_id, sessionToken: r.session_token, environment: "sandbox",
    onComplete: () => { toast("Verified. Thank you."); render(); }, onCancel: () => {}, onError: () => toast("Verification didn't finish. Nothing was changed.") }).open();
}

setInterval(() => { if (!document.hidden && !["pact", "about", "schedules"].includes(tab) && !document.querySelector("[data-slot]:not(:empty)")) render(true); }, 4000);
route();

// ---- voice / ask box (section 3.11): explains and pauses; never approves money ----
Voice.init({
  acts: () => activityCache,
  api, usd, ruleSentence, cap, backTotal,
  openNeeds: () => { location.hash = "#needs"; },
  pause: async () => { await api("/pact/pause", { method: "POST" }); render(); },
  orb: (s) => { if (window.Orb) { Orb.busy = s !== "idle"; Orb.set(s === "idle" ? (activityCache.some((x) => x.decision === "ASK" && x.approval_id) ? "ask" : "idle") : s); } },
});
