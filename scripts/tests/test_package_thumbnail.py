"""Cover thumbnail supersamples before downscale."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import package  # noqa: E402


class CoverThumbnailTests(unittest.TestCase):
    def test_render_supersamples_then_downscales(self) -> None:
        page = mock.MagicMock()
        page.get_size.return_value = (595.276, 841.89)
        pil = mock.MagicMock()
        pil.convert.return_value = pil
        pil.resize.return_value = pil
        bitmap = mock.MagicMock()
        bitmap.to_pil.return_value = pil
        page.render.return_value = bitmap
        document = mock.MagicMock()
        document.__getitem__.return_value = page

        with mock.patch.object(package.pdfium, "PdfDocument", return_value=document):
            package.render_cover_thumbnail(Path("cover.pdf"), Path("/tmp/out.png"))

        scale = page.render.call_args.kwargs.get("scale")
        if scale is None:
            scale = page.render.call_args.args[0]
        expected = (
            package.THUMBNAIL_WIDTH * package.THUMBNAIL_SUPERSAMPLE
        ) / 595.276
        self.assertAlmostEqual(scale, expected)
        pil.resize.assert_called_once_with(
            (package.THUMBNAIL_WIDTH, package.THUMBNAIL_HEIGHT),
            package.Image.Resampling.LANCZOS,
        )
        document.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
