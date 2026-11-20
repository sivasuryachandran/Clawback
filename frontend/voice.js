// Ask box + one-click voice (Web Speech API, Chrome). Explains what Clawback did and can pause it.
// It can NEVER approve, buy, send, edit limits or un-pause. C's POST /voice/ask is used if it exists;
// otherwise the same fixed intents are answered locally from the activity data.
window.Voice = (function () {
  const REFUSE = "I can tell you what I did, why, or pause. Approvals happen on screen.";
  let pendingPause = false, ctx;
  const el = (id) => document.getElementById(id);

  function words(s) { return String(s).toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter((w) => w.length > 2); }
  function local(text) {
    const t = text.toLowerCase().trim(), acts = ctx.acts();
    if (pendingPause) { pendingPause = false; if (/^(yes|yeah|yep|sure|ok|okay)\b/.test(t)) return { answer: "Paused. Clawback won't act until you sign a new Pact.", pause: true }; return { answer: "Okay, I won't pause." }; }
    if (/\b(approve|buy|purchase|spend|send|email|limit|unpause|resume|cancel|delete)\b/.test(t) && !/\bwhy\b/.test(t)) return { answer: REFUSE };
    if (/\bpause\b|\bstop clawback\b/.test(t)) { pendingPause = true; return { answer: "Say yes to pause Clawback." }; }
    if (/\bwhy\b|\bstopped?\b/.test(t)) {
      const q = new Set(words(t)); let best = null, score = 0;
      for (const a of acts) { const sc = words(a.item_title + " " + (a.variant || "")).filter((w) => q.has(w)).length; if (sc > score) { score = sc; best = a; } }
      if (!best) return { answer: "Which item? Try: why did you stop the backpack?" };
      return { answer: best.decision === "BLOCK" ? `We didn't act on your ${best.item_title}. ${ctx.ruleSentence(best)}` : `Your ${best.item_title}: ${best.decision === "ASK" ? "waiting for your OK" : "handled, everything checked out"}.` };
    }
    if (/\b(ok|okay|approval|waiting|need|pending)\b/.test(t)) {
      const a = acts.find((x) => x.decision === "ASK" && x.approval_id);
      return a ? { answer: `Your ${a.item_title} needs your OK. Opening it.`, open: true } : { answer: "Nothing needs your OK right now." };
    }
    if (/\b(today|back|how much|did you do|summary|status)\b/.test(t)) {
      const latest = new Map(); acts.forEach((a) => { const k = a.line_item_id ?? a.opportunity_id; if (!latest.has(k)) latest.set(k, a); });
      const v = [...latest.values()], w = v.filter((x) => x.decision === "ASK").length, s = v.filter((x) => x.decision === "BLOCK").length;
      return { answer: `You've got ${ctx.usd(ctx.backTotal(acts))} back. ${w} waiting for you, ${s} stopped by your rules.` };
    }
    return { answer: REFUSE };
  }

  async function ask(text) {
    if (!text.trim()) return;
    let r = null;
    if (!pendingPause) { try { r = await ctx.api("/voice/ask", { method: "POST", body: JSON.stringify({ text }) }); } catch { r = null; } }
    if (!r || !r.answer) r = local(text);
    show(r.answer);
    if (r.open) ctx.openNeeds();
    if (r.pause) { try { await ctx.pause(); } catch (e) { show(e.message); } }
  }
  function show(msg) { el("answer").textContent = msg; openPanel(); speak(msg); }
  function openPanel() {                                           // floating orb: pop the answer box open
    if (document.body.classList.contains("mini")) { document.body.classList.add("ask-open"); el("ask").hidden = false; }
  }
  function speak(msg) {
    if (!("speechSynthesis" in window)) return;
    speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(msg);
    u.onstart = () => ctx.orb("speaking"); u.onend = u.onerror = () => ctx.orb("idle");
    speechSynthesis.speak(u);
  }

  // One click starts listening; it stops by itself when you finish speaking, then answers out loud.
  // Click again while listening to cancel.
  let rec = null, listening = false, heard = "", silenceTimer = null;
  const SILENCE_MS = 5000;           // 5 seconds with nothing new heard -> answer (or say we heard nothing)
  const armSilence = () => { clearTimeout(silenceTimer); silenceTimer = setTimeout(() => { try { rec && rec.stop(); } catch {} }, SILENCE_MS); };
  function setLive(on) {
    listening = on;
    const b = el("orbbtn"); b.classList.toggle("live", on); b.title = on ? "Listening: click to cancel" : "Click the sphere and speak";
    el("orb-hint").textContent = on ? "Listening… click the sphere to cancel" : "Click the sphere and speak, or type below";
    ctx.orb(on ? "listening" : "idle");
  }
  function stopListening(cancel) { clearTimeout(silenceTimer); if (cancel) { heard = ""; listening = false; } if (rec) { try { rec.abort ? (cancel ? rec.abort() : rec.stop()) : rec.stop(); } catch {} } }
  function startListening() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { show("Voice isn't supported in this browser. Type your question instead."); return; }
    if ("speechSynthesis" in window) speechSynthesis.cancel();       // stop talking before we listen
    heard = ""; rec = new SR(); rec.lang = "en-US"; rec.continuous = true; rec.interimResults = true; rec.maxAlternatives = 1;
    rec.onresult = (e) => { let t = ""; for (const r of e.results) t += r[0].transcript; heard = t; el("asktext").value = t; armSilence(); };
    rec.onerror = (e) => {
      clearTimeout(silenceTimer); setLive(false);
      if (e.error === "no-speech") { heard = ""; show("I didn't hear anything. Click the sphere and try again."); return; }
      heard = "";
      if (e.error === "not-allowed" || e.error === "service-not-allowed") show("Microphone is blocked. Allow it in the browser, or type your question.");
      else if (e.error !== "aborted") show("I couldn't hear that. Try again or type it.");
    };
    rec.onend = () => {
      clearTimeout(silenceTimer);
      const wasListening = listening; setLive(false);
      const q = heard.trim(); heard = "";
      if (!wasListening) return;                                   // cancelled by the user
      if (q) { el("asktext").value = ""; ask(q); } else show("I didn't hear anything. Click the sphere and try again.");
    };
    try { rec.start(); setLive(true); el("answer").textContent = "Listening…"; openPanel(); armSilence(); } catch { setLive(false); }
  }
  function toggleMic() { if (listening) stopListening(true); else startListening(); }

  function init(c) {
    ctx = c;
    el("askform").addEventListener("submit", (e) => { e.preventDefault(); ask(el("asktext").value); el("asktext").value = ""; });

    el("orbbtn").addEventListener("click", toggleMic);
    addEventListener("keydown", (e) => { if (e.code === "Space" && !e.repeat && !/INPUT|TEXTAREA|BUTTON|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); toggleMic(); } });
    addEventListener("hashchange", () => { document.body.classList.remove("ask-open"); });
  }
  return { init, local: (t) => local(t) };
})();
