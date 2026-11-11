"""The API's accepted job kinds must match the model's.

This pairing has drifted three times. It silently 422'd `presentation`; a
warning comment was added and it drifted again, so by 2026-09-22 the API was
refusing scope_from_estimate, schedule_from_estimate and change_order_generation
while the worker was perfectly able to run them. The failure is invisible until
someone presses the button, and the error blames the caller.

The Literal is now derived from AI_JOB_KINDS so they cannot disagree. This test
fails if anyone reintroduces a hand-maintained list.
"""
import typing

from app.models.ai_job import AI_JOB_KINDS
from app.schemas.ai_job import AIJobKind


def test_schema_accepts_exactly_the_model_kinds() -> None:
    assert set(typing.get_args(AIJobKind)) == set(AI_JOB_KINDS)


def test_change_order_generation_is_accepted() -> None:
    # The kind that exposed the drift. Named explicitly so a regression points
    # straight at the feature that broke rather than at an abstract set diff.
    assert "change_order_generation" in typing.get_args(AIJobKind)
