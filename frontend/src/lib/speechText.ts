// Turning the recogniser's result list into one transcript.
//
// ⚠️ REBUILT FROM ALL RESULTS ON EVERY EVENT, never appended. Amir, 2026-10-16:
// "It's double-writing the words, and it just wasn't usable." The old code
// appended each final chunk from `resultIndex` onward to a running string, which
// writes words twice in two ways browsers really do:
//  - Chrome can deliver an already-final result again (resultIndex points back at
//    it), so the same chunk was appended a second time;
//  - Chrome on Android, in continuous mode, makes each final result CUMULATIVE:
//    "forms stripped", then "forms stripped on the east", then "forms stripped on
//    the east footing", so appending all three repeats the start twice.
// Reading the whole list each time fixes the first; collapsing a chunk that
// starts with the previous one fixes the second.

export interface ResultLike {
  isFinal: boolean;
  0: { transcript: string };
}

const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ");

/** Final text and the current interim guess, from the full result list. */
export function transcriptFrom(results: ArrayLike<ResultLike>): { final: string; interim: string } {
  const finals: string[] = [];
  let interim = "";
  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    const t = (r?.[0]?.transcript ?? "").trim();
    if (!t) continue;
    if (!r.isFinal) {
      interim += (interim ? " " : "") + t;
      continue;
    }
    const prev = finals[finals.length - 1];
    if (prev !== undefined && norm(t).startsWith(norm(prev))) {
      finals[finals.length - 1] = t; // cumulative: the new one contains the old
    } else if (prev !== undefined && norm(prev).startsWith(norm(t))) {
      continue; // a shorter repeat of what is already there
    } else {
      finals.push(t);
    }
  }
  // The interim guess can also restate the last final on Android.
  const last = finals[finals.length - 1];
  if (last && interim && norm(interim).startsWith(norm(last))) {
    interim = interim.trim().slice(last.length).trim();
  }
  return { final: finals.join(" "), interim };
}
