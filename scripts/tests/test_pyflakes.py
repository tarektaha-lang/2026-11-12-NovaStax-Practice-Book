"""Development check: scripts/ stays free of pyflakes findings."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]


class PyflakesTests(unittest.TestCase):
    def test_scripts_tree_is_clean(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", str(SCRIPTS)],
            capture_output=True,
            text=True,
            check=False,
        )
        report = (result.stdout + result.stderr).strip()
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report, "")


if __name__ == "__main__":
    unittest.main()
