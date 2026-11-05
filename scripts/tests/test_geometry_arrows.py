from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.geometry_arrows import (  # noqa: E402
    canonicalize_document,
    canonicalize_geometry_arrows,
    canonicalize_tree,
    is_ray_meaning,
    section_is_vector_lesson,
)


class GeometryArrowTests(unittest.TestCase):
    def test_ray_wording_and_membership_become_overrightarrow(self) -> None:
        self.assertEqual(
            canonicalize_geometry_arrows(r"Ray $\vec{AE}$ intersects ray $\vec{DC}$."),
            r"Ray $\overrightarrow{AE}$ intersects ray $\overrightarrow{DC}$.",
        )
        self.assertEqual(
            canonicalize_geometry_arrows(r"يقطع الشعاع $\vec{BE}$ الضلع $AC$."),
            r"يقطع الشعاع $\overrightarrow{BE}$ الضلع $AC$.",
        )
        self.assertEqual(
            canonicalize_geometry_arrows(r"$H \in \vec{BA}$, $AB = AH$."),
            r"$H \in \overrightarrow{BA}$, $AB = AH$.",
        )

    def test_nested_braces_and_idempotence(self) -> None:
        nested = r"$\vec{\hehi  \beh }$ و$\vec{\text{EA}}$"
        once = canonicalize_geometry_arrows(nested)
        self.assertEqual(
            once,
            r"$\overrightarrow{\hehi  \beh }$ و$\overrightarrow{\text{EA}}$",
        )
        self.assertEqual(canonicalize_geometry_arrows(once), once)
        already = r"$\overrightarrow{OP}$ bisects $\angle AOB$"
        self.assertEqual(canonicalize_geometry_arrows(already), already)

    def test_non_vector_section_rewrites_bare_vec(self) -> None:
        self.assertEqual(
            canonicalize_geometry_arrows(r"$\vec{AZ} // \overline{YD}$"),
            r"$\overrightarrow{AZ} // \overline{YD}$",
        )

    def test_vector_lesson_keeps_vec_except_named_rays(self) -> None:
        addition = r"Find $\vec{a} + \vec{AB}$."
        self.assertEqual(
            canonicalize_geometry_arrows(addition, vector_lesson=True),
            addition,
        )
        self.assertEqual(
            canonicalize_geometry_arrows(
                r"The ray $\vec{AB}$ is not a vector.",
                vector_lesson=True,
            ),
            r"The ray $\overrightarrow{AB}$ is not a vector.",
        )

    def test_ray_meaning_rewrites_spec_notation_token(self) -> None:
        self.assertTrue(is_ray_meaning("Ray originating at point $D$ and passing through $E$"))
        self.assertTrue(is_ray_meaning("الشعاع الذي يبدأ من النقطة $D$ ويمر بالنقطة $E$"))
        row = canonicalize_tree(
            {
                "id": "notation_3",
                "notation": r"$\vec{DE}$",
                "meaning": "Ray originating at point $D$ and passing through point $E$",
            },
            vector_lesson=True,
        )
        self.assertEqual(row["notation"], r"$\overrightarrow{DE}$")
        mixed = canonicalize_tree(
            {
                "notation": r"$\vec{AB}$ / $\overleftrightarrow{AB}$",
                "meaning": "ray originating at $A$ passing through $B$ / straight line",
            },
            vector_lesson=True,
        )
        self.assertEqual(
            mixed["notation"],
            r"$\overrightarrow{AB}$ / $\overleftrightarrow{AB}$",
        )

    def test_vector_lesson_detection_ignores_out_of_scope(self) -> None:
        vectors = {
            "title": "Vectors and unit vectors",
            "spec": {
                "overview": "Students add $\\vec{a}$ and $\\vec{b}$.",
                "glossary": [{"term": "Unit vector", "definition": "A vector of length 1."}],
            },
        }
        self.assertTrue(section_is_vector_lesson(vectors))
        geometry = {
            "title": "Applications of Parallelism",
            "spec": {
                "overview": "The ray drawn from a midpoint.",
                "out_of_scope": [
                    {
                        "text": "Formal vector proofs of parallelism.",
                        "reason": "The section uses synthetic geometry.",
                    }
                ],
            },
        }
        self.assertFalse(section_is_vector_lesson(geometry))
        rewritten = canonicalize_document(
            {
                **geometry,
                "spec": {
                    **geometry["spec"],
                    "notations": [
                        {
                            "notation": r"$\vec{DE}$",
                            "meaning": "Ray originating at point $D$ and passing through point $E$",
                        }
                    ],
                },
            }
        )
        self.assertEqual(
            rewritten["spec"]["notations"][0]["notation"],
            r"$\overrightarrow{DE}$",
        )
        kept = canonicalize_document(
            {
                **vectors,
                "exercises": {
                    "groups": [
                        {
                            "text": r"Simplify $\vec{a}+\vec{AB}$.",
                        }
                    ]
                },
            }
        )
        self.assertIn(r"$\vec{a}+\vec{AB}$", kept["exercises"]["groups"][0]["text"])

    def test_book_rewrites_each_section_and_map_symbol_rays(self) -> None:
        book = canonicalize_document(
            {
                "title": "Mathematics",
                "sections": [
                    {
                        "title": "Applications of Parallelism",
                        "spec": {"overview": "A ray from a midpoint."},
                        "stem": r"$\vec{AZ} // \overline{YD}$",
                    },
                    {
                        "title": "Vectors and unit vectors",
                        "spec": {"overview": "Add $\\vec{a}$."},
                        "stem": r"Find $\vec{a}+\vec{AB}$.",
                    },
                ],
            }
        )
        self.assertEqual(
            book["sections"][0]["stem"],
            r"$\overrightarrow{AZ} // \overline{YD}$",
        )
        self.assertEqual(book["sections"][1]["stem"], r"Find $\vec{a}+\vec{AB}$.")
        row = canonicalize_tree(
            {
                "id": "symbol:keep",
                "kind": "symbol",
                "source_math": r"$\vec{DE}$",
                "math_text": r"$\vec{DE}$",
                "contexts": [
                    "Ray originating at point $D$ and passing through point $E$"
                ],
                "evidence": [{"quote": r"\vec{DE} // \overline{BC}"}],
            },
            vector_lesson=True,
        )
        self.assertEqual(row["id"], "symbol:keep")
        self.assertEqual(row["source_math"], r"$\overrightarrow{DE}$")
        self.assertEqual(row["math_text"], r"$\overrightarrow{DE}$")
        self.assertEqual(row["evidence"][0]["quote"], r"\overrightarrow{DE} // \overline{BC}")


if __name__ == "__main__":
    unittest.main()
