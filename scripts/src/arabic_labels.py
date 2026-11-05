"""Rewrite Latin x/y on Arabic-notation curve labels to the letters the wording uses."""

from __future__ import annotations

import re

from src.arabic_math import LETTER_TO_NAME

# One-letter variables the plotter still emits as Latin, even when the stem uses س/ص.
LATIN_XY = re.compile(r"(?<![A-Za-z\\])([xy])(?![A-Za-z])", re.I)

ARABIC_LETTER = "".join(sorted(LETTER_TO_NAME))
# A variable named beside an axis: ``(س)`` in the description or ``$س$`` in the stem.
AXIS_VAR = re.compile(
    rf"\(\s*([{ARABIC_LETTER}])\s*\)|\$\s*([{ARABIC_LETTER}])\s*\$"
)

HORIZONTAL = ("أفقي", "horizontal")
VERTICAL = ("رأسي", "عمودي", "vertical")

# How far from an axis word a letter still counts as naming that axis.
AXIS_WINDOW_BEFORE = 50
AXIS_WINDOW_AFTER = 50


def rewrite_arabic_curve_label(label: str, wording: str = "") -> str:
    """Map standalone Latin ``x`` / ``y`` in a curve equation to Arabic letters.

    Uses the letter tagged as the horizontal / vertical axis in ``wording`` when
    one is there (``المحور الأفقي (ن)``), otherwise ``س`` / ``ص``. Labels that
    already have no Latin ``x`` or ``y`` are left alone.
    """
    if not LATIN_XY.search(label):
        return label
    x_name = letter_near(wording, HORIZONTAL) or "س"
    y_name = letter_near(wording, VERTICAL) or "ص"

    def replace(found: re.Match[str]) -> str:
        return x_name if found.group(1).lower() == "x" else y_name

    return LATIN_XY.sub(replace, label)


def rewrite_graph(graph: dict, wording: str) -> dict:
    """A copy of ``graph`` whose curve labels have been rewritten, or the same dict."""
    curves = graph.get("curves")
    if not isinstance(curves, list):
        label = graph.get("label")
        if not isinstance(label, str):
            return graph
        rewritten = rewrite_arabic_curve_label(label, wording)
        return graph if rewritten == label else {**graph, "label": rewritten}

    changed = False
    rewritten_curves: list = []
    for curve in curves:
        if not isinstance(curve, dict) or not isinstance(curve.get("label"), str):
            rewritten_curves.append(curve)
            continue
        rewritten = rewrite_arabic_curve_label(curve["label"], wording)
        if rewritten == curve["label"]:
            rewritten_curves.append(curve)
            continue
        changed = True
        rewritten_curves.append({**curve, "label": rewritten})
    return {**graph, "curves": rewritten_curves} if changed else graph


def letter_near(text: str, keywords: tuple[str, ...]) -> str:
    """The Arabic letter closest to one of ``keywords``, or empty."""
    if not text:
        return ""
    best: tuple[float, str] | None = None
    for keyword in keywords:
        for hit in re.finditer(re.escape(keyword), text):
            start = max(0, hit.start() - AXIS_WINDOW_BEFORE)
            end = min(len(text), hit.end() + AXIS_WINDOW_AFTER)
            keyword_mid = (hit.start() + hit.end()) / 2
            for found in AXIS_VAR.finditer(text, start, end):
                letter = found.group(1) or found.group(2)
                mid = (found.start() + found.end()) / 2
                distance = abs(mid - keyword_mid)
                if best is None or distance < best[0]:
                    best = (distance, letter)
    return best[1] if best else ""
