"""Use the native trusted test-only builder without rewriting generated SQL."""
from fixture_compensation import build_trusted_compensation_sql

def build_trusted_compensation(**values):
    return build_trusted_compensation_sql(**values)
