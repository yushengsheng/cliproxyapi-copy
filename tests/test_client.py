import json
from pathlib import Path
import tempfile
import unittest

from cliproxy_config.client import configuration_directory
from cliproxy_config.crypto import ConfigError


class ConfigurationDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_default_directory_and_environment_override(self):
        home = self.root / '另一个用户'
        custom = self.root / '不同配置位置'
        self.assertEqual(configuration_directory(home, {}), home / '.codex')
        self.assertEqual(configuration_directory(home, {'CODEX_HOME': str(custom)}), custom)
        self.assertEqual(configuration_directory(home, {'CODEX_HOME': '~/custom'}), home / 'custom')
        with self.assertRaises(ConfigError):
            configuration_directory(home, {'CODEX_HOME': 'relative'})

    def test_manager_directory_record_is_preserved_and_environment_takes_precedence(self):
        home = self.root / 'home'
        custom = self.root / 'recorded'
        record = home / 'Library/Application Support/linux服务器管理/client-config-directories.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'codex': str(custom)}), encoding='utf-8')
        self.assertEqual(configuration_directory(home, {}), custom)
        override = self.root / 'override'
        self.assertEqual(configuration_directory(home, {'CODEX_HOME': str(override)}), override)
        record.write_text(json.dumps({'codex': 'relative'}), encoding='utf-8')
        with self.assertRaises(ConfigError):
            configuration_directory(home, {})

    def test_windows_manager_directory_record_uses_appdata(self):
        record = self.root / 'AppData/linux服务器管理/client-config-directories.json'
        record.parent.mkdir(parents=True)
        directory = self.root / '客户端配置'
        record.write_text(json.dumps({'codex': str(directory)}), encoding='utf-8')
        self.assertEqual(configuration_directory(self.root, {'APPDATA': str(record.parent.parent)}), directory)


if __name__ == '__main__':
    unittest.main()
