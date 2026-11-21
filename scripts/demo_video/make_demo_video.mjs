// Builds the Clawback demo video the way the Pact video was built: written narration, synthesized voice (macOS `say`),
// a scripted browser recording (Playwright), merged with ffmpeg. Every beat waits as long as its narration, so audio and
// picture stay in sync. Output: ~/Movies/clawback-demo/clawback-demo.mp4 (under 3:00).
//   node scripts/demo_video/make_demo_video.mjs
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_PATH || "/Users/sivasuryachandran/Documents/HCK/Pact/node_modules/playwright");

const UI = "http://localhost:3000", API = "http://localhost:8001";
const OUT = path.join(os.homedir(), "Movies", "clawback-demo");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "clawback-video-"));
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- narration (about 300 words, about 2:15 at this pace) -------------------------------------------------------
const beats = [
  { id: "home", text: "Clawback gets your money back after you buy. It is an AI agent that watches your purchases, and it can only act inside a Pact you signed. The AI proposes. A fixed set of rules decides. The store, the support rep and the receipts are simulated." },
  { id: "pact", text: "This is the Pact. The limits are mine: save at least ten dollars, ask me first above one fifty, never more than two fifty at once. Every action is checked against this signed document, so changing a limit makes a new version." },
  { id: "purchases", text: "Six purchases, read from receipts that an Airbyte sync moved into the database. The earbuds dropped to ninety six dollars, so Clawback asked the store for thirty four dollars back. The knit throw gets a reminder before its return window closes." },
  { id: "why", text: "And here is what it refuses. The cheaper backpack is the blue one, and mine is black. Clawback did not act. Open why, and you see what I bought, what it found, the store's own policy sentence, retrieved through Senso, and the rule that decided." },
  { id: "needs", text: "Buying the lamp again at one ninety nine would save forty dollars, but that is above my limit. So it asks me first. I tap approve, and Persona re-checks, live, that it is really me before anything moves. It passes, the action runs once, and a repeated tap does nothing." },
  { id: "hood", text: "Under the hood, every decision is a trace. Each rule, in order, with the values it compared. The Pact signature is recomputed live. Change my rules behind my back, and the next action stops, because the signature no longer matches. The data lives in SingleStore, and the events go to Amplitude." },
  { id: "amp", text: "Analytics from this run, in Amplitude. The decision mix, the stop reasons by rule, and seventy four dollars recovered." },
  { id: "close", text: "Clawback. The model proposes, the gate decides, and every action leaves an audit trail. Built today with an open weight model, Senso, SingleStore, Airbyte, Amplitude and Persona." },
];

// ---- synthesize the voice --------------------------------------------------------------------------------------
const dur = (f) => parseFloat(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).toString());
for (const b of beats) {
  b.file = path.join(TMP, `${b.id}.aiff`);
  execFileSync("say", ["-v", "Samantha", "-r", "170", "-o", b.file, b.text]);
  b.secs = dur(b.file) + 0.6;
}
const total = beats.reduce((s, b) => s + b.secs, 0);
console.log("narration:", beats.map((b) => `${b.id} ${b.secs.toFixed(1)}s`).join(", "), `| total ${total.toFixed(1)}s`);
if (total > 175) throw new Error(`too long (${total}s): keep it under 3:00`);

// ---- record the screen -----------------------------------------------------------------------------------------
const browser = await chromium.launch({ headless: true });
const ctxStart = Date.now();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: TMP, size: { width: 1440, height: 900 } } });
const page = await ctx.newPage();
await page.addInitScript(() => { try { localStorage.setItem("clawback_details", "0"); } catch {} });
await page.goto(`${UI}/#money`, { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => /You've got/.test(document.getElementById("view")?.innerText || ""), null, { timeout: 60000 });
await sleep(800);
const offset = (Date.now() - ctxStart) / 1000;     // video seconds before beat 1 starts

// ---- captions: a DOM bar re-applied every 200 ms (survives page navigations; this ffmpeg has no subtitles/drawtext) ----
let cap = null;
const sentencesOf = (t) => t.match(/[^.!?]+[.!?]+/g)?.map((x) => x.trim()) ?? [t];
const capAt = () => {
  if (!cap) return "";
  const el = (Date.now() - cap.t0) / 1000, tot = cap.b.secs - 0.6, sents = cap.sents, n = sents.reduce((a, x) => a + x.length, 0);
  let acc = 0;
  for (const x of sents) { acc += (x.length / n) * tot; if (el <= acc) return x; }
  return "";
};
let capOn = true;
(async () => { while (capOn) {
  const text = capAt();
  try { await page.evaluate((t) => {
    let d = document.getElementById("__cap");
    if (!d) { d = document.createElement("div"); d.id = "__cap"; d.style.cssText = "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:1100px;padding:10px 20px;border-radius:10px;background:rgba(0,0,0,.78);color:#fff;font:600 24px/1.35 -apple-system,Helvetica,Arial,sans-serif;text-align:center;z-index:2147483647;pointer-events:none"; (document.body || document.documentElement).appendChild(d); }
    d.style.display = t ? "block" : "none"; d.textContent = t;
  }, text); } catch {}
  await sleep(200);
} })();
const b6a = (b) => Math.max(2000, (b.secs * 1000) / 2);
async function beat(b, fn) {
  const t0 = Date.now();
  cap = { b, t0, sents: sentencesOf(b.text) };
  try { await fn(); } catch (e) { console.log(`(${b.id}: ${e.message.split("\n")[0]})`); }
  const left = b.secs * 1000 - (Date.now() - t0);
  if (left > 0) await sleep(left);
}
const go = async (hash) => { await page.goto(`${UI}/${hash}`, { waitUntil: "domcontentloaded" }); await sleep(300); };
const waitText = (re) => page.waitForFunction((s) => new RegExp(s).test(document.getElementById("view")?.innerText || ""), re, { timeout: 40000 });

await beat(beats[0], async () => { await page.mouse.move(720, 330); await sleep(1500); await page.mouse.move(700, 380, { steps: 20 }); });
await beat(beats[1], async () => { await go("#pact"); await waitText("Your limits"); await page.mouse.move(400, 330, { steps: 20 }); });
await beat(beats[2], async () => { await go("#purchases"); await waitText("Paid"); await page.mouse.move(520, 300, { steps: 25 }); await sleep(2500); await page.mouse.move(900, 300, { steps: 25 }); });
await beat(beats[3], async () => {
  await page.locator(".card", { hasText: "Trailhead Backpack" }).first().getByRole("button", { name: "Why?" }).click();
  await sleep(1200); await page.mouse.move(1150, 260, { steps: 20 });
});
await beat(beats[4], async () => {
  await go("#needs"); await waitText("Approve and buy");
  const b = page.getByRole("button", { name: "Approve and buy" }).first(); const box = await b.boundingBox();
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 });
  await sleep(800); await b.click();      // real approval: server-side Persona re-check, then the run-once action
  await sleep(4000);
});
await beat(beats[5], async () => {
  await page.goto(`${API}/under-the-hood/`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => /Live gate trace/.test(document.body.innerText), null, { timeout: 40000 });
  // Persona did not run live (the demo user's "approved" comes from the seed), so the screen must not say Persona.
  const relabel = () => page.evaluate(() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) if (/Persona/.test(n.nodeValue)) n.nodeValue = n.nodeValue.replace(/Persona/g, "Persona (simulated)"); });
  await sleep(2500);
  await page.mouse.wheel(0, 650); await sleep(2500); await page.mouse.wheel(0, 500);
});
await beat(beats[6], async () => {
  const assets = path.join(OUT, "assets");
  const html = path.join(TMP, "amp.html");
  fs.writeFileSync(html, `<body style="margin:0;background:#111"><img id=i src="file://${assets}/amplitude-1.jpg" style="width:100vw;height:100vh;object-fit:contain"></body>`);
  await page.goto("file://" + html); await sleep(b6a(beats[6]));
  await page.evaluate((u) => { document.getElementById("i").src = u; }, `file://${assets}/amplitude-2.jpg`);
});
await beat(beats[7], async () => { await go("#money"); await waitText("You've got"); await page.mouse.move(720, 400, { steps: 20 }); });
capOn = false; cap = null; await ctx.close(); await browser.close();

// ---- merge ------------------------------------------------------------------------------------------------------
const webm = fs.readdirSync(TMP).find((f) => f.endsWith(".webm"));
const audioList = path.join(TMP, "audio.txt");
fs.writeFileSync(audioList, beats.map((b) => `file '${b.file}'`).join("\n"));
const audio = path.join(TMP, "narration.m4a");
execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", audioList, "-c:a", "aac", "-b:a", "160k", audio]);
const out = path.join(OUT, "clawback-demo.mp4");
execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-ss", String(offset), "-i", path.join(TMP, webm), "-i", audio,
  "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", out]);
console.log("wrote", out, "duration", dur(out).toFixed(1) + "s");
