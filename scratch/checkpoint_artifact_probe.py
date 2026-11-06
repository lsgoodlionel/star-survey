#!/usr/bin/env python3
"""CI-only proof of preserving a pending marker after a killed child."""
import os, signal, subprocess, sys, tempfile, time
from pathlib import Path

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('hosted CI only')
root = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
evidence = root / 'checkpoint-evidence'
evidence.mkdir(mode=0o700)
work = Path(tempfile.mkdtemp(prefix='checkpoint-work-')).resolve()
module = root / 'platform/deploy/production/release'
code = '''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from fixture_checkpoint import run_checkpointed
run_checkpointed(Path(sys.argv[2]),Path(sys.argv[3]),'a'*64,lambda: time.sleep(60),lambda: True)
'''
proc = subprocess.Popen([sys.executable,'-c',code,str(module),str(evidence),str(work)])
try:
    marker = evidence / 'fixture-recovery.pending'
    deadline = time.monotonic() + 10
    while not marker.is_file() and time.monotonic() < deadline: time.sleep(.05)
    if not marker.is_file(): raise RuntimeError('pending marker not created')
    proc.send_signal(signal.SIGKILL); proc.wait(timeout=5)
    if not marker.is_file() or marker.read_text() != 'a'*64 + '\n': raise RuntimeError('pending marker not retained')
    print('killed child left pending marker available to artifact uploader; host loss remains unverified')
finally:
    if proc.poll() is None: proc.kill(); proc.wait(timeout=5)
    work.rmdir()
