"""Fail-closed checks for an exact, same-repository candidate workflow run.

This checks provenance metadata only. Callers must also resolve the tag to its
commit and verify the candidate's signatures, attestations and native evidence.
"""
import re
import hashlib


RC_TAG = re.compile(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-rc\.(?:0|[1-9][0-9]*)')
SHA = re.compile(r'[0-9a-f]{40}')
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')


def verify_run(run, run_id, workflow_id, repository, tag, commit):
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        raise ValueError('invalid run id')
    if not isinstance(workflow_id, int) or isinstance(workflow_id, bool) or workflow_id <= 0:
        raise ValueError('invalid workflow id')
    if not isinstance(repository, str) or not REPO.fullmatch(repository):
        raise ValueError('invalid repository')
    if not isinstance(tag, str) or not RC_TAG.fullmatch(tag):
        raise ValueError('invalid candidate tag')
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise ValueError('invalid candidate commit')
    expected = {'id': run_id, 'workflow_id': workflow_id, 'event': 'push',
                'status': 'completed', 'conclusion': 'success', 'head_sha': commit,
                'head_branch': tag}
    if not isinstance(run, dict) or any(run.get(k) != v for k, v in expected.items()):
        raise ValueError('candidate run identity or result mismatch')
    for key in ('repository', 'head_repository'):
        if not isinstance(run.get(key), dict) or run[key].get('full_name') != repository:
            raise ValueError('candidate run repository mismatch')


def verify_human_approval(issue, approval, evidence, owner, run_id, tag, commit, manifest_sha256):
    """Validate candidate-bound, owner-authored acceptance and approval records.

    The API caller must fetch the issue and both comments from the same repository;
    this routine does not establish authenticity of arbitrary input JSON.
    """
    if not re.fullmatch(r'[A-Za-z0-9-]+', owner) or not re.fullmatch(r'[0-9a-f]{64}', manifest_sha256):
        raise ValueError('invalid owner or manifest digest')
    if not RC_TAG.fullmatch(tag) or not SHA.fullmatch(commit) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError('invalid candidate binding')
    if issue.get('number') != 41 or issue.get('state') != 'closed' or issue.get('state_reason') != 'completed':
        raise ValueError('voiceover issue is not completed')
    closed_at = issue.get('closed_at', '')
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', closed_at):
        raise ValueError('invalid issue closure time')
    for item in (approval, evidence):
        if item.get('user', {}).get('login') != owner or item.get('author_association') != 'OWNER':
            raise ValueError('record is not from repository owner')
    created_at = approval.get('created_at', '')
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', created_at) or created_at < closed_at:
        raise ValueError('approval must follow current issue closure')
    updated_at = evidence.get('updated_at', '')
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', updated_at) or updated_at > created_at:
        raise ValueError('acceptance was modified after approval')
    evidence_id = evidence.get('id')
    if not isinstance(evidence_id, int) or isinstance(evidence_id, bool) or evidence_id <= 0:
        raise ValueError('invalid evidence comment id')
    if evidence.get('issue_url') != f'https://api.github.com/repos/{owner}/star-survey/issues/41':
        raise ValueError('acceptance evidence belongs to a different issue')
    binding = f'run: {run_id}\ntag: {tag}\ncommit: {commit}\nmanifest-sha256: {manifest_sha256}\n'
    evidence_prefix = ('SURVEY-VOICEOVER-ACCEPTANCE v1\n' + binding + 'result: passed\n'
                       'coverage: editor,keyboard,focus,errors,save,preview,publish,respond\nmethod: manual-voiceover\n')
    record = re.fullmatch(re.escape(evidence_prefix) + r'tester: ([A-Za-z0-9_-]{2,39})\nindependent-reviewer: ([A-Za-z0-9_-]{2,39})\nevidence: ([^\r\n]{25,1000})', evidence.get('body', ''))
    if record is None or record.group(1).lower() == record.group(2).lower():
        raise ValueError('manual acceptance and independent review evidence required')
    approval_body = ('SURVEY-RELEASE-APPROVAL v1\n' + binding + 'voiceover-result: passed\n'
                     f'voiceover-evidence: https://github.com/{owner}/star-survey/issues/41#issuecomment-{evidence_id}\n'
                     'decision: approve-publication\n'
                     'voiceover-evidence-sha256: ' + hashlib.sha256(evidence['body'].encode('utf-8')).hexdigest())
    if approval.get('body') != approval_body:
        raise ValueError('human records do not match the exact candidate and required acceptance')


def main():
    import argparse
    import json
    import subprocess
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True, type=int)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--assets')
    parser.add_argument('--approval-comment-id', type=int)
    parser.add_argument('--evidence-comment-id', type=int)
    args = parser.parse_args()
    if not REPO.fullmatch(args.repository) or not RC_TAG.fullmatch(args.tag) or not SHA.fullmatch(args.commit) or args.run_id <= 0:
        parser.error('invalid candidate binding')
    def api(path):
        return json.loads(subprocess.check_output(['gh', 'api', path], text=True))
    workflow = api(f'repos/{args.repository}/actions/workflows/release.yml')
    run = api(f'repos/{args.repository}/actions/runs/{args.run_id}')
    verify_run(run, args.run_id, workflow.get('id'), args.repository, args.tag, args.commit)
    actual = subprocess.check_output(['git', 'rev-parse', f'refs/tags/{args.tag}^{{commit}}'], text=True).strip()
    if actual != args.commit:
        raise SystemExit('candidate tag does not point to candidate commit')
    if any((args.manifest_sha256, args.assets, args.approval_comment_id, args.evidence_comment_id)):
        import hashlib
        from pathlib import Path
        import urllib.request
        if args.repository != 'lsgoodlionel/star-survey' or not args.assets or not args.manifest_sha256:
            raise SystemExit('invalid promotion identity')
        if not args.approval_comment_id or not args.evidence_comment_id or args.approval_comment_id <= 0 or args.evidence_comment_id <= 0:
            raise SystemExit('approval and acceptance comment ids required')
        assets = Path(args.assets)
        manifest = assets / 'release.json'
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != args.manifest_sha256:
            raise SystemExit('candidate manifest digest mismatch')
        readiness = json.loads((assets / 'release-readiness.json').read_text())
        if readiness.get('promotable') is not True or readiness.get('reason') != 'upgrade-verified':
            raise SystemExit('bootstrap and unverified upgrades cannot be promoted')
        def public_api(path):
            request = urllib.request.Request('https://api.github.com/' + path,
                                             headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'survey-release-gate'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        prefix = f'repos/{args.repository}/issues'
        issue = public_api(prefix + '/41')
        approval = public_api(prefix + f'/comments/{args.approval_comment_id}')
        evidence = public_api(prefix + f'/comments/{args.evidence_comment_id}')
        expected_url = f'https://api.github.com/repos/{args.repository}/issues/41'
        if approval.get('id') != args.approval_comment_id or approval.get('issue_url') != expected_url or evidence.get('id') != args.evidence_comment_id:
            raise SystemExit('human record is not on the acceptance issue')
        verify_human_approval(issue, approval, evidence, 'lsgoodlionel', args.run_id, args.tag, args.commit, args.manifest_sha256)


if __name__ == '__main__':
    main()
