#!/usr/bin/env python3
"""Run Harness unittest discovery with an optional zero-skip CI contract."""

import argparse
import sys
import unittest


def _cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _cases(item)
        else:
            yield item


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-directory", default="tools/agent-harness/tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--include-substring")
    parser.add_argument("--exclude-substring")
    parser.add_argument("--fail-on-skip", action="store_true")
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.discover(args.start_directory, pattern=args.pattern)
    suite = unittest.TestSuite(
        case for case in _cases(suite)
        if (not args.include_substring or args.include_substring in case.id())
        and (not args.exclude_substring or args.exclude_substring not in case.id())
    )
    if suite.countTestCases() == 0:
        print("no tests selected", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    if args.fail_on_skip and result.skipped:
        print("unexpected skips: " + str(len(result.skipped)), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
