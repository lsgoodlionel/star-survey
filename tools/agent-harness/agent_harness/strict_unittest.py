"""Run unittest discovery and reject successful runs that contain skips."""

import argparse
from pathlib import Path
import sys
import unittest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--top")
    parser.add_argument("--exclude-file", action="append", default=[])
    args = parser.parse_args(argv)
    start = Path(args.start)
    if not start.is_dir():
        return 2
    if any(Path(name).name != name or not name.startswith("test_")
           or not name.endswith(".py") for name in args.exclude_file):
        return 2
    discovered = unittest.defaultTestLoader.discover(
        str(start), pattern=args.pattern, top_level_dir=args.top)
    excluded = set(args.exclude_file)

    def selected(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from selected(item)
                continue
            module = sys.modules.get(item.__class__.__module__)
            source = Path(getattr(module, "__file__", "")).name
            if source not in excluded:
                yield item

    suite = unittest.TestSuite(selected(discovered))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
