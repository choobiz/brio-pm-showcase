// Speech-to-text, using what the browser already has.
//
// ⚠️ WHY THE WEB SPEECH API AND NOT A TRANSCRIPTION SERVICE. Nothing to deploy,
// no per-minute cost, no audio leaving the device on Safari, and it starts
// returning words in under a second. The cost is that it is not universal —
// Chrome, Edge and Safari have it; Firefox does not. That is a real gap and the
// UI must say so rather than rendering nothing, which reads as a broken button.
//
// ⚠️ A TRANSCRIPT IS A DRAFT, NEVER A RECORD. General-purpose recognisers are
// trained on ordinary speech and construction vocabulary is not that: "soffit",
// "rebar", "parging", "RSI", "mullion" and every sheet number come back mangled
// often enough that submitting a transcript unread would file nonsense under
// someone's name. Everything here fills an EDITABLE field. Nothing it produces
// is ever sent anywhere without a person seeing it first.

import { transcriptFrom } from "./speechText";

/** The two names the same API ships under. */
type SpeechRecognitionCtor = new () => ISpeechRecognition;

export interface ISpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

export interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: {
    length: number;
    [i: number]: { isFinal: boolean; 0: { transcript: string; confidence: number }; length: number };
  };
}

function ctor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function speechSupported(): boolean {
  return ctor() !== null;
}

/** Why it is unavailable, in words the reader can act on. */
export function speechUnavailableReason(): string | null {
  if (typeof window === "undefined") return null;
  if (ctor()) return null;
  const ua = navigator.userAgent;
  if (/Firefox\//i.test(ua)) {
    return "Firefox doesn't support speech input. Chrome, Edge and Safari do — or type instead.";
  }
  return "This browser doesn't support speech input. Type instead, or try Chrome or Safari.";
}

/** What went wrong, keyed on the spec's error codes. */
export function describeSpeechError(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access was blocked. Allow it in the browser's site settings, then try again.";
    case "no-speech":
      return "Nothing was heard. Check the microphone and try again.";
    case "audio-capture":
      return "No microphone was found.";
    case "network":
      return "Speech recognition needs a connection and couldn't reach the service.";
    case "aborted":
      return "Recording stopped.";
    default:
      return `Speech input stopped (${code}).`;
  }
}

export interface SpeechSession {
  stop(): void;
}

/**
 * Start listening. `onText` receives the full text so far on every update:
 * `final` is what the recogniser has committed, `interim` is its current guess.
 *
 * Continuous, because the shortest useful thing anyone says on a site is longer
 * than the default single-utterance window, which cuts off at the first pause.
 */
export function listen(opts: {
  lang?: string;
  onText: (final: string, interim: string) => void;
  onError: (message: string) => void;
  onEnd: () => void;
}): SpeechSession | null {
  const C = ctor();
  if (!C) return null;

  const rec = new C();
  rec.lang = opts.lang ?? "en-CA";
  rec.continuous = true;
  rec.interimResults = true;
  rec.maxAlternatives = 1;

  let stopped = false;

  // The whole list, every time: appending from resultIndex wrote words twice
  // (see lib/speechText.ts).
  rec.onresult = (e) => {
    const { final, interim } = transcriptFrom(e.results);
    opts.onText(final, interim);
  };

  rec.onerror = (e) => {
    // "aborted" is what a deliberate stop() looks like; reporting it as a
    // failure would put an error box on every normal finish.
    if (e.error === "aborted" && stopped) return;
    opts.onError(describeSpeechError(e.error));
  };

  rec.onend = () => opts.onEnd();

  try {
    rec.start();
  } catch {
    // start() throws if called while already running. Treat as already-on.
    return { stop: () => { stopped = true; rec.abort(); } };
  }

  return {
    stop: () => {
      stopped = true;
      try { rec.stop(); } catch { /* already stopped */ }
    },
  };
}
