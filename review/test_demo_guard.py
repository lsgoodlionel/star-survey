"""Exercise the exact demo guard with a stubbed read-only Docker CLI."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path('/tmp/survey-acceptance-46766fd-src/platform/deploy/demo/run-admin-web-demo.sh')

class DemoGuardTest(unittest.TestCase):
    def run_guard(self, collision='', override=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); demo=root/'repo/platform/deploy/demo'; demo.mkdir(parents=True)
            script=demo/'run-admin-web-demo.sh'; shutil.copyfile(SOURCE,script)
            if collision=='runtime': (demo/'.runtime').mkdir()
            if collision=='envfile': (demo/'.env').touch()
            bindir=root/'bin'; bindir.mkdir()
            docker=bindir/'docker'; docker.write_text('''#!/bin/sh
[ "$1" = --host ] && [ "$2" = unix:///Users/lionel/.docker/run/docker.sock ] || exit 92
shift 2
case "$1 $2" in
  "context inspect") if [ "$TEST_COLLISION" = context ]; then echo unix:///other.sock; else echo unix:///Users/lionel/.docker/run/docker.sock; fi ;;
  "info --format") echo disposable-daemon ;;
  "ps -aq") if [ "$TEST_COLLISION" = container ]; then echo collision; fi ;;
  "volume ls") if [ "$TEST_COLLISION" = error_volume ]; then exit 2; fi
               if [ "$TEST_COLLISION" = volume ] || [ "$TEST_COLLISION" = long_volume ]; then echo survey-review-46766fd-maven; fi
               if [ "$TEST_COLLISION" = long_volume ]; then awk 'BEGIN { for (i=0;i<20000;i++) print "other-volume-" i }'; fi ;;
  "network ls") if [ "$TEST_COLLISION" = error_network ]; then exit 2; fi
                if [ "$TEST_COLLISION" = network ] || [ "$TEST_COLLISION" = long_network ]; then echo survey-review-46766fd_default; fi
                if [ "$TEST_COLLISION" = long_network ]; then awk 'BEGIN { for (i=0;i<20000;i++) print "other-network-" i }'; fi ;;
  "image ls") if [ "$TEST_COLLISION" = error_image ]; then exit 2; fi
              if [ "$TEST_COLLISION" = image ] || [ "$TEST_COLLISION" = long_image ]; then echo survey-review-46766fd-admin; fi
              if [ "$TEST_COLLISION" = long_image ]; then awk 'BEGIN { for (i=0;i<20000;i++) print "other-image-" i }'; fi ;;
  *) exit 91 ;;
esac
'''); docker.chmod(0o755)
            grep=bindir/'grep'; grep.write_text('#!/bin/sh\nexit 2\n'); grep.chmod(0o755)
            python=bindir/'python3'; python.write_text('#!/bin/sh\n[ "$TEST_COLLISION" != port ]\n'); python.chmod(0o755)
            env={k:v for k,v in os.environ.items() if k not in ('DOCKER_HOST','DOCKER_CONTEXT','COMPOSE_PROJECT_NAME','COMPOSE_FILE','COMPOSE_PROFILES','COMPOSE_ENV_FILES','ADMIN_WEB_DEMO_PORT','PLATFORM_MAVEN_REPO')}
            env.update(PATH=str(bindir)+os.pathsep+os.environ['PATH'], TEST_COLLISION=collision)
            if override: env['PLATFORM_MAVEN_REPO']='foreign-cache'
            return subprocess.run(['bash','-c','source "$1"; require_empty_review_target','_',str(script)],env=env,capture_output=True,text=True,timeout=10)
    def test_clean_snapshot_passes(self):
        self.assertEqual(self.run_guard().returncode,0)
    def test_each_collision_fails_closed(self):
        for collision in ('context','runtime','envfile','container','volume','network','image','port'):
            with self.subTest(collision=collision): self.assertNotEqual(self.run_guard(collision).returncode,0)
    def test_all_docker_operations_are_routed_through_pinned_array(self):
        source=SOURCE.read_text()
        self.assertIn('DOCKER=(docker --host unix:///Users/lionel/.docker/run/docker.sock)',source)
        self.assertIn('COMPOSE=("${DOCKER[@]}" compose -p',source)
        import re
        remaining=[line for line in source.splitlines() if re.search(r'(?<![A-Za-z0-9_])docker (?:compose|exec|run|inspect|start|stop|image|info|ps|volume|network|context)\b',line)]
        self.assertEqual(remaining,[])

    def test_no_reuse_or_password_sync_in_start(self):
        source=SOURCE.read_text()
        start=source[source.index('start_demo() {'):source.index('\nstatus_demo() {')]
        self.assertNotIn('prepare_existing_database_credentials',start)
        self.assertNotIn('sync_database_credentials',start)
        for operation in ('status','stop','refresh-token'):
            result=subprocess.run(['bash',str(SOURCE),operation],capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('supports start only',result.stderr)

    def test_long_resource_lists_reject_early_collisions(self):
        for collision in ('long_volume', 'long_network', 'long_image'):
            with self.subTest(collision=collision):
                result=self.run_guard(collision)
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertIn('already exists',result.stderr)

    def test_list_failures_reject_and_grep_failure_cannot_hide_collisions(self):
        for collision in ('error_volume','error_network','error_image'):
            with self.subTest(collision=collision):
                result=self.run_guard(collision)
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertIn('list unavailable',result.stderr)
        # Every scenario runs with a fake grep that exits 2; builtins must
        # still find an early collision and reject it.
        for collision in ('long_volume','long_network','long_image'):
            with self.subTest(collision=collision):
                result=self.run_guard(collision)
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertIn('already exists',result.stderr)

    def test_environment_override_fails_closed(self):
        self.assertNotEqual(self.run_guard(override=True).returncode,0)
if __name__=='__main__': unittest.main()
