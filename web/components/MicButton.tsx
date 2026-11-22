"use client";
import { useEffect, useRef, useState } from "react";
import { Mic } from "lucide-react";

// Push to talk: hold the button, speak, let go. The browser's own speech recognition (Web Speech API) turns speech into text; the text goes
// into the chat box through onText, and the person reads it, edits it and presses Send. This component never sends a message and never
// touches a card: it only knows onText. No audio reaches our server: the browser's speech service hears it (Chrome: Google, Safari: Apple).

type Result = { isFinal: boolean; 0: { transcript: string } };
type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<Result> }) => void) | null;
  onend: (() => void) | null; onerror: ((e: { error: string }) => void) | null;
  start(): void; stop(): void; abort(): void;
};

function recognitionClass(): (new () => Recognition) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as (new () => Recognition) | null;
}

// the app's language when the page says French, else the browser's: French for fr-*, English otherwise
export function speechLang(): string {
  const page = document.documentElement.lang.toLowerCase(), browser = (navigator.language || "en").toLowerCase();
  return page.startsWith("fr") || browser.startsWith("fr") ? "fr-FR" : browser.startsWith("en") ? navigator.language : "en-GB";
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Microphone access is blocked. Allow it in the browser to use voice.",
  "service-not-allowed": "Microphone access is blocked. Allow it in the browser to use voice.",
  "no-speech": "Nothing was heard. Hold the button while you speak.",
  "audio-capture": "No microphone was found.",
  network: "Voice needs the browser's speech service, which could not be reached.",
};

export function useSpeechSupported() {
  const [ok, setOk] = useState(false);
  useEffect(() => setOk(recognitionClass() !== null), []);      // after the first render: the server has no window
  return ok;
}

export function MicButton({ text, onText, onProblem, disabled }: {
  text: string; onText: (t: string) => void; onProblem: (p: string | null) => void; disabled?: boolean;
}) {
  const [listening, setListening] = useState(false);
  const rec = useRef<Recognition | null>(null);
  const base = useRef("");            // what was in the box before speaking: the transcript is added after it
  const heard = useRef("");           // the final parts so far

  useEffect(() => () => rec.current?.abort(), []);

  function start() {
    const Klass = recognitionClass();
    if (!Klass || rec.current || disabled) return;
    const r = new Klass();
    r.lang = speechLang();
    r.continuous = true;
    r.interimResults = true;
    base.current = text.trim() ? text.trimEnd() + " " : "";
    heard.current = "";
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const part = e.results[i][0].transcript;
        if (e.results[i].isFinal) heard.current += part; else interim += part;
      }
      onText((base.current + heard.current + interim).replace(/\s+/g, " ").trimStart());
    };
    r.onerror = (e) => { if (e.error !== "aborted") onProblem(ERRORS[e.error] ?? "Voice input stopped. Try again, or type your message."); };
    r.onend = () => { rec.current = null; setListening(false); };
    onProblem(null);
    rec.current = r;
    setListening(true);
    try { r.start(); } catch { rec.current = null; setListening(false); }
  }

  function stop() { rec.current?.stop(); }        // the last words still arrive (onresult), then onend

  return (
    <button type="button" disabled={disabled}
            className={`h-8 w-8 shrink-0 select-none touch-none px-0 ${listening ? "btn-primary" : "btn-ghost"}`}
            aria-label={listening ? "Listening: release to stop" : "Hold to speak"} aria-pressed={listening}
            title="Hold to speak"
            onPointerDown={(e) => { e.preventDefault(); try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* no capture: pointerup still stops */ } start(); }}
            onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={stop}
            onContextMenu={(e) => e.preventDefault()}
            onKeyDown={(e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); start(); } }}
            onKeyUp={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); stop(); } }}>
      <Mic size={16} aria-hidden />
    </button>
  );
}
