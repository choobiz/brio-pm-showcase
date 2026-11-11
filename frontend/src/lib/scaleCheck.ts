// No imports: scripts/check-scale-check.mjs loads this file directly in Node.

/**
 * Checking a page's scale against the drawing itself (2026-10-26, 456 W
 * Sample ID-0). The title block said 1/4" = 1'-0" but the plan had been
 * pasted in enlarged to about 0.48" per foot, so every length came out 1.9x
 * too long and nothing on the page said so.
 */

/** PDF units (1/72 in) per real foot for the common scales. */
const COMMON: [string, number][] = [
  ['1/16" = 1\'-0"', 4.5],
  ['3/32" = 1\'-0"', 6.75],
  ['1/8" = 1\'-0"', 9],
  ['3/16" = 1\'-0"', 13.5],
  ['1/4" = 1\'-0"', 18],
  ['3/8" = 1\'-0"', 27],
  ['1/2" = 1\'-0"', 36],
  ['3/4" = 1\'-0"', 54],
  ['1" = 1\'-0"', 72],
  ["1:20", 43.2],
  ["1:25", 34.56],
  ["1:50", 17.28],
  ["1:75", 11.52],
  ["1:100", 8.64],
  ["1:200", 4.32],
];

/** Lengths further apart than this are a different scale, not drafting slop. */
export const SCALE_TOLERANCE = 0.02;

const isMetric = (label: string | null | undefined) => !!label && label.includes(":");

/**
 * The common scale `ppf` is, within 1%, or null. Only names from the family of
 * `like` (a scale label on the same sheet; imperial when absent): ID-0's
 * 34.6 units per foot is also 1:25, which means nothing on an imperial set.
 */
export function namedScaleFor(ppf: number, like?: string | null): string | null {
  const hit = COMMON.find(([n, v]) => isMetric(n) === isMetric(like) && Math.abs(ppf - v) <= 0.01 * v);
  return hit ? hit[0] : null;
}

/** A scale in words: its common name, else inches per foot (or 1:N). */
export function describeScale(ppf: number, like?: string | null): string {
  const named = namedScaleFor(ppf, like);
  if (named) return named;
  return isMetric(like) ? `1:${Math.round(864 / ppf)}` : `${(ppf / 72).toFixed(2)}" = 1'-0"`;
}

/**
 * How far the scale in use is from what the drawing measures, as the factor
 * every length would be off by (1.93 = 93% too long), or null when they agree.
 * Lengths are units / ppf, so measuring at `inUse` instead of `measured`
 * scales every length by measured / inUse.
 */
export function lengthError(inUse: number, measured: number): number | null {
  if (!(inUse > 0) || !(measured > 0)) return null;
  const f = measured / inUse;
  return Math.abs(f - 1) <= SCALE_TOLERANCE ? null : f;
}

/** "93% too long" / "40% too short". */
export function describeLengthError(f: number): string {
  return f > 1 ? `${Math.round((f - 1) * 100)}% too long` : `${Math.round((1 - f) * 100)}% too short`;
}

/**
 * A length typed as feet: 48.42, 48'-5", 48' 5", 48'5, 48-5, 48 5 1/2.
 * The calibrate box took only decimal feet, so a printed 48'-5" had to be
 * converted by hand. Null when it is not a length.
 */
export function parseFeetInput(raw: string): number | null {
  const s = raw.trim().replace(/[’′]/g, "'").replace(/[”″]/g, '"').replace(/''/g, '"');
  if (!s) return null;
  if (/^\d+(\.\d+)?$/.test(s)) {
    const v = parseFloat(s);
    return v > 0 ? v : null;
  }
  const m = s.match(/^(\d+)\s*(?:'\s*-?|-|\s)\s*(?:(\d+(?:\.\d+)?)(?:\s+(\d+)\/(\d+))?\s*"?)?$/);
  if (!m) return null;
  const inches = (m[2] ? parseFloat(m[2]) : 0) + (m[3] && m[4] && +m[4] > 0 ? +m[3] / +m[4] : 0);
  if (inches >= 12) return null;
  const v = parseInt(m[1], 10) + inches / 12;
  return v > 0 ? v : null;
}
