import type { DrawingPageMeta } from "@/lib/drawings";

/**
 * Transform a PDF bbox into screen-pixel coordinates inside a rendered page.
 *
 * ⚠️ DUPLICATED FROM THE LIVE QUANTITIES PAGE, DELIBERATELY. The original lives
 * inside a 1,531-line component in the app people use daily, and extracting it
 * would mean editing that file — which this workspace is explicitly not allowed
 * to do while it remains unproven. The two converge when the workspace is
 * accepted; until then they must be kept in step BY HAND, and a divergence here
 * means two parts of the product disagree about where a measurement sits on a
 * drawing.
 *
 * The rotation handling is the whole difficulty. PyMuPDF reports `page.rect` in
 * POST-rotation dimensions but returns path coordinates PRE-rotation, while
 * pdf.js rotates the canvas so the long axis matches a normal viewer. So the
 * pre-rotation page size has to be recovered before the box can be rotated to
 * match what is on screen.
 *
 * ⚠️ THE ROTATION PATH IS UNVERIFIED AGAINST REAL DATA. An earlier version of
 * this comment asserted "270° is not hypothetical — the PROJB and Maple sets
 * are both rotated that way". Checked 2026-10-02: production holds exactly ONE
 * stored drawing analysis and its page is rotation 0, so nothing on record
 * supports that claim — it may predate the 2026-08-31 wipe. The transform is
 * exercised at 90/180/270 by scripts/check-bbox-alignment.mjs using synthetic
 * rotations of a real page, which proves the maths and NOT that a real rotated
 * sheet renders correctly. Confirm that by eye the first time one is analysed.
 */

export interface ScreenBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function bboxToScreen(
  bbox: [number, number, number, number],
  page: DrawingPageMeta,
  renderWidth: number,
): ScreenBox | null {
  const [x0, y0, x1, y1] = bbox;
  const isRotated = page.rotation % 180 === 90;
  const Wpre = isRotated ? page.height : page.width;
  const Hpre = isRotated ? page.width : page.height;
  if (Wpre <= 0 || Hpre <= 0 || page.width <= 0) return null;

  let sx0: number, sy0: number, sx1: number, sy1: number;
  switch (((page.rotation % 360) + 360) % 360) {
    case 90: // displayed 90° CW: (x, y) -> (Hpre - y, x)
      sx0 = Hpre - y1; sy0 = x0; sx1 = Hpre - y0; sy1 = x1; break;
    case 180:
      sx0 = Wpre - x1; sy0 = Hpre - y1; sx1 = Wpre - x0; sy1 = Hpre - y0; break;
    case 270: // displayed 90° CCW: (x, y) -> (y, Wpre - x)
      sx0 = y0; sy0 = Wpre - x1; sx1 = y1; sy1 = Wpre - x0; break;
    default:
      sx0 = x0; sy0 = y0; sx1 = x1; sy1 = y1; break;
  }
  // page.width is already post-rotation, which is what is rendered to.
  const scale = renderWidth / page.width;
  return {
    left: Math.min(sx0, sx1) * scale,
    top: Math.min(sy0, sy1) * scale,
    width: Math.abs(sx1 - sx0) * scale,
    height: Math.abs(sy1 - sy0) * scale,
  };
}
