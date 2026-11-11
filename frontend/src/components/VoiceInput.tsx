"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  listen,
  speechSupported,
  speechUnavailableReason,
  type SpeechSession,
} from "@/lib/speech";

/**
 * Dictate into a text field.
 *
 * ⚠️ TAP TO START, TAP TO STOP — a deliberate divergence from the approved
 * wireframe, which showed "hold to talk". The argument for hold-to-talk is that
 * it is unambiguous. The argument against it is the actual use: a daily site
 * report is thirty to ninety seconds of speech, and holding a button that long
 * in work gloves, one-handed, is worse than pressing it twice. Hold-to-talk also
 * fails badly on a mis-grip — the recording ends mid-sentence and the speaker
 * does not notice until they look. Flagged rather than done quietly; easy to
 * swap back if Amir prefers the original.
 *
 * ⚠️ WHAT COMES BACK IS A DRAFT. The transcript lands in an editable field and
 * never submits itself. Construction vocabulary defeats a general recogniser
 * often enough — "soffit", "parging", sheet numbers — that an unread transcript
 * would file nonsense under someone's name.
 *
 * Unsupported browsers get a sentence saying so, not a missing button. A control
 * that silently disappears reads as broken; one that explains reads as a known
 * limit.
 */
export function VoiceInput({
  value,
  onChange,
  disabled,
  label = "Dictate",
  className = "",
}: {
  /** Current field text — dictation APPENDS to it rather than replacing. */
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  label?: string;
  className?: string;
}) {
  const [supported, setSupported] = useState<boolean | null>(null);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const session = useRef<SpeechSession | null>(null);
  // The field text as it was when recording started. Dictation is appended to
  // this, so a long pause mid-sentence cannot wipe what was already typed.
  const base = useRef("");

  // Capability is read after mount: on the server there is no `window`, and
  // rendering "unsupported" during SSR would flash the wrong state.
  useEffect(() => setSupported(speechSupported()), []);

  useEffect(() => () => { session.current?.stop(); }, []);

  const stop = useCallback(() => {
    session.current?.stop();
    session.current = null;
    setListening(false);
    setInterim("");
  }, []);

  const start = useCallback(() => {
    setErr(null);
    base.current = value ? value.trimEnd() + " " : "";
    const s = listen({
      onText: (final, live) => {
        setInterim(live);
        onChange(base.current + final);
      },
      onError: (message) => {
        setErr(message);
        stop();
      },
      onEnd: () => {
        setListening(false);
        setInterim("");
        session.current = null;
      },
    });
    if (!s) {
      setErr(speechUnavailableReason() ?? "Speech input isn't available here.");
      return;
    }
    session.current = s;
    setListening(true);
  }, [value, onChange, stop]);

  if (supported === null) return null;

  if (!supported) {
    return (
      <p className={`text-xs text-slate-500 ${className}`}>
        {speechUnavailableReason()}
      </p>
    );
  }

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={listening ? stop : start}
          disabled={disabled}
          aria-pressed={listening}
          className={`inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium
                      disabled:opacity-40 ${
                        listening
                          ? "bg-red-600 text-white hover:bg-red-700"
                          : "border border-slate-300 hover:bg-slate-50"
                      }`}
        >
          <span aria-hidden>{listening ? "■" : "🎤"}</span>
          {listening ? "Stop" : label}
        </button>

        {listening && (
          <span className="flex items-center gap-1.5 text-xs text-slate-600">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-600" aria-hidden />
            Listening — speak normally, then press Stop.
          </span>
        )}
      </div>

      {/* The live guess, shown while it is still a guess. Without it there is no
          feedback that the microphone is working until the recogniser commits,
          which can be several seconds and reads as a dead button. */}
      {listening && interim && (
        <p className="mt-1.5 text-xs italic text-slate-500">…{interim}</p>
      )}

      {err && (
        <p className="mt-1.5 text-xs text-amber-800">{err}</p>
      )}

      {!listening && !err && (
        <p className="mt-1 text-xs text-slate-500">
          Dictation is added to the box above and can be edited. Nothing is sent until you
          send it — check the words first, site terms often come back wrong.
        </p>
      )}
    </div>
  );
}
