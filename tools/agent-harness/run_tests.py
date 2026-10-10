#!/usr/bin/env python3
"""Run Harness unittest discovery with an optional zero-skip CI contract."""

import argparse
import sys
import unittest


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-directory", default="tools/agent-harness/tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--fail-on-skip", action="store_true")
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.discover(args.start_directory, pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    if args.fail_on_skip and result.skipped:
        print("unexpected skips: " + str(len(result.skipped)), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
