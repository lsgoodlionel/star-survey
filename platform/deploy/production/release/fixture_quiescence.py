"""Validate a database quiescence snapshot; never authorize a target by itself.

A snapshot is point-in-time evidence, not an exclusive lease. The caller must
independently bind the container, database and observer, prevent new privileged
writers for the whole transaction, and reject missing or stale evidence.
"""


def require_quiescent_snapshot(snapshot, expected):
    """Reject unknown clients, changed identities or any outstanding work."""
    if not isinstance(expected, dict) or set(expected) != {'database_oid', 'clients'}:
        raise ValueError('expected database identity is invalid')
    if not isinstance(snapshot, dict) or set(snapshot) != {
        'database_oid', 'allow_connections', 'clients', 'prepared', 'unsafe'
    }:
        raise ValueError('database snapshot is invalid')
    if (type(snapshot['database_oid']) is not int or snapshot['database_oid'] <= 0 or
            snapshot['database_oid'] != expected.get('database_oid') or
            snapshot['allow_connections'] is not False or
            type(snapshot['prepared']) is not int or snapshot['prepared'] != 0 or
            type(snapshot['unsafe']) is not int or snapshot['unsafe'] != 0):
        raise ValueError('database gate or work state is unsafe')
    clients, expected_clients = snapshot['clients'], expected.get('clients')
    if not isinstance(clients, list) or not isinstance(expected_clients, list) or not expected_clients:
        raise ValueError('database clients are invalid')
    def identities(items):
        result = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {'pid', 'role', 'database', 'started'}:
                raise ValueError('database client identity is invalid')
            if (type(item['pid']) is not int or item['pid'] <= 0 or
                    any(not isinstance(item[key], str) or not item[key] for key in ('role', 'database', 'started'))):
                raise ValueError('database client identity is invalid')
            identity = (item['pid'], item['role'], item['database'], item['started'])
            if identity in result or any(prior[0] == item['pid'] for prior in result):
                raise ValueError('database client identity is ambiguous')
            result.add(identity)
        return result
    if identities(clients) != identities(expected_clients):
        raise ValueError('unexpected or replaced database client')
