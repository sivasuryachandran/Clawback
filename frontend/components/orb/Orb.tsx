"use client";
/**
 * The Clawback orb (Teammate C; section 3.11).
 *
 * A soft animated sphere: breathes when idle, glows while listening, spins while thinking, ripples
 * while speaking. Push-to-talk (hold the orb or press Space), with a text box and sample questions so
 * the demo never depends on the microphone. Uses only the browser's own speech APIs (Chrome).
 *
 * It can explain, summarise and PAUSE. It can never approve, buy, send, edit limits or un-pause: the
 * server (/voice/ask) refuses those, and this component has no way to trigger them either.
 *
 * Drop-in:      <Orb />
 * Controlled:   <Orb state="listening" onAsk={(text) => ...} onOpenCard={(id) => ...} />
 */
import { FormEvent, PointerEvent as ReactPointerEvent, useCallback, useEffect, useRef, useState } from "react";
import "./orb.css";
import { API_URL } from "./config";

export type OrbState = "idle" | "listening" | "thinking" | "speaking";

export interface StoryLine { icon: string; text: string }
export interface AskResult {
  intent: string;
  answer: string;
  open_card_id?: string | null;
  lines?: StoryLine[];
  approval_id?: number | null;
}

export interface OrbProps {
  /** Force the visual state (otherwise the orb manages it). */
  state?: OrbState;
  /** Override what happens to a question. Return an AskResult to have the orb show and speak it. */
  onAsk?: (text: string) => void | AskResult | Promise<void | AskResult>;
  /** Called when an answer points at a card on screen (the opportunity id). Default: scroll to and flash it. */
  onOpenCard?: (id: string) => void;
  apiUrl?: string;
  /** Orb diameter in px (default 168). */
  size?: number;
  /** Sample questions shown as chips. */
  suggestions?: string[];
  className?: string;
}

const DEFAULT_SUGGESTIONS = ["What did you do today?", "Why did you stop the backpack?", "Anything need my OK?"];

// Minimal typings for the Web Speech API (not in lib.dom).
interface SpeechRecognitionLike {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  if (el.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(el.tagName) && !el.hasAttribute("data-orb-button");
}

function newSessionId(): string {
  try { return crypto.randomUUID(); } catch { return "s" + Math.random().toString(36).slice(2, 12); }
}

export default function Orb({ state, onAsk, onOpenCard, apiUrl = API_URL, size = 168, suggestions = DEFAULT_SUGGESTIONS, className }: OrbProps) {
  const [phase, setPhase] = useState<OrbState>("idle");
  const [heard, setHeard] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [typed, setTyped] = useState("");
  const [canListen, setCanListen] = useState(false);

  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const heardRef = useRef("");
  const listeningRef = useRef(false);
  const sessionRef = useRef<string>("");
  const speakTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mutedRef = useRef(false);
  const shown: OrbState = state ?? phase;

  useEffect(() => {
    sessionRef.current = newSessionId();
    setCanListen(getRecognition() !== null);
  }, []);
  useEffect(() => { mutedRef.current = muted; if (muted) window.speechSynthesis?.cancel(); }, [muted]);

  const openCard = useCallback((id: string) => {
    if (onOpenCard) { onOpenCard(id); return; }
    // The card for an opportunity is found by any of the usual markings, so this works with whatever the
    // Money back screen uses: data-card-id / data-opportunity-id attributes, or an id of card-N / opportunity-N / opp-N.
    const safe = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(id) : id.replace(/[^\w-]/g, "");
    const el = document.querySelector<HTMLElement>(
      [`[data-card-id="${safe}"]`, `[data-opportunity-id="${safe}"]`, `#card-${safe}`, `#opportunity-${safe}`, `#opp-${safe}`].join(", "));
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("orb-flash");
      setTimeout(() => el.classList.remove("orb-flash"), 1800);
    }
  }, [onOpenCard]);

  const speak = useCallback((text: string) => {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
    if (!synth || mutedRef.current || !text) { setPhase("idle"); return; }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US"; u.rate = 1;
    const voice = synth.getVoices().find((v) => v.lang === "en-US") ?? synth.getVoices().find((v) => v.lang.startsWith("en"));
    if (voice) u.voice = voice;
    const done = () => { if (speakTimer.current) clearTimeout(speakTimer.current); setPhase("idle"); };
    u.onstart = () => setPhase("speaking");
    u.onend = done;
    u.onerror = done;
    setPhase("speaking");
    // some browsers never fire onend; answers are under 25 words, so 12 s is always enough
    if (speakTimer.current) clearTimeout(speakTimer.current);
    speakTimer.current = setTimeout(() => setPhase("idle"), 12000);
    synth.speak(u);
  }, []);

  const ask = useCallback(async (raw: string) => {
    const text = raw.trim();
    if (!text) { setPhase("idle"); return; }
    setError(null); setHeard(text); setPhase("thinking");
    try {
      let res: void | AskResult;
      if (onAsk) {
        res = await onAsk(text);
      } else {
        const mock = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mock") === "1";
        const r = await fetch(`${apiUrl}/voice/ask${mock ? "?mock=1" : ""}`, {
          method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, session_id: sessionRef.current }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        res = (await r.json()) as AskResult;
      }
      if (!res) { setPhase("idle"); return; }
      setResult(res);
      if (res.open_card_id) openCard(String(res.open_card_id));
      speak(res.answer);
    } catch {
      setResult(null);
      setError("Something went wrong on our side. Nothing was sent or bought.");
      setPhase("idle");
    }
  }, [apiUrl, onAsk, openCard, speak]);

  const stopListening = useCallback(() => { try { recRef.current?.stop(); } catch { /* already stopped */ } }, []);

  const startListening = useCallback(() => {
    const SR = getRecognition();
    if (!SR) { setError("Voice needs Chrome. Type your question below."); return; }
    if (listeningRef.current) return;
    window.speechSynthesis?.cancel();
    const rec = new SR();
    rec.lang = "en-US"; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    heardRef.current = "";
    rec.onresult = (e) => {
      let t = "";
      for (let i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
      heardRef.current = t; setHeard(t);
    };
    rec.onerror = (e) => {
      setError(e.error === "not-allowed" || e.error === "service-not-allowed" ? "The microphone is blocked. Type your question below."
        : e.error === "no-speech" ? "I didn't hear anything. Hold the orb and try again."
        : e.error === "aborted" ? null : "Voice isn't available right now. Type your question below.");
    };
    rec.onend = () => {
      listeningRef.current = false; recRef.current = null;
      const text = heardRef.current.trim();
      if (text) void ask(text); else setPhase("idle");
    };
    try {
      recRef.current = rec; listeningRef.current = true;
      setError(null); setHeard(""); setPhase("listening");
      rec.start();
    } catch {
      listeningRef.current = false; recRef.current = null; setPhase("idle");
      setError("Voice isn't available right now. Type your question below.");
    }
  }, [ask]);

  // Space = push to talk (only when you're not typing or on another control)
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return;
      e.preventDefault(); startListening();
    };
    const up = (e: KeyboardEvent) => { if (e.code === "Space" && listeningRef.current) { e.preventDefault(); stopListening(); } };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [startListening, stopListening]);

  useEffect(() => () => {
    try { recRef.current?.abort(); } catch { /* ignore */ }
    window.speechSynthesis?.cancel();
    if (speakTimer.current) clearTimeout(speakTimer.current);
  }, []);

  const onDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    e.preventDefault();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* not supported */ }
    startListening();
  };
  const onUp = () => stopListening();
  const submit = (e: FormEvent) => { e.preventDefault(); const t = typed; setTyped(""); void ask(t); };

  const status =
    shown === "listening" ? (heard || "Listening…")
    : shown === "thinking" ? "Thinking…"
    : shown === "speaking" ? "Speaking…"
    : canListen ? "Hold the orb or press Space, then ask" : "Type a question below";

  return (
    <section className={`orb-root ${className ?? ""}`} data-state={shown} aria-label="Clawback assistant"
      style={{ ["--orb-size" as string]: `${size}px` }}>
      <div className="orb-stage">
        <span className="orb-ring" aria-hidden="true" />
        <span className="orb-ripple" aria-hidden="true" /><span className="orb-ripple" aria-hidden="true" /><span className="orb-ripple" aria-hidden="true" />
        <button type="button" className="orb-btn" data-orb-button aria-label="Hold to talk to Clawback"
          onPointerDown={onDown} onPointerUp={onUp} onPointerCancel={onUp} onContextMenu={(e) => e.preventDefault()}>
          <span className="orb-sphere" aria-hidden="true"><span className="orb-sheen" /></span>
        </button>
      </div>

      <div className="orb-state" aria-hidden="true">{status}</div>
      <div className="orb-answer" role="status" aria-live="polite">{error ?? result?.answer ?? ""}</div>

      {result?.lines && result.lines.length > 0 && (
        <ul className="orb-story" aria-label="Why">
          {result.lines.map((l, i) => (<li key={i}><span aria-hidden="true">{l.icon}</span><span>{l.text}</span></li>))}
        </ul>
      )}

      {result?.intent === "pause_confirm" && (
        <div className="orb-confirm" role="group" aria-label="Confirm pause">
          <button type="button" className="orb-btn-small" onClick={() => void ask("yes")}>Yes, pause Clawback</button>
          <button type="button" className="orb-btn-small quiet" onClick={() => void ask("no")}>No</button>
        </div>
      )}

      <form className="orb-form" onSubmit={submit}>
        <label className="orb-sr" htmlFor="orb-input">Ask Clawback a question</label>
        <input id="orb-input" value={typed} onChange={(e) => setTyped(e.target.value)} maxLength={300}
          placeholder="Ask: why did you stop the backpack?" autoComplete="off" enterKeyHint="send" />
        <button type="submit" className="orb-btn-small" disabled={!typed.trim() || shown === "thinking"}>Ask</button>
      </form>

      <div className="orb-chips">
        {suggestions.map((s) => (<button key={s} type="button" className="orb-chip" onClick={() => void ask(s)}>{s}</button>))}
      </div>

      <div className="orb-tools">
        <span>I can explain or pause. Approvals happen on screen.</span>
        <button type="button" aria-pressed={muted} onClick={() => setMuted((m) => !m)}>{muted ? "Unmute voice" : "Mute voice"}</button>
      </div>
    </section>
  );
}
