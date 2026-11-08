"""Conservative CI routing and fail-closed aggregate check; standard library only."""
import json
import os
from pathlib import Path, PurePosixPath
import sys
import urllib.request


def paths_from_records(records):
    paths = []
    for record in records:
        paths.append(record.get('filename'))
        if record.get('previous_filename'):
            paths.append(record['previous_filename'])
    return paths


def plan(paths, force_full=False):
    if not paths:
        return 'full', True
    policy = False
    for path in paths:
        if not isinstance(path, str) or path.startswith('/') or '..' in PurePosixPath(path).parts:
            return 'full', True
        if path == '.github/release/baseline-policy.json':
            policy = True
        elif path.endswith('.md') and (path.startswith(('docs/', 'platform/docs/'))
                or (path.startswith('README') and '/' not in path)):
            continue
        else:
            return 'full', True
    return ('full' if force_full else 'policy' if policy else 'docs'), False


def changed_paths(event_name, event, api):
    if event_name == 'pull_request':
        number = int(event['pull_request']['number'])
        pr = api(f'pulls/{number}')
        if pr['head']['sha'] != event['pull_request']['head']['sha']:
            return None
        records = []
        for page in range(1, 31):
            batch = api(f'pulls/{number}/files?per_page=100&page={page}')
            records.extend(batch)
            if len(batch) < 100:
                break
        if len(records) >= 3000 or len(records) != pr['changed_files']:
            return None
        return paths_from_records(records)
    if event_name == 'push':
        before, after = event['before'], event['after']
        if before == '0' * 40:
            return None
        records = api(f'compare/{before}...{after}').get('files')
        if records is None or len(records) >= 300:
            return None
        return paths_from_records(records)
    return None


def accept_results(profile, images, changes, quality, build, relay_requested=False, relay='skipped'):
    if profile not in {'full', 'policy', 'docs'} or changes != 'success':
        return False
    return (quality == ('skipped' if profile == 'docs' else 'success')
            and build == ('success' if images else 'skipped')
            and relay == ('success' if relay_requested else 'skipped'))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'summary':
        needs = json.loads(os.environ['CI_NEEDS'])
        outputs = needs['changes']['outputs']
        relay_requested = os.environ.get('RELAY_REQUESTED')
        ok = (relay_requested in {'true', 'false'} and
              accept_results(outputs.get('profile'), outputs.get('images') == 'true',
                             needs['changes']['result'], needs['quality']['result'], needs['images']['result'],
                             relay_requested == 'true', needs['relay']['result']))
        print(json.dumps({'accepted': ok, 'jobs': needs}, ensure_ascii=False))
        return 0 if ok else 1
    event_name = os.environ['GITHUB_EVENT_NAME']
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    repository = os.environ['GITHUB_REPOSITORY']
    def api(path):
        request = urllib.request.Request(f'https://api.github.com/repos/{repository}/{path}', headers={
            'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
            'Accept': 'application/vnd.github+json',
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    try:
        paths = changed_paths(event_name, event, api)
    except Exception as exc:
        # Do not log API responses, headers, URLs or credentials.
        print(f'Changed-file evidence unavailable ({type(exc).__name__}); using full CI')
        paths = None
    profile, images = plan(paths, force_full=event_name != 'pull_request')
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'profile={profile}\nimages={str(images).lower()}\n')
    print(f'CI plan: profile={profile}, image-build={images}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
