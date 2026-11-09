#!/usr/bin/env python3
"""Blocked fixture experiment: CI environment variables do not prove target isolation.

The original experiment creates roles and changes a database connection gate.
It is intentionally not included until independent target ownership and writer
exclusion are established and reviewed. This entry performs no database action.
"""


def main():
    raise RuntimeError('database experiment blocked pending independent target isolation')


if __name__ == '__main__':
    main()
