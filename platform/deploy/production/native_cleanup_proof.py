"""Candidate-bound ordinary uninstall with an independent ownership postcheck.

The trusted native harness supplies an already authenticated candidate binding.
This does not authenticate signatures, erase host fixtures, authorize restore,
or qualify a candidate. No recovery or replay is attempted on any failure.
The installation lock spans preflight, the existing controller operation and
postcheck; the trust boundary remains an exclusive, trusted controller/daemon.
"""
from copy import deepcopy
import re


def _bootstrap_controller():
    """Read the trusted controller entry as source, never import its pyc.

    The native harness authenticates this entry and its bundle before launch,
    as for surveyctl itself. The stdlib-only controller then admits all owned
    source bytes against the installed inventory before any owned code runs.
    """
    import importlib.util
    import os
    from pathlib import Path
    import stat
    import sys
    path=Path(__file__).absolute().with_name('surveyctl.py')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|getattr(os,'O_CLOEXEC',0))
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1
                or before.st_uid!=os.geteuid() or before.st_mode & 0o022):
            raise RuntimeError('unsafe controller bootstrap source')
        payload=stream.read(1024*1024+1)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid,s.st_nlink)
        if (len(payload)>1024*1024 or identity(before)!=identity(os.fstat(stream.fileno()))
                or identity(before)!=identity(os.stat(path,follow_symlinks=False))):
            raise RuntimeError('controller bootstrap source changed')
    name='surveyctl_native_cleanup_bootstrap'
    if name in sys.modules:
        raise RuntimeError('controller bootstrap is already occupied')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    try:
        exec(compile(payload,str(path),'exec',dont_inherit=True),module.__dict__)
    except BaseException:
        sys.modules.pop(name,None)
        raise
    return module


def main(argv=None):
    """Cold CLI bootstrap: stdlib/controller only, then verified named proof."""
    import argparse
    import json
    import sys
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True)
    for option in ('version','commit','manifest-sha256','bundle-sha256','run-id','attempt','ubuntu','architecture'):
        parser.add_argument('--'+option,required=True)
    args=parser.parse_args(argv)
    binding=dict(schemaVersion=1,version=args.version,commit=args.commit,
        manifestSha256=args.manifest_sha256,bundleSha256=args.bundle_sha256,
        runId=args.run_id,attempt=args.attempt,ubuntu=args.ubuntu,architecture=args.architecture)
    try:
        controller=_bootstrap_controller()
        if any(name in sys.modules for name in controller.OWNED_IMPORT_ORDER):
            raise RuntimeError('cold native entry contains preloaded owned modules')
        manager=controller.SurveyManager(args.target)
        manager._load_owned_support(args.version)
        admitted=sys.modules['native_cleanup_proof']
        result=admitted.prove_uninstall(manager,binding)
    except Exception:
        print('Native ordinary uninstall proof unavailable; preserve fixture and do not replay.',file=sys.stderr)
        return 4
    print(json.dumps(result,sort_keys=True))
    return 0


# Script execution stops here. Only the checksum-admitted named module below
# may import the owned exception class or expose the actual proof operation.
if __name__=='__main__':
    raise SystemExit(main())

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
    installed = manager._verify_staged_release(manager._release_dir(version), version)
    if installed != {key: binding[key] for key in ('version','manifestSha256','architecture','bundleSha256')}:
        raise CleanupProofError('native cleanup candidate differs from the verified installed bundle')
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
