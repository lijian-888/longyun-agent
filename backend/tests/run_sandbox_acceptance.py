"""Run every backend test and fail when an integration check is skipped."""

from __future__ import annotations

import sys
import unittest


def main() -> None:
    suite = unittest.defaultTestLoader.discover("/app/tests", pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if result.skipped:
        print("Unexpected skipped acceptance checks:", file=sys.stderr)
        for test, reason in result.skipped:
            print(f"- {test}: {reason}", file=sys.stderr)
    if not result.wasSuccessful() or result.skipped:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
