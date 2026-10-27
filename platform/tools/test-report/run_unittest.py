"""Run product CI unittest suites and reject unexpected skips or empty suites."""

import argparse
import sys
import unittest
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--exclude-file", action="append", default=[])
    args = parser.parse_args()
    if not Path(args.start).is_dir() or any(
        Path(name).name != name or not name.startswith("test_") or not name.endswith(".py")
        for name in args.exclude_file
    ):
        return 2
    discovered = unittest.TestLoader().discover(args.start, pattern=args.pattern)

    def cases(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from cases(item)
            else:
                module = sys.modules.get(item.__class__.__module__)
                if Path(getattr(module, "__file__", "")).name not in args.exclude_file:
                    yield item

    selected = unittest.TestSuite(cases(discovered))
    if not selected.countTestCases():
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
