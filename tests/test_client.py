from pathlib import Path
import json
import plistlib
import tempfile
import unittest
from unittest import mock

from cliproxy_config.client import application_list, configuration_directory, resolve_client
from cliproxy_config.crypto import ConfigError


class ClientSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def application(self, identifier="com.openai.codex"):
        app = self.root / "任意安装路径" / "ChatGPT.app"
        (app / "Contents").mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier}))
        return app

    def test_application_location_is_independent_of_user_config(self):
        application = self.application()
        home = self.root / "另一个用户名"
        selected, directory = resolve_client(application, home, {})
        self.assertEqual(selected, application)
        self.assertEqual(directory, home / ".codex")

    def test_respects_custom_environment_and_linux_manager_record(self):
        home = self.root / "home"
        custom = self.root / "不同配置位置"
        self.assertEqual(configuration_directory(home, {"CODEX_HOME": str(custom)}), custom)
        self.assertEqual(configuration_directory(home, {"CODEX_HOME": "~/custom"}), home / "custom")
        record = home / "Library/Application Support/linux服务器管理/client-config-directories.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"codex": str(custom)}), encoding="utf-8")
        self.assertEqual(configuration_directory(home, {}), custom)
        self.assertEqual(resolve_client(self.application(), home, {})[1], custom)

    def test_invalid_or_unsupported_apps_are_not_assigned_a_config(self):
        with self.assertRaises(ConfigError):
            resolve_client(self.application("com.other.application"), self.root, {})
        with self.assertRaises(ConfigError):
            resolve_client(self.root / "missing.app", self.root, {})
        with self.assertRaises(ConfigError):
            configuration_directory(self.root, {"CODEX_HOME": "relative"})

    def test_windows_app_can_be_in_an_arbitrary_install_folder(self):
        executable = self.root / "便携 客户端" / "ChatGPT.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        _, directory = resolve_client(executable, self.root / "新电脑用户", {})
        self.assertEqual(directory, self.root / "新电脑用户/.codex")

    def test_application_list_reads_client_name_and_icon(self):
        app = self.application()
        info_path = app / "Contents/Info.plist"
        info_path.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.openai.codex", "CFBundleDisplayName": "ChatGPT"}))
        with mock.patch("cliproxy_config.client.installed_clients", return_value=[app]), \
             mock.patch("cliproxy_config.client.application_icon", return_value=b"fixture-image"):
            rows = application_list()
        self.assertEqual(rows[0].name, "ChatGPT")
        self.assertEqual(rows[0].path, app)
        self.assertEqual(rows[0].icon, b"fixture-image")


if __name__ == "__main__":
    unittest.main()
