"""Engine mutations for the fixed-stack ownership protocol, no CLI mutation.

Existing read-only inspect projections are reused; credentials/raw API bodies
are never part of their returned observations. Claim image is already local.
"""
from copy import deepcopy
import re
import time
from urllib.parse import quote

import runtime_ownership as ownership
from owned_docker_exec import LocalDockerAPI


class ReadOnlyDockerRunner:
    def __init__(self, runner):
        self.runner = runner

    def run(self, command, **kwargs):
        prefix = ['docker', '--host', ownership.DockerOwnershipDriver.HOST]
        args = list(command[3:])
        if (list(command[:3]) != prefix or not args or not (
                args[0] == 'info' or len(args) >= 2 and args[0] in ('container', 'network', 'volume', 'image')
                and args[1] in ('ls', 'inspect'))):
            raise ownership.OwnershipError('only fixed local read-only Docker queries are allowed')
        return self.runner.run(command, **kwargs)


class APIOwnershipDriver(ownership.DockerOwnershipDriver):
    def __init__(self, runner, contract, cohort_probe, *, api=None):
        super().__init__(ReadOnlyDockerRunner(runner), contract, cohort_probe)
        self.api = api or LocalDockerAPI()
        self.idle_sleep = time.sleep

    def _container(self, identity, *, allowed_exec=None, allow_paused=False):
        if (allowed_exec is not None and (not isinstance(allowed_exec, str)
                or not ownership.HEX64.fullmatch(allowed_exec)) or type(allow_paused) is not bool):
            raise ownership.OwnershipError('container observation allowance is invalid')
        deadline = time.monotonic() + 5
        for attempt in range(50):
            result = super()._container(identity)
            row = getattr(self, '_last_container_snapshot', None)
            active = row.get('execs') if isinstance(row, dict) and row.get('id') == identity else None
            # Daemon health probes also register ExecIDs. No ID is adopted,
            # whitelisted or marked ended here: read again until the actual
            # snapshot has no other exec. Private pending receipts are still
            # rejected independently by the unchanged cohort/ownership gates.
            retry = (result[1] and isinstance(active, list) and active
                and all(isinstance(value, str) and ownership.HEX64.fullmatch(value) for value in active)
                and any(value != allowed_exec for value in active)
                and type(row.get('paused')) is bool and (not row['paused'] or allow_paused)
                and row.get('status') in {'running', 'created', 'exited', 'dead'})
            if not retry or attempt == 49 or time.monotonic() >= deadline:
                return result
            self.idle_sleep(.1)

    def _query(self, arguments):
        args = list(arguments)
        if len(args) == 5 and args[:4] == ['container', 'stop', '--time', '20']:
            identity = self.api._id(args[4])
            self.api._json('POST', '/containers/' + identity + '/stop?t=20', accepted=(204, 304))
            return identity
        if len(args) == 3 and args[:2] in (['container', 'rm'], ['network', 'rm']):
            identity = self.api._id(args[2])
            path = '/containers/' + identity + '?force=false&v=false' if args[0] == 'container' else '/networks/' + identity
            self.api._json('DELETE', path, accepted=(204,))
            return identity
        if len(args) == 3 and args[:2] == ['volume', 'rm']:
            name = args[2]
            if not re.fullmatch(re.escape(ownership.PROJECT) + r'_[A-Za-z0-9_.-]{1,128}', name):
                raise ownership.OwnershipError('volume deletion identity is invalid')
            self.api._json('DELETE', '/volumes/' + quote(name, safe='') + '?force=false', accepted=(204,))
            return name
        return super()._query(args)  # ReadOnlyDockerRunner rejects other mutations.

    def create_claim(self, name, binding, image):
        if (name != ownership.CLAIM_NAME or not isinstance(binding, dict)
                or set(binding) != {'daemonId', 'installationId', 'targetSha256'}
                or not isinstance(image, str) or image.startswith('-')
                or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image)):
            raise ownership.OwnershipError('claim request binding is invalid')
        if self._json(['image', 'inspect', '--format', '{{json .Config.Volumes}}', '--', image]) not in ({}, None):
            raise ownership.OwnershipError('claim image declares implicit volumes')
        value = self.api._json('POST', '/containers/create?name=' + quote(name, safe=''), {
            'Image': image, 'Entrypoint': ['/bin/true'], 'Cmd': [], 'User': '65534:65534',
            'Labels': {self.OWNER: binding['installationId'], 'io.survey.target-sha256': binding['targetSha256'],
                       'io.survey.daemon': binding['daemonId'], 'io.survey.role': 'ownership-claim'},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                           'SecurityOpt': ['no-new-privileges:true']}})
        if (not isinstance(value, dict) or set(value) != {'Id', 'Warnings'} or value['Warnings'] not in ([], None)
                or not isinstance(value['Id'], str) or not ownership.HEX64.fullmatch(value['Id'])):
            raise ownership.OwnershipError('claim create identity is unknown')
        self.binding = deepcopy(binding)
        return value['Id']
