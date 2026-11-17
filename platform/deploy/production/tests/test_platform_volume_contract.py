"""Guard persistent platform volume boundaries in the production image."""
import json
from pathlib import Path
import shlex
import unittest
import yaml

PRODUCTION = Path(__file__).resolve().parents[1]
DOCKERFILE = PRODUCTION.parents[1] / 'services/business/Dockerfile'


def declared_volumes(source):
    result = []
    for line in source.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or parts[0].upper() != 'VOLUME':
            continue
        value = parts[1]
        if value.endswith('\\'):
            raise ValueError('multiline VOLUME must be reviewed')
        items = json.loads(value) if value.startswith('[') else shlex.split(value, comments=True)
        if not isinstance(items, list) or not items or any(not isinstance(x, str) for x in items):
            raise ValueError('invalid VOLUME')
        result.extend(items)
    return result


class PlatformVolumeContractTest(unittest.TestCase):
    def test_image_does_not_declare_anonymous_parent_volume(self):
        self.assertNotIn('/var/lib/survey', declared_volumes(DOCKERFILE.read_text(encoding='utf-8')))

    def test_volume_parser_covers_multiple_arguments(self):
        for line in ('VOLUME ["/other", "/var/lib/survey"]', 'VOLUME /other /var/lib/survey'):
            with self.subTest(line=line):
                self.assertIn('/var/lib/survey', declared_volumes(line))
        with self.assertRaises(ValueError):
            declared_volumes('VOLUME /other \\')

    def test_assets_and_exports_remain_distinct_managed_named_volumes(self):
        compose = yaml.safe_load((PRODUCTION / 'compose.yml').read_text(encoding='utf-8'))
        mounts = compose['services']['platform']['volumes']
        for name, target in (('platform-assets', '/var/lib/survey/assets'),
                             ('platform-exports', '/var/lib/survey/exports')):
            matching = []
            for mount in mounts:
                if isinstance(mount, str):
                    fields = mount.split(':')
                    if len(fields) >= 2 and fields[1] == target:
                        matching.append(fields)
                elif isinstance(mount, dict) and mount.get('target') == target:
                    matching.append(mount)
            self.assertEqual([[name, target]], matching)
            definition = compose['volumes'][name] or {}
            self.assertFalse(definition.get('external', False))
            self.assertNotIn('name', definition)


if __name__ == '__main__':
    unittest.main()
