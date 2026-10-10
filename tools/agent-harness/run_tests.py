#!/usr/bin/env python3
"""Run Harness unittest discovery with an optional zero-skip CI contract."""

import argparse
import os
from pathlib import Path
import sys
import unittest


_LINUX_IMAGE = ("python:3.11-slim@sha256:"
                "e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce")
_DOCKER_PATHS = {
    "/usr/bin/docker",
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
}


def _linux_image(value):
    if value != _LINUX_IMAGE:
        raise argparse.ArgumentTypeError("Linux fixture image must use the pinned digest")
    return value


def _docker_path(value):
    if not Path(value).is_absolute() or value not in _DOCKER_PATHS:
        raise argparse.ArgumentTypeError("Docker fixture path is not an approved absolute entry")
    return value


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
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--linux-image", type=_linux_image)
    parser.add_argument("--docker", type=_docker_path)
    args = parser.parse_args(argv)
    for name in ("HARNESS_TEST_CI_MODE", "HARNESS_TEST_LINUX_IMAGE", "HARNESS_TEST_DOCKER"):
        os.environ.pop(name, None)
    if args.ci and (args.linux_image is None or args.docker is None):
        parser.error("--ci requires --linux-image and --docker")
    if (args.linux_image is None) != (args.docker is None):
        parser.error("--linux-image and --docker must be supplied together")
    if args.ci:
        os.environ["HARNESS_TEST_CI_MODE"] = "1"
    if args.linux_image is not None:
        os.environ["HARNESS_TEST_LINUX_IMAGE"] = args.linux_image
        os.environ["HARNESS_TEST_DOCKER"] = args.docker
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
