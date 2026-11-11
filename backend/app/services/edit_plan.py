"""Editing a document by writing an instruction, safely.

The model returns a CHANGE SET, never a rewritten document. That is the whole
safety property: an editor required to name each change, one at a time, with
the value it expects to be replacing, cannot quietly drop a section the way a
full rewrite can — and a dropped section in a scope is a trade that silently
stops being priced.

Every proposed change carries the value it believes is currently there. Before
anything is applied that `before` is checked against the real document. A
change whose `before` does not match is marked STALE and is not applied: either
the model invented a path, or the document moved underneath the job while it
was queued. Both are cases where guessing is worse than stopping.

Nothing here writes to the database. `verify` annotates, `apply` returns a new
payload; persisting it, snapshotting the old one and recomputing totals are the
caller's job.
"""

from __future__ import annotations

import copy
import re
from typing import Any

#: `categories[1].items[0].description`, `exclusions[3]`, `assumptions[]`.
#: `[]` means append. A bare key at the root is `title`.
_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d*)\]")

# 800 since 2026-10-15 (was 400): ZZ estimate v2 had more fields than that,
# and its 66 assumptions came after the line items, so "change the hardware
# finish to matte black" could not see "satin chrome". See describe() for the
# order, which matters more than the cap.
MAX_PATH_LINES = 800
MAX_VALUE_CHARS = 300


class PathError(ValueError):
    pass


def _segments(path: str) -> list[str | int | None]:
    if not path or not path.strip():
        raise PathError("empty path")
    out: list[str | int | None] = []
    pos = 0
    for m in _SEGMENT.finditer(path):
        if m.start() != pos and path[pos:m.start()] != ".":
            raise PathError(f"cannot parse path {path!r}")
        pos = m.end()
        key, idx = m.group(1), m.group(2)
        if key is not None:
            out.append(key)
        elif idx == "":
            out.append(None)          # append marker
        else:
            out.append(int(idx))
    if pos != len(path):
        raise PathError(f"cannot parse path {path!r}")
    return out


def _walk(doc: Any, segs: list[str | int | None]) -> tuple[Any, str | int | None]:
    """Return (container, final_key) without creating anything."""
    cur = doc
    for seg in segs[:-1]:
        if isinstance(seg, str):
            if not isinstance(cur, dict) or seg not in cur:
                raise PathError(f"no such key {seg!r}")
            cur = cur[seg]
        elif isinstance(seg, int):
            if not isinstance(cur, list) or seg >= len(cur):
                raise PathError(f"index {seg} out of range")
            cur = cur[seg]
        else:
            raise PathError("append marker may only appear at the end of a path")
    return cur, segs[-1]


def get(doc: Any, path: str) -> Any:
    container, key = _walk(doc, _segments(path))
    if key is None:
        raise PathError("cannot read an append path")
    if isinstance(key, str):
        if not isinstance(container, dict) or key not in container:
            raise PathError(f"no such key {key!r}")
        return container[key]
    if not isinstance(container, list) or key >= len(container):
        raise PathError(f"index {key} out of range")
    return container[key]


# --- describing the document for the prompt --------------------------------

def leaf_paths(doc: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Every addressable leaf, with its path.

    Handed to the model instead of asking it to infer paths from raw JSON.
    Path construction becomes mechanical rather than a second thing it can get
    wrong on top of the edit itself.
    """
    out: list[tuple[str, Any]] = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.extend(leaf_paths(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.extend(leaf_paths(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, doc))
    return out


def describe(doc: Any) -> str:
    """A path-annotated rendering of the document, capped."""
    lines: list[str] = []
    # Prose first (assumptions, exclusions, notes, summary), the big tables of
    # line items last: when the cap bites, it should cut repetitive numbers, not
    # the sentences a described change usually targets.
    if isinstance(doc, dict):
        def _bulky(item: tuple[str, Any]) -> bool:
            v = item[1]
            return isinstance(v, list) and any(isinstance(x, dict) for x in v)
        ordered = sorted(doc.items(), key=_bulky)  # stable: original order within each group
        paths = [lp for k, v in ordered for lp in leaf_paths(v, str(k))]
    else:
        paths = leaf_paths(doc)
    for path, value in paths:
        text = "null" if value is None else str(value)
        if len(text) > MAX_VALUE_CHARS:
            text = text[:MAX_VALUE_CHARS] + " …"
        lines.append(f"{path} = {text}")
        if len(lines) >= MAX_PATH_LINES:
            lines.append(
                f"[… {len(paths) - MAX_PATH_LINES} more fields not listed; "
                "edits to them cannot be proposed in this run]"
            )
            break
    return "\n".join(lines)


# --- verification ----------------------------------------------------------

VALID_ACTIONS = ("replace", "remove", "add")


def _same(a: Any, b: Any) -> bool:
    """Compare a proposed `before` to reality, tolerantly on formatting only.

    The model round-trips values through its own text, so trailing whitespace
    and int-vs-float are noise. Anything else is a real mismatch and must fail.
    """
    if a == b:
        return True
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    if isinstance(a, str) and isinstance(b, (int, float)):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except ValueError:
            return False
    if isinstance(b, str) and isinstance(a, (int, float)):
        return _same(b, a)
    return False


def verify(doc: Any, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate each proposed change with whether it can actually be applied."""
    out: list[dict[str, Any]] = []
    for raw in changes:
        change = dict(raw) if isinstance(raw, dict) else {}
        path = str(change.get("path") or "")
        action = str(change.get("action") or "").lower()
        change["action"] = action

        if action not in VALID_ACTIONS:
            change.update(applicable=False,
                          problem=f"unknown action {action!r}")
            out.append(change)
            continue
        try:
            segs = _segments(path)
        except PathError as exc:
            change.update(applicable=False, problem=str(exc))
            out.append(change)
            continue

        if action == "add":
            # Appending needs the list to exist; it does not need a `before`.
            try:
                container, key = _walk(doc, segs)
            except PathError as exc:
                change.update(applicable=False, problem=str(exc))
                out.append(change)
                continue
            target = container.get(key) if isinstance(key, str) and isinstance(container, dict) else None
            if key is None:
                target = container
            if not isinstance(target, list):
                change.update(applicable=False,
                              problem=f"{path} is not a list, so nothing can be added to it")
            else:
                change.update(applicable=True, problem=None)
            out.append(change)
            continue

        try:
            actual = get(doc, path)
        except PathError as exc:
            change.update(applicable=False, problem=str(exc))
            out.append(change)
            continue

        change["actual"] = actual
        if "before" in change and not _same(change.get("before"), actual):
            # STALE. Either the path was invented, or the record changed while
            # the job was queued. Applying anyway would overwrite something
            # nobody looked at.
            change.update(
                applicable=False,
                problem="the current value is not what this edit expected to find",
            )
        else:
            change.update(applicable=True, problem=None)
        out.append(change)
    return out


# --- application -----------------------------------------------------------

def apply(doc: Any, changes: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
    """Apply the applicable changes to a COPY. Returns (new_doc, applied).

    Removals are done last and in descending index order: removing
    `exclusions[1]` first would renumber `exclusions[3]` and the second removal
    would take the wrong line.
    """
    new = copy.deepcopy(doc)
    applied: list[dict[str, Any]] = []

    ordinary = [c for c in changes if c.get("applicable") and c.get("action") != "remove"]
    removals = [c for c in changes if c.get("applicable") and c.get("action") == "remove"]

    for change in ordinary:
        segs = _segments(str(change["path"]))
        container, key = _walk(new, segs)
        if change["action"] == "replace":
            container[key] = change.get("after")
        else:  # add
            target = container if key is None else container[key]
            target.append(change.get("after"))
        applied.append(change)

    def _sort_key(c: dict[str, Any]) -> tuple[str, int]:
        segs = _segments(str(c["path"]))
        last = segs[-1]
        return (str(c["path"]).rsplit("[", 1)[0], last if isinstance(last, int) else -1)

    for change in sorted(removals, key=_sort_key, reverse=True):
        segs = _segments(str(change["path"]))
        container, key = _walk(new, segs)
        if isinstance(key, int) and isinstance(container, list):
            container.pop(key)
        elif isinstance(key, str) and isinstance(container, dict):
            container.pop(key, None)
        else:
            continue
        applied.append(change)

    return new, applied
