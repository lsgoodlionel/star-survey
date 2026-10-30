"""Run production Engine image contracts against an already built local image."""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main(image):
    name = 'survey-engine-smoke-' + uuid.uuid4().hex[:10]
    try:
        docker('run', '-d', '--name', name, '--security-opt', 'no-new-privileges:true',
               '-p', '127.0.0.1::80', image)
        assert docker('exec', name, 'id', '-u') == '10001'
        docker('exec', name, 'httpd', '-t')
        docker('exec', name, 'php', '-r',
               'foreach (["calendar","ctype","curl","dom","fileinfo","gd","iconv","intl",'
               '"ldap","mbstring","mysqli","pdo_mysql","pdo_pgsql","pgsql","session",'
               '"sodium","xml","xmlreader","xmlwriter","zip","zlib"] as $m) '
               '{if (!extension_loaded($m)) {fwrite(STDERR,$m);exit(1);}} '
               'if (!session_start()) exit(2); require "vendor/autoload.php";')
        docker('exec', '-u', '0', name, 'sh', '-ec',
               'mkdir -p upload/surveys/999/files tmp/assets tmp/runtime; '
               'echo private > upload/surveys/999/files/fu_probe.txt; '
               'echo private > upload/surveys/999/files/futmp_probe.txt; '
               'echo public > upload/surveys/999/files/public.txt; '
               'echo private > tmp/runtime/probe.txt; echo public > tmp/assets/probe.txt')
        port = docker('port', name, '80/tcp').split(':')[-1]
        for _ in range(60):
            state = json.loads(docker('inspect', name))[0]['State']
            if state.get('Health', {}).get('Status') == 'healthy':
                break
            assert state['Running'], state
            time.sleep(1)
        else:
            raise AssertionError('Engine health check did not become healthy')
        for path, expected in [('/upload/surveys/999/files/fu_probe.txt', 403),
                               ('/upload/surveys/999/files/futmp_probe.txt', 403),
                               ('/upload/surveys/999/files/public.txt', 200),
                               ('/tmp/runtime/probe.txt', 403), ('/tmp/assets/probe.txt', 200)]:
            try:
                with urllib.request.urlopen('http://127.0.0.1:' + port + path, timeout=5) as response:
                    code = response.status
            except urllib.error.HTTPError as error:
                code = error.code
            assert code == expected, (path, code, expected)
        print('Engine image contracts passed')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main(sys.argv[1])
