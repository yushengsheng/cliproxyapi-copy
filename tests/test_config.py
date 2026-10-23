from __future__ import annotations

import base64
import copy
import json
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

from cliproxy_config import config
from cliproxy_config.agents import apply_agent
from cliproxy_config.config import (FILES, PROVIDER, ConfigError, atomic_write, render_config, restore_backup)
from cliproxy_config.crypto import MAX_FILE_BYTES, decrypt, encrypt
from cliproxy_config.gateway import make_connection
from model_fixtures import model_row


PASSWORD = "fixture-密码-12345"
KEY = "fixture-secret-only"


def connection():
    payload = make_connection('https://fixture.invalid/v1', KEY,
        [model_row('fixture-model', context=64000, display_name='测试模型'), model_row('fixture-second')], 'fixture-model')
    payload['model_settings'].update(model_reasoning_effort='high', model_context_window=64000)
    payload['provider_settings']['stream_idle_timeout_ms'] = 300000
    return payload


class EncryptionTests(unittest.TestCase):
    def test_roundtrip_randomness_and_no_plain_secrets(self):
        payload = connection()
        first, second = encrypt(payload, PASSWORD), encrypt(payload, PASSWORD)
        self.assertNotEqual(first, second)
        self.assertNotIn(KEY, first)
        self.assertNotIn(payload["base_url"], first)
        self.assertNotIn(PASSWORD, first)
        self.assertEqual(decrypt(first, PASSWORD), payload)
        self.assertEqual(decrypt(first.encode(), PASSWORD), payload)

    def test_wrong_password_tampering_and_truncation(self):
        content = encrypt(connection(), PASSWORD)
        with self.assertRaisesRegex(ConfigError, "密码不正确"):
            decrypt(content, "wrong-password")
        envelope = json.loads(content)
        cipher = bytearray(base64.b64decode(envelope["data"]))
        cipher[10] ^= 1
        envelope["data"] = base64.b64encode(cipher).decode()
        with self.assertRaisesRegex(ConfigError, "损坏"):
            decrypt(json.dumps(envelope), PASSWORD)
        with self.assertRaises(ConfigError):
            decrypt(content[:50], PASSWORD)

    def test_bounded_fixed_format_and_password(self):
        with self.assertRaises(ConfigError):
            encrypt(connection(), "short")
        with self.assertRaises(ConfigError):
            decrypt(b"x" * (MAX_FILE_BYTES + 1), PASSWORD)
        envelope = json.loads(encrypt(connection(), PASSWORD))
        for key, value in (("version", True), ("kdf", "scrypt-n999999999-r8-p1"), ("salt", "bad"), ("nonce", [])):
            altered = dict(envelope, **{key: value})
            with self.subTest(key=key), self.assertRaises(ConfigError):
                decrypt(json.dumps(altered), PASSWORD)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "用户 空格'中文 &配置"
        self.payload = connection()

    def tearDown(self):
        self.temp.cleanup()

    def assert_applied(self, directory, payload):
        document = tomllib.loads((directory / FILES[0]).read_text(encoding='utf-8'))
        self.assertEqual(document['model'], payload['model'])
        self.assertEqual(document['model_providers'][PROVIDER]['base_url'], payload['base_url'])
        for key, value in payload['model_settings'].items():
            self.assertEqual(document[key], value)
        self.assertEqual((directory / FILES[1]).read_text(encoding='utf-8'), payload['api_key'])
        self.assertEqual(json.loads((directory / FILES[2]).read_bytes()), payload['catalog'])

    def test_preserves_unrelated_config_and_rewrites_native_paths(self):
        original = ('''# 用户注释\nmodel = "old"\nmodel_provider = "other"\nmodel_verbosity = "low"\n'''
                    '''[plugins."fixture@local"]\nenabled = true\n[model_providers.other]\nbase_url = "http://localhost"\n'''
                    '''[mcp_servers.fixture]\ncommand = "keep-me"\n''').encode()
        atomic_write(self.directory / FILES[0], original)
        auth = self.directory / "auth.json"
        atomic_write(auth, b'{"tokens":"do-not-migrate"}')
        backup = apply_agent("codex", self.directory, self.payload, PASSWORD)
        current = tomllib.loads((self.directory / FILES[0]).read_text(encoding="utf-8"))
        before = tomllib.loads(original.decode())
        self.assertIn("# 用户注释", (self.directory / FILES[0]).read_text(encoding="utf-8"))
        for field in ("plugins", "mcp_servers"):
            self.assertEqual(current[field], before[field])
        self.assertEqual(current["model_providers"]["other"], before["model_providers"]["other"])
        self.assertEqual(current["model"], self.payload["model"])
        self.assertNotIn("model_verbosity", current)
        self.assertEqual(current["model_catalog_json"], str(self.directory / FILES[2]))
        self.assert_applied(self.directory, self.payload)
        self.assertEqual(auth.read_bytes(), b'{"tokens":"do-not-migrate"}')
        self.assertNotIn(KEY, backup.read_text(encoding="utf-8"))
        if sys.platform != "win32":
            self.assertEqual((self.directory / FILES[1]).stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            auth_info = current["model_providers"][PROVIDER]["auth"]
            self.assertEqual(subprocess.check_output([auth_info["command"], *auth_info["args"]], text=True), KEY)

    def test_native_catalog_replaces_32k_and_invalid_none_without_touching_other_settings(self):
        rows = [model_row('gpt-6.1-sol', 'openai', 500000, ('low', 'medium', 'high', 'xhigh', 'max'),
                          default_reasoning_level='low'),
                model_row('claude-opus-5-5', 'anthropic', 1000000)]
        for default in ('claude-opus-5-5', 'gpt-6.1-sol'):
            with self.subTest(default=default):
                payload = make_connection('https://fixture.invalid/v1', KEY, rows, default)
                payload['model_settings']['model_reasoning_effort'] = 'none'
                original = (b'model_reasoning_effort="none"\nmodel_context_window=32768\n'
                            b'model_auto_compact_token_limit=28000\n[mcp_servers.keep]\ncommand="keep"\n'
                            b'[models.new_thread]\nmodel="old"\nreasoning_effort="none"\n')
                document = tomllib.loads(render_config(original, payload, self.directory).decode())
                self.assertEqual(document['model_reasoning_effort'], 'low' if default == 'gpt-6.1-sol' else 'medium')
                self.assertNotIn('model_context_window', document)
                self.assertNotIn('model_auto_compact_token_limit', document)
                self.assertNotIn('reasoning_effort', document['models']['new_thread'])
                self.assertEqual(document['mcp_servers']['keep']['command'], 'keep')
                payload['model_settings']['model_reasoning_effort'] = 'high'
                document = tomllib.loads(render_config(original, payload, self.directory).decode())
                self.assertEqual(document['model_reasoning_effort'], 'high')

    def test_import_onto_another_directory_and_repeated_application(self):
        apply_agent("codex", self.directory, self.payload, PASSWORD)
        exported = encrypt(self.payload, PASSWORD)
        destination = self.root / "新电脑配置"
        apply_agent("codex", destination, decrypt(exported, PASSWORD), PASSWORD)
        self.assert_applied(destination, self.payload)
        self.assertNotIn(str(self.directory), (destination / FILES[0]).read_text(encoding="utf-8"))
        apply_agent("codex", destination, self.payload, PASSWORD)
        parsed = tomllib.loads((destination / FILES[0]).read_text(encoding="utf-8"))
        self.assertEqual(list(parsed["model_providers"]), [PROVIDER])

    def test_encrypted_restore_recovers_exact_bytes_and_missing_files(self):
        original = b'# preserve whitespace\nmodel="old"\n'
        atomic_write(self.directory / FILES[0], original, 0o640)
        backup = apply_agent("codex", self.directory, self.payload, PASSWORD)
        undo_backup = restore_backup(self.directory, backup.read_bytes(), PASSWORD)
        self.assertEqual((self.directory / FILES[0]).read_bytes(), original)
        self.assertFalse((self.directory / FILES[1]).exists())
        self.assertFalse((self.directory / FILES[2]).exists())
        if sys.platform != "win32":
            self.assertEqual((self.directory / FILES[0]).stat().st_mode & 0o777, 0o640)
        restore_backup(self.directory, undo_backup.read_bytes(), PASSWORD)
        self.assert_applied(self.directory, self.payload)

    def test_wrong_restore_password_and_other_directory_do_not_write(self):
        backup = apply_agent("codex", self.directory, self.payload, PASSWORD)
        before = config._snapshot(self.directory)
        with self.assertRaises(ConfigError):
            restore_backup(self.directory, backup.read_bytes(), "wrong-password")
        self.assertEqual(config._snapshot(self.directory), before)
        destination = self.root / "elsewhere"
        with self.assertRaisesRegex(ConfigError, "原配置目录"):
            restore_backup(destination, backup.read_bytes(), PASSWORD)
        self.assertFalse(destination.exists())

    def test_failed_write_rolls_back_original_files(self):
        for name in FILES:
            atomic_write(self.directory / name, b"before")
        atomic_write(self.directory / FILES[0], b'model="old"\n')
        before = config._snapshot(self.directory)
        writer = config.atomic_write
        failed = False
        def faulty(path, content, mode=0o600):
            nonlocal failed
            if path.name == FILES[1] and not failed:
                failed = True
                raise OSError("fixture failure")
            return writer(path, content, mode)
        with mock.patch.object(config, "atomic_write", side_effect=faulty):
            with self.assertRaisesRegex(ConfigError, "自动撤回"):
                apply_agent("codex", self.directory, self.payload, PASSWORD)
        self.assertEqual(config._snapshot(self.directory), before)
        backups = list((self.directory / "cliproxy-config-backups").glob("*.cproxybackup"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(decrypt(backups[0].read_bytes(), PASSWORD)["kind"], "backup")

    def test_profile_invalid_toml_and_symlink_are_rejected(self):
        for original in (b'profile="custom"\n', b"[broken"):
            atomic_write(self.directory / FILES[0], original)
            with self.assertRaises(ConfigError):
                apply_agent("codex", self.directory, self.payload, PASSWORD)
            self.assertEqual((self.directory / FILES[0]).read_bytes(), original)
            self.assertFalse((self.directory / "cliproxy-config-backups").exists())
        if sys.platform != "win32":
            actual = self.root / "actual"
            actual.mkdir()
            link = self.root / "link"
            link.symlink_to(actual)
            with self.assertRaisesRegex(ConfigError, "符号链接"):
                apply_agent("codex", link, self.payload, PASSWORD)
            self.assertEqual(list(actual.iterdir()), [])

    def test_arbitrary_imported_commands_and_invalid_models_are_rejected(self):
        for modify in (lambda p: p.update(command="malicious"),
                       lambda p: p.update(base_url="https://user:pass@fixture.invalid/v1"),
                       lambda p: p.update(model="missing"),
                       lambda p: p["provider_settings"].update(auth={"command": "malicious"}),
                       lambda p: p["catalog"]["models"].append({"slug": "fixture-model"})):
            payload = copy.deepcopy(self.payload)
            modify(payload)
            with self.assertRaises(ConfigError):
                apply_agent("codex", self.directory, payload, PASSWORD)
            self.assertFalse(self.directory.exists())

    def test_windows_auth_quotes_path_and_uses_no_unix_commands(self):
        directory = PureWindowsPath(r"C:\Users\用户名 空格' &\配置")
        output = render_config(None, self.payload, directory, "win32")
        parsed = tomllib.loads(output.decode())
        self.assertEqual(parsed["model_catalog_json"], str(directory / FILES[2]))
        auth = parsed["model_providers"][PROVIDER]["auth"]
        self.assertNotIn("/bin/cat", output.decode())
        self.assertTrue(auth["command"].endswith(r"\WindowsPowerShell\v1.0\powershell.exe"))
        script = base64.b64decode(auth["args"][-1]).decode("utf-16le")
        self.assertIn("用户名 空格'' &", script)
        self.assertIn("ReadAllText", script)

    def test_windows_snapshot_mode_normalization(self):
        states = {name: {"data": b"same", "mode": 0o600} for name in FILES}
        other = {name: {"data": b"same", "mode": 0o666} for name in FILES}
        with mock.patch.object(config.sys, "platform", "win32"):
            self.assertTrue(config._same_states(states, other))
            other[FILES[1]]["data"] = b"changed"
            self.assertFalse(config._same_states(states, other))

    def test_backup_folder_and_mutation_races_abort_before_config_writes(self):
        atomic_write(self.directory / FILES[0], b'model="old"\n')
        original_encrypt = config.encrypt
        def race(payload, password):
            result = original_encrypt(payload, password)
            (self.directory / FILES[0]).write_bytes(b'model="changed-by-user"\n')
            return result
        with mock.patch.object(config, "encrypt", side_effect=race):
            with self.assertRaisesRegex(ConfigError, "备份期间"):
                apply_agent("codex", self.directory, self.payload, PASSWORD)
        self.assertEqual((self.directory / FILES[0]).read_bytes(), b'model="changed-by-user"\n')
        self.assertFalse((self.directory / FILES[1]).exists())

    def test_concurrent_edits_are_not_overwritten_during_rollback(self):
        atomic_write(self.directory / FILES[0], b'model="original"\n')
        writer = config.atomic_write
        def race(path, content, mode=0o600):
            result = writer(path, content, mode)
            if path.name == FILES[0]:
                path.write_bytes(b'model="new-user-edit"\n')
            return result
        with mock.patch.object(config, "atomic_write", side_effect=race):
            with self.assertRaisesRegex(ConfigError, "未覆盖其他程序"):
                apply_agent("codex", self.directory, self.payload, PASSWORD)
        self.assertEqual((self.directory / FILES[0]).read_bytes(), b'model="new-user-edit"\n')
        self.assertFalse((self.directory / FILES[1]).exists())


if __name__ == "__main__":
    unittest.main()
