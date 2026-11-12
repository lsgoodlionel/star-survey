"""Read-only release/config binding for a future recovery controller.

No restore admission, runtime packaging or CLI caller. Assets must be supplied by
an explicitly selected trusted candidate flow, never by a request body. Uses the
existing full release verifier; no caller-provided `verified=True` is accepted.
Trusted application/exclusive-controller boundary, not hostile same-UID defence.
"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import signal
import time

from controlled_database_import import NewCopyTarget
from runtime_controller import source_contract
from owned_docker_exec import UncertainExec

REPOSITORY='lsgoodlionel/star-survey'
VERIFIER=Path(__file__).resolve().parent/'release/verify_release.sh'

class TargetBindingError(Exception):
    pass


class TargetVerificationUncertain(TargetBindingError,UncertainExec):
    """Retain verification context; never admit SQL on unknown helper state."""


def _group_absent(pgid):
    try:os.killpg(pgid,0)
    except ProcessLookupError:return True
    except OSError:return False
    return False


def _terminate_group(process,timeout):
    # A timed-out wait has not reaped our leader. Signal its original process
    # group once while that PID cannot be reused; never signal after wait/reap.
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    except OSError:return False
    deadline=time.monotonic()+timeout
    try:process.wait(timeout=max(0.001,deadline-time.monotonic()))
    except BaseException:return False
    while not _group_absent(process.pid):
        if time.monotonic()>=deadline:return False
        time.sleep(min(0.02,max(0,deadline-time.monotonic())))
    return True


def _run_verifier(command,timeout=180,cleanup_timeout=3):
    try:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
    except OSError as error:
        raise TargetBindingError('target release verification unavailable') from error
    try:process.wait(timeout=timeout)
    except BaseException as error:
        settled=_terminate_group(process,cleanup_timeout)
        if not settled:
            failure=TargetVerificationUncertain('verifier process group outcome unknown; retain context')
            failure.verifier_process=process
            raise failure from error
        if not isinstance(error,Exception):raise
        raise TargetBindingError('target release verification interrupted; process group absent') from error
    if not _group_absent(process.pid):
        # The leader was already reaped. Do not risk signaling a reused group
        # ID; unexpected descendants remain unknown, even after exit 0.
        failure=TargetVerificationUncertain('verifier exited with a nonempty or unobservable process group')
        failure.verifier_process=process
        raise failure
    return process


def _snapshot(root):
    """Bounded flat assets; record identity and content, never return raw bodies."""
    try:
        before=root.lstat()
        if (not stat.S_ISDIR(before.st_mode) or before.st_uid not in (0,os.geteuid())
                or before.st_mode&0o022):raise TargetBindingError('target assets directory is unsafe')
        names=sorted(root.iterdir())
        if not 1<=len(names)<=64:raise TargetBindingError('target assets inventory is unbounded')
        def identity(s):return (s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        result={};total=0
        for path in names:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}',path.name):
                raise TargetBindingError('target asset name is unsafe')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
            try:
                info=os.fstat(fd);total+=info.st_size
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                        or info.st_uid not in (0,os.geteuid()) or info.st_mode&0o022
                        or not 0<info.st_size<=8*1024**3 or total>16*1024**3):
                    raise TargetBindingError('target asset is unsafe or oversized')
                digest=hashlib.sha256();remaining=info.st_size
                while remaining:
                    block=os.read(fd,min(65536,remaining))
                    if not block:raise TargetBindingError('target asset was truncated')
                    remaining-=len(block);digest.update(block)
                if (os.read(fd,1) or identity(info)!=identity(os.fstat(fd))
                        or identity(info)!=identity(path.lstat())):
                    raise TargetBindingError('target asset changed while reading')
                result[path.name]=(identity(info),digest.hexdigest())
            finally:os.close(fd)  # No retry of a potentially reused descriptor.
        if identity(before)!=identity(root.lstat()):raise TargetBindingError('target directory changed')
        return result
    except OSError as error:raise TargetBindingError('target assets observation failed') from error


class RecoveryTargetBinding:
    """Pin verified assets plus staged effective Compose; still not permission."""
    def __init__(self,manager,version,assets,manifest_sha256,commit):
        if (not isinstance(version,str) or re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-rc\.(0|[1-9][0-9]*))?',version) is None
                or not isinstance(manifest_sha256,str) or re.fullmatch('[0-9a-f]{64}',manifest_sha256) is None
                or not isinstance(commit,str) or re.fullmatch('[0-9a-f]{40}',commit) is None):
            raise TargetBindingError('target identity must be explicit')
        self.manager,self.version,self.manifest_sha256,self.commit=manager,version,manifest_sha256,commit
        raw=Path(assets)
        if not raw.is_absolute() or raw.is_symlink():raise TargetBindingError('target assets path is unsafe')
        self.assets=raw.resolve(strict=True)
        self.assets_view=_snapshot(self.assets)
        if self.assets_view.get('release.json',(None,None))[1]!=manifest_sha256:
            raise TargetBindingError('target manifest digest differs')
        helper=VERIFIER.read_bytes();self.helper_sha=hashlib.sha256(helper).hexdigest()
        try:
            result=_run_verifier(['bash',str(VERIFIER),'--assets',str(self.assets),'--repository',REPOSITORY,
                '--target-tag','v'+version,'--source-tag','v'+version,'--target-commit',commit],
                timeout=180)
        except (OSError,subprocess.TimeoutExpired) as error:
            raise TargetBindingError('target release verification unavailable') from error
        if type(result.returncode) is not int or result.returncode!=0:
            raise TargetBindingError('target release verification rejected')
        self.data=deepcopy(self._configuration())
        self.recheck()

    def _configuration(self):
        try:
            # Existing implementation verifies retained signed bundle checksum,
            # generated-file integrity and actual Compose/local image projection.
            data=source_contract(self.manager,self.version)
            manifest=self.manager._release_manifest(self.version)
            if (data['version']!=self.version or data['manifestSha256']!=self.manifest_sha256
                    or manifest.sha256!=self.manifest_sha256 or manifest.raw['commit']!=self.commit):
                raise TargetBindingError('staged target identity differs from verified assets')
            for service in ('platform-db','engine-db'):
                ids=data['contract']['services'][service]['imageIds']
                if (not isinstance(ids,list) or len(ids)!=1 or not isinstance(ids[0],str)
                        or re.fullmatch('sha256:[0-9a-f]{64}',ids[0]) is None):
                    raise TargetBindingError('target database image identity is invalid')
            return data
        except TargetBindingError:raise
        except Exception as error:raise TargetBindingError('target effective configuration unavailable') from error

    def recheck(self):
        if (hashlib.sha256(VERIFIER.read_bytes()).hexdigest()!=self.helper_sha
                or _snapshot(self.assets)!=self.assets_view or self._configuration()!=self.data):
            raise TargetBindingError('verified target assets or effective configuration changed')
        return {'schemaVersion':1,'releaseSignaturesVerified':True,'effectiveConfigurationSnapshotBound':True,
                'manifestSha256':self.manifest_sha256,'commit':self.commit,
                'targetConfigurationVerified':False,'restoreAuthorized':False,'candidateQualified':False}

    def check_database_target(self,target):
        report=self.recheck()
        if not isinstance(target,NewCopyTarget) or target.failed:
            raise TargetBindingError('new database target controller required')
        for service in ('platform-db','engine-db'):
            expected=self.data['contract']['services'][service]['imageIds']
            actual=target.protocol.driver.contract['services'].get(service,{}).get('imageIds')
            if actual!=expected or not target.check(service,target.services.get(service)):
                raise TargetBindingError('database target no longer matches verified release projection')
        # This checks the image IDs and owned isolation. Passwords, imported
        # roles, app settings and the full product topology are not approved.
        return dict(report,databaseImagesBound=True,targetConfigurationVerified=False)
