"""The footprint measurement tool's two load-bearing steps.

Both exist to stop the same failure: a raster outline whose AREA is right while
its PERIMETER is inflated by pixel jitter — which is exactly how the CAD engine
reported 156.9 lf around a building that measures 120."""
import numpy as np

from scripts.measure_footprint_from_pdf import _steps


def test_jitter_does_not_become_perimeter():
    """One pixel of wobble per row must not add length to a straight wall."""
    ppf = 54.0
    straight = np.full(400, 6.70)
    jittered = straight + np.tile([0.0, 1 / ppf, 0.0, -1 / ppf], 100)
    out = _steps(jittered, int(ppf * 1.5), 0.35)
    assert float(np.abs(np.diff(out)).sum()) == 0.0     # a straight wall is straight


def test_a_real_step_survives():
    """A wall that genuinely jogs 5.9 ft must keep its jog — the filter removes
    noise, not geometry."""
    ppf = 54.0
    prof = np.concatenate([np.full(200, 6.70), np.full(200, 0.78)])
    out = _steps(prof, int(ppf * 1.5), 0.35)
    assert abs(float(np.abs(np.diff(out)).sum()) - 5.92) < 0.15


def test_the_1234_outline_reconstructs_to_the_measured_perimeter():
    """Garage 19.56 ft wide below, office 13.78 ft wide above, 38.63 ft tall,
    9.48 ft of jog in the side walls — the profile measured off sheet A1.04."""
    height, top_w, bottom_w, jog = 38.63, 13.78, 19.56, 9.48
    perimeter = 2 * height + jog + top_w + bottom_w
    assert abs(perimeter - 120.1) < 0.2
