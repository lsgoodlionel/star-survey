"""Candidate-bound ordinary uninstall with an independent ownership postcheck.

The trusted native harness supplies an already authenticated candidate binding.
This does not authenticate signatures, erase host fixtures, authorize restore,
or qualify a candidate. No recovery or replay is attempted on any failure.
The installation lock spans preflight, the existing controller operation and
postcheck; the trust boundary remains an exclusive, trusted controller/daemon.
"""
from copy import deepcopy
import re

from owned_docker_exec import UncertainExec


class CleanupProofError(UncertainExec):
    """Proof unavailable: preserve the installation, intent and receipts."""


def _binding(value):
    keys = {'schemaVersion', 'version', 'commit', 'manifestSha256', 'bundleSha256',
            'runId', 'attempt', 'ubuntu', 'architecture'}
    if (not isinstance(value, dict) or set(value) != keys
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value['version'], str)
            or re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-rc\.[1-9][0-9]*', value['version']) is None
            or any(not isinstance(value[key], str) or re.fullmatch(r'[0-9a-f]{'+str(size)+'}', value[key]) is None
                   for key, size in (('commit',40), ('manifestSha256',64), ('bundleSha256',64)))
            or any(not isinstance(value[key],str) or re.fullmatch(r'[1-9][0-9]{0,19}',value[key]) is None
                   for key in ('runId','attempt'))
            or value['ubuntu'] not in ('22.04','24.04') or value['architecture'] not in ('amd64','arm64')):
        raise CleanupProofError('native cleanup candidate binding is invalid; preserve the fixture')
    return deepcopy(value)


def _source(manager, binding):
    version = binding['version']
    manifest = manager._release_manifest(version)
    bundle = manifest.raw.get('assets',{}).get('bundles',{}).get(binding['architecture'],{})
    if (manifest.version != version or manifest.sha256 != binding['manifestSha256']
            or manifest.raw.get('commit') != binding['commit']
            or bundle.get('sha256') != binding['bundleSha256']):
        raise CleanupProofError('native cleanup candidate differs from the installed source')
    runtime = manager._runtime(version)  # Validates staged code, source configuration and ownership overlay.
    if runtime.data.get('version') != version or runtime.data.get('manifestSha256') != binding['manifestSha256']:
        raise CleanupProofError('native cleanup runtime differs from the candidate')
    return runtime


def prove_uninstall(manager, candidate):
    """Run ordinary uninstall once, under its existing lock; return bounded facts.

    Only use from a trusted harness after source authentication. Success proves
    runtime resources retired and the same data volumes/claim retained at the
    final observation; host/TLS fixture cleanup is a separate proof. No secrets,
    resource IDs or controller records are returned for artifact upload.
    """
    binding = _binding(candidate)
    try:
        with manager._mutation_lock(operation='uninstall'):
            state = manager._read_state()
            if state.get('version') != binding['version'] or state.get('status') not in ('healthy','install_failed'):
                raise CleanupProofError('native cleanup installation state is not eligible')
            runtime = _source(manager,binding)
            source = deepcopy(runtime.data)
            before = runtime.protocol._record({'ready','settled-failure'})
            # This calls the normal controller; never substitute purge/down/rm.
            manager.uninstall(purge_data=False, confirmation=None)
            state = manager._read_state()
            if (state.get('version') != binding['version'] or state.get('status') != 'uninstalled'
                    or state.get('dataPurged') is not False):
                raise CleanupProofError('ordinary uninstall state is not verified')
            # Reopen from disk and independently query daemon/claim/cohort after
            # the controller returns. A controller exit code is not this proof.
            runtime = _source(manager,binding)
            after = runtime.protocol._record({'retained-data'})
            if (runtime.data != source or
                    any(after.get(k) != before.get(k) for k in before if k not in {'phase','resources'})
                    or after['resources'] != dict(containers={},networks={},volumes=before['resources']['volumes'])):
                raise CleanupProofError('uninstall changed retained identity or left runtime resources')
            result = dict(schemaVersion=1, candidate=binding, ordinaryUninstallVerified=True,
                retainedVolumeCount=len(before['resources']['volumes']), claimRetained=True,
                cohortQuiescent=True, hostCleanupVerified=False, candidateQualified=False)
        # Do not expose success before the controller lock/intent finalization.
        return result
    except CleanupProofError:
        raise
    except Exception as exc:
        raise CleanupProofError('native cleanup outcome is unverified; preserve the fixture and do not replay') from exc
