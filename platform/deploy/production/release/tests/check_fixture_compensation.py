#!/usr/bin/env python3
"""Blocked fixture compensation experiment; no database actions are included.

A disposable target, writer exclusion and fail-closed cleanup must be proven
before the original mutation experiment can be considered for execution.
"""


def raise_database_failure(_untrusted_details):
    """Never place raw database output, SQL, or connection details in an error."""
    raise RuntimeError('isolated database operation failed') from None


def main():
    raise RuntimeError('compensation experiment blocked pending independent target isolation')


if __name__ == '__main__':
    main()
