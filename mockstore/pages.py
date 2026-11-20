"""HTML for the Mock Store (plain, believable store; server-rendered, no build step).

Every dynamic value goes through `esc()`. The admin and support pages do their work with a few
lines of fetch() and write server data into the DOM with textContent, never innerHTML.
"""
from __future__ import annotations

from html import escape as esc

from . import store

SWATCH = {"black": "#23262b", "blue": "#2f5fd0", "white": "#f1f1ee", "gray": "#8a8f98"}

CSS = """
:root{--ink:#1d2430;--mute:#667085;--line:#e4e7ec;--brand:#0b6b4f;--brand2:#0a5640;--bg:#f6f7f8;--sale:#b42318}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
header.top{background:#fff;border-bottom:1px solid var(--line)}
.bar{max-width:980px;margin:0 auto;padding:14px 20px;display:flex;gap:22px;align-items:center}
.logo{font-weight:800;font-size:20px;color:var(--brand);letter-spacing:-.3px}.bar nav{display:flex;gap:18px;font-size:15px;margin-left:auto}
main{max-width:980px;margin:0 auto;padding:26px 20px 60px}
h1{font-size:28px;margin:.2em 0 .5em}h2{font-size:20px;margin:1.4em 0 .5em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:18px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px;display:block;color:inherit}
.card:hover{text-decoration:none;box-shadow:0 2px 10px rgba(16,24,40,.08)}
.tile{height:130px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:52px;margin-bottom:10px}
.price{font-weight:700;font-size:20px}.was{color:var(--mute);text-decoration:line-through;font-weight:400;font-size:15px;margin-left:6px}
.badge{display:inline-block;font-size:12px;font-weight:700;padding:2px 8px;border-radius:999px;background:#fef3f2;color:var(--sale);margin-left:6px}
.muted{color:var(--mute)}.row{display:flex;gap:30px;flex-wrap:wrap}.pic{flex:0 0 320px;max-width:100%}
.pic .tile{height:320px;font-size:110px}.info{flex:1;min-width:260px}
.swatches a{display:inline-block;padding:6px 14px;border:1px solid var(--line);border-radius:999px;margin-right:8px;color:var(--ink);background:#fff}
.swatches a.on{border-color:var(--brand);box-shadow:inset 0 0 0 1px var(--brand);font-weight:600}
button,.btn{font:inherit;background:var(--brand);color:#fff;border:0;border-radius:8px;padding:9px 16px;cursor:pointer}
button:hover{background:var(--brand2)}button.alt{background:#fff;color:var(--ink);border:1px solid var(--line)}
button.alt:hover{background:#f2f4f7}button:disabled{opacity:.5;cursor:default}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;font-size:15px;vertical-align:middle}th{background:#f9fafb;font-size:13px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
input[type=number],input[type=text],textarea{font:inherit;padding:8px 10px;border:1px solid #cfd4dc;border-radius:8px;width:100%}
input.p{width:110px}textarea{min-height:120px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.note{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.ok{color:#067647}.bad{color:var(--sale)}code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
footer{max-width:980px;margin:0 auto;padding:0 20px 30px;color:var(--mute);font-size:13px}
.toast{position:fixed;right:18px;bottom:18px;background:#101828;color:#fff;padding:10px 14px;border-radius:8px;opacity:0;transition:.2s}.toast.on{opacity:1}
"""


def layout(title: str, body: str, script: str = "") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Mock Store</title>
<style>{CSS}</style></head><body>
<header class="top"><div class="bar"><a class="logo" href="/mock">Mock Store</a>
<nav><a href="/mock">Shop</a><a href="/mock/policy">Policies</a><a href="/mock/support">Support</a></nav></div></header>
<main>{body}</main>
<footer>Mock Store is a simulated retailer built for the Clawback hackathon demo. No real orders, no real money.</footer>
<div class="toast" id="toast" role="status"></div>
<script>
const BASE = location.pathname.replace(/\\/(admin|support|p|policy).*$/, '') || '/mock';
function toast(t){{const e=document.getElementById('toast');e.textContent=t;e.classList.add('on');setTimeout(()=>e.classList.remove('on'),2200)}}
async function api(path, body){{
  const r = await fetch(BASE + '/api/' + path, {{method: body===undefined?'GET':'POST', headers:{{'Content-Type':'application/json'}}, body: body===undefined?undefined:JSON.stringify(body)}});
  let j = {{}}; try{{ j = await r.json() }}catch(e){{}}
  if(!r.ok) throw new Error((j.detail && (j.detail.message||j.detail)) || ('HTTP '+r.status));
  return j;
}}
{script}
</script></body></html>"""


def _swatch(variant: str) -> str:
    return SWATCH.get(variant.lower(), "#d0d5dd")


def _tile(l: dict) -> str:
    bg = _swatch(l["variant"])
    fg = "#222" if l["variant"].lower() in ("white",) else "#fff"
    return f'<div class="tile" style="background:{bg};color:{fg}">{l["icon"]}</div>'


def _price_html(l: dict) -> str:
    cur, seed = l["price"], l["seed_price"]
    was = f'<span class="was">${seed:,.2f}</span>' if cur < seed else ""
    return f'<span class="price">${cur:,.2f}</span>{was}'


def home() -> str:
    seen, cards = set(), []
    for l in store._state["listings"].values():
        if l["sku"] in seen:
            continue
        seen.add(l["sku"])
        variants = store.listings_for_sku(l["sku"])
        low = min(v["price"] for v in variants)
        badge = '<span class="badge">Clearance</span>' if l["category"] == "clearance" else ""
        more = f' · {len(variants)} colors' if len(variants) > 1 else ""
        cards.append(f'<a class="card" href="/mock/p/{esc(l["sku"])}">{_tile(l)}'
                     f'<div>{esc(l["title"])}{badge}</div><div class="price">${low:,.2f}</div>'
                     f'<div class="muted">{esc(l["category"].title())}{more}</div></a>')
    return layout("Shop", f"<h1>Shop</h1><div class='grid'>{''.join(cards)}</div>")


def product(sku: str, variant: str | None) -> str | None:
    vs = store.listings_for_sku(sku)
    if not vs:
        return None
    l = next((v for v in vs if variant and v["variant"].lower() == variant.lower()), vs[0])
    sw = ""
    if len(vs) > 1:
        sw = "<div class='swatches'>" + "".join(
            f'<a class="{"on" if v["id"] == l["id"] else ""}" href="/mock/p/{esc(v["sku"])}?variant={esc(v["variant"])}">{esc(v["variant"])}</a>'
            for v in vs) + "</div>"
    badge = '<span class="badge">Clearance: final sale</span>' if l["category"] == "clearance" else ""
    body = f"""<div class="row"><div class="pic">{_tile(l)}</div><div class="info">
<div class="muted">{esc(l["category"].title())}</div><h1>{esc(l["title"])} {badge}</h1>
<div style="margin:.4em 0 1em">{_price_html(l)}</div>
<p class="muted">Color: <strong style="color:var(--ink)">{esc(l["variant"])}</strong></p>{sw}
<p>{esc(l["blurb"])}</p>
<p><button type="button" onclick="toast('Demo store: nothing to buy here')">Add to cart</button></p>
<p class="muted mono">SKU {esc(l["sku"])} · Model {esc(l["model_number"])} · UPC {esc(l["upc"])}</p>
<p class="muted">Price adjustments and returns follow our <a href="/mock/policy">policies</a>.</p></div></div>"""
    return layout(l["title"], body)


def policy() -> str:
    p = store.policy()
    items = "".join(f"<li>{esc(s)}</li>" for s in p["sentences"])
    body = f"""<h1>Price adjustment and return policy</h1>
<div class="note"><ul style="margin:0;padding-left:20px;line-height:1.8">{items}</ul></div>
<p class="muted">Revision {p["revision"]} · updated {esc(p["updated_at"])}</p>"""
    return layout("Policies", body)


ADMIN_SCRIPT = """
async function setPrice(id, fixed){const v=(fixed!==undefined)?fixed:document.getElementById('p'+id).value;
  try{const j=await api('price',{listing_id:id,price:parseFloat(v)});
    if(!j.changed){toast('Already at that price. Nothing was sent. Press Reset first to redo the drop.');return}
    toast('Price set');setTimeout(()=>location.reload(),350)}catch(e){toast(e.message)}}
async function scale(id,pct){try{await api('drop',{listing_id:id,pct:pct});toast(pct<0?'Dropped':'Raised');setTimeout(()=>location.reload(),350)}catch(e){toast(e.message)}}
async function act(name){try{await api(name,{});toast('Done');setTimeout(()=>location.reload(),350)}catch(e){toast(e.message)}}
async function setWindow(){const d=parseInt(document.getElementById('days').value,10);
  try{await api('policy-window',{days:d});toast('Policy updated');setTimeout(()=>location.reload(),350)}catch(e){toast(e.message)}}
function row(r){const tr=document.createElement('tr');
  [r.at,'listing '+r.listing_id+(r.refresh?' (refresh)':''),'$'+Number(r.price).toFixed(2),r.ok?'delivered ('+r.status+')':'FAILED: '+(r.error||'')].forEach((t,i)=>{
    const td=document.createElement('td');td.textContent=t;if(i===3)td.className=r.ok?'ok':'bad';tr.appendChild(td)});return tr}
async function refreshLog(){try{const j=await api('webhook-log');const b=document.getElementById('wlog');b.textContent='';
  if(!j.deliveries.length){const td=document.createElement('td');td.colSpan=4;td.className='muted';td.textContent='No price changes sent yet.';const tr=document.createElement('tr');tr.appendChild(td);b.appendChild(tr)}
  j.deliveries.slice(0,8).forEach(r=>b.appendChild(row(r)))}catch(e){}}
function srow(r){const tr=document.createElement('tr');
  [r.at,r.text,r.target,r.ok?'delivered ('+r.status+')':'FAILED: '+(r.error||'')].forEach((t,i)=>{
    const td=document.createElement('td');td.textContent=t;if(i===3)td.className=r.ok?'ok':'bad';tr.appendChild(td)});return tr}
async function refreshSupport(){try{const j=await api('support-log');const b=document.getElementById('slog');b.textContent='';
  if(!j.messages.length){const td=document.createElement('td');td.colSpan=4;td.className='muted';td.textContent='No support messages yet.';const tr=document.createElement('tr');tr.appendChild(td);b.appendChild(tr)}
  j.messages.slice(0,8).forEach(r=>b.appendChild(srow(r)))}catch(e){}}
// Optional: keep prices "seen within 15 minutes" for the gate's R8a while this page is open. Off by default.
let freshTimer=null;
function toggleFresh(on, everyMs){
  clearInterval(freshTimer);freshTimer=null;
  const label=document.getElementById('fresh-note');
  if(!on){label.textContent='';return}
  const run=async()=>{try{const j=await api('resend',{});label.textContent='Last re-announced '+new Date().toLocaleTimeString()+' ('+j.sent+' prices)'}catch(e){label.textContent='Could not re-announce: '+e.message}};
  run();freshTimer=setInterval(run, everyMs||300000);
}
refreshLog();refreshSupport();setInterval(()=>{refreshLog();refreshSupport()},3000);
"""


def admin() -> str:
    rows = []
    for l in store._state["listings"].values():
        i = l["id"]
        rows.append(f"""<tr><td>{i}</td><td>{esc(l["title"])}<div class="muted mono">{esc(l["sku"])} · {esc(l["variant"])} · {esc(l["category"])}</div></td>
<td><strong>${l["price"]:,.2f}</strong><div class="muted">seed ${l["seed_price"]:,.2f}</div></td>
<td style="white-space:nowrap"><input class="p" id="p{i}" type="number" step="0.01" min="0.01" value="{l["price"]:.2f}" aria-label="New price for listing {i}">
<button type="button" onclick="setPrice({i})">Set</button>
<button type="button" class="alt" onclick="scale({i},-25)">Drop 25%</button>
<button type="button" class="alt" onclick="scale({i},25)">Raise 25%</button></td></tr>""")
    p = store.policy()
    body = f"""<h1>Store admin <span class="muted" style="font-size:16px">(demo control panel)</span></h1>
<p class="muted">Every price change is sent to Clawback's watcher straight away.</p>
<div class="toolbar"><button type="button" onclick="act('prestage')">1. Pre-stage (all but the earbuds)</button>
<button type="button" onclick="setPrice(1, 95.99)">2. Live drop: earbuds to $95.99</button>
<button type="button" class="alt" onclick="act('preset')">Demo preset (all four at once)</button>
<button type="button" class="alt" onclick="act('resend')">Refresh prices (before an approval)</button>
<button type="button" class="alt" onclick="act('reset')">Reset to seed</button></div>
<table><thead><tr><th>#</th><th>Product</th><th>Price</th><th>Change</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class="muted">Before approving the lamp, press <strong>Refresh prices</strong>: the gate only accepts a price seen in the last 15 minutes (R8a). Demo preset: earbuds $95.99 · lamp $199.00 · Blue backpack $64.00 · fan $39.00.</p>
<h2>Price-adjustment window</h2>
<div class="toolbar" style="align-items:center"><input id="days" type="number" min="1" max="90" value="{p["adjust_window_days"]}" style="width:90px" aria-label="Adjustment window in days"> days from purchase
<button type="button" onclick="setWindow()">Update policy page</button><span class="muted">policy revision {p["revision"]}</span></div>
<p class="muted">For the live policy-edit stretch: this changes the store's policy page and <span class="mono">/mock/api/policy</span>. Senso and <span class="mono">store_policies</span> still need the same edit.</p>
<h2>Keep prices fresh</h2>
<p><label><input type="checkbox" id="keep-fresh" onchange="toggleFresh(this.checked)"> Re-announce the current prices every 5 minutes while this page is open</label>
<span id="fresh-note" class="muted" style="margin-left:10px"></span></p>
<p class="muted">Off by default. Turn it on shortly before the lamp approval so the price is always "seen within 15 minutes". It sends the same webhook as "Refresh prices", flagged <span class="mono">refresh:true</span>.</p>
<h2>Webhooks sent to Clawback</h2>
<table><thead><tr><th>Time (UTC)</th><th>Listing</th><th>Price</th><th>Result</th></tr></thead><tbody id="wlog"></tbody></table>
<h2>Support messages (presenter view)</h2>
<table><thead><tr><th>Time (UTC)</th><th>Message</th><th>Conversation</th><th>Result</th></tr></thead><tbody id="slog"></tbody></table>"""
    return layout("Admin", body, ADMIN_SCRIPT)


SUPPORT_SCRIPT = """
const box=document.getElementById('msg'),btn=document.getElementById('send'),st=document.getElementById('status');
function mine(m){const li=document.createElement('li');li.textContent=m;return li}
async function send(){const text=box.value.trim();if(!text){st.textContent='Type a message first.';return}
  btn.disabled=true;st.textContent='Sending...';
  try{const j=await api('support',{text});st.textContent=j.accepted?'Sent. The customer will see this in their conversation.':'Could not send.';
    document.getElementById('sent').prepend(mine(text));box.value=''}catch(e){st.textContent='Could not send: '+e.message}
  btn.disabled=false}
btn.addEventListener('click',send);
box.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))send()});
"""


def support() -> str:
    body = """<h1>Mock Store support</h1>
<p class="muted">Reply to the customer's open price-adjustment conversation.</p>
<div class="note"><label for="msg" style="font-weight:600">Your reply</label>
<textarea id="msg" maxlength="2000" placeholder="Type a message to the customer..."></textarea>
<div class="toolbar"><button id="send" type="button">Send reply</button><span id="status" class="muted" role="status"></span></div></div>
<h2>Sent in this session</h2><ul id="sent" class="muted" style="padding-left:20px"></ul>"""
    return layout("Support", body, SUPPORT_SCRIPT)
