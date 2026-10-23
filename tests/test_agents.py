from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ruamel.yaml import YAML

from cliproxy_config import config
from cliproxy_config.agents import (AGENTS, PI_PROVIDER, apply_agent, default_directory, preview_agent,
                                   render_agent, restore_agent)
from cliproxy_config.crypto import ConfigError, decrypt
from cliproxy_config.gateway import make_connection
from test_config import PASSWORD
from model_fixtures import model_row


def payload(key='fixture-secret'):
    return make_connection('https://fixture.invalid/v1', key,
        [model_row('claude-fixture-one', 'fixture'), model_row('claude-fixture-two', 'fixture')], 'claude-fixture-two')


class AgentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_all_agents_apply_exact_selection_and_restore_original_bytes(self):
        originals = {
            'claude': {'claude_desktop_config.json': b'{"mcpServers":{"keep":{}},"deploymentMode":"1p"}',
                       'configLibrary/_meta.json': b'{"entries":[{"id":"keep","name":"keep"}],"appliedId":"keep"}'},
            'codex': {'config.toml': b'# comment\n[mcp_servers.keep]\ncommand="keep"\n'},
            'claude-code': {'settings.json': b'{"permissions":{"allow":["Read"]},"env":{"KEEP":"yes"}}'},
            'pi': {'models.json': b'{"providers":{"other":{"baseUrl":"https://keep.invalid"}}}',
                   'settings.json': b'{"theme":"keep"}', 'auth.json': b'{"other":{"type":"api_key","key":"keep"}}'},
            'hermes': {'config.yaml': b'# keep comment\nterminal:\n  backend: local\nproviders:\n  other:\n    api: https://keep.invalid/v1\n'},
        }
        for agent, definition in AGENTS.items():
            with self.subTest(agent=agent):
                directory = self.root / agent
                for name, data in originals[agent].items():
                    config.atomic_write(directory / name, data)
                if agent != 'pi':
                    config.atomic_write(directory / 'auth.json', b'{"account":{"type":"oauth","refresh":"fixture-original"}}')
                original_auth = (directory / 'auth.json').read_bytes()
                before = config._snapshot(directory, definition.files)
                backup = apply_agent(agent, directory, payload(), PASSWORD)
                self.assertNotIn('fixture-secret', backup.read_text())
                after = config._snapshot(directory, definition.files)
                rendered = render_agent(agent, {}, payload(), directory)
                combined = b'\n'.join(row['data'] for row in after.values())
                for name in ('claude-fixture-one', 'claude-fixture-two'):
                    self.assertIn(name.encode(), combined)
                self.assertIn(b'fixture-secret', combined)
                if agent == 'claude-code':
                    doc = json.loads(after['settings.json']['data'])
                    self.assertEqual(doc['permissions'], {'allow': ['Read']})
                    self.assertEqual(doc['env']['KEEP'], 'yes')
                    self.assertEqual(doc['availableModels'], ['claude-fixture-one', 'claude-fixture-two'])
                elif agent == 'pi':
                    self.assertIn('other', json.loads(after['auth.json']['data']))
                    self.assertEqual(json.loads(after['settings.json']['data'])['theme'], 'keep')
                    self.assertEqual(len(json.loads(after['models.json']['data'])['providers'][PI_PROVIDER]['models']), 2)
                elif agent == 'hermes':
                    self.assertIn(b'# keep comment', combined)
                    doc = YAML().load(after['config.yaml']['data'])
                    self.assertEqual(doc['terminal']['backend'], 'local')
                    self.assertFalse(doc['providers']['cliproxyapi']['discover_models'])
                undo = restore_agent(agent, directory, backup.read_bytes(), PASSWORD)
                self.assertEqual(config._snapshot(directory, definition.files), before)
                restore_agent(agent, directory, undo.read_bytes(), PASSWORD)
                self.assertEqual(config._snapshot(directory, definition.files), after)
                if agent != 'pi':
                    self.assertEqual((directory / 'auth.json').read_bytes(), original_auth)
                self.assertEqual(set(rendered), set(definition.files))

    def test_pi_keys_are_literal_and_do_not_become_shell_commands(self):
        key = '!echo ${DO_NOT_RUN}'
        result = render_agent('pi', {}, payload(key), self.root)
        self.assertEqual(json.loads(result['auth.json'])[PI_PROVIDER]['key'], key)
        self.assertNotIn('apiKey', json.loads(result['models.json'])['providers'][PI_PROVIDER])

    def test_pi_missing_input_modalities_keep_a_valid_text_default(self):
        connection = make_connection('https://fixture.invalid/v1', 'key',
                                     [model_row('custom', input_modalities=None)], 'custom')
        result = render_agent('pi', {}, connection, self.root)
        models = json.loads(result['models.json'])['providers'][PI_PROVIDER]['models']
        self.assertEqual(models[0]['input'], ['text'])

    def test_pi_import_preserves_existing_cli_proxy_plugin_and_authentication(self):
        directory = self.root / 'pi-with-plugin'
        plugin = {'baseUrl': 'https://plugin.invalid/v1', 'models': [{'id': 'plugin-model'}]}
        credential = {'type': 'oauth', 'access': 'fixture-plugin-access', 'refresh': 'fixture-plugin-refresh', 'expires': 0}
        documents = {
            'models.json': {'providers': {'cliproxyapi': plugin}},
            'auth.json': {'cliproxyapi': credential},
            'settings.json': {'packages': ['npm:@router-for-me/pi-cliproxyapi-provider'], 'theme': 'keep'},
        }
        for name, document in documents.items():
            config.atomic_write(directory / name, json.dumps(document).encode())
        before = config._snapshot(directory, AGENTS['pi'].files)
        backup = apply_agent('pi', directory, payload(), PASSWORD)
        models = json.loads((directory / 'models.json').read_bytes())
        auth = json.loads((directory / 'auth.json').read_bytes())
        settings = json.loads((directory / 'settings.json').read_bytes())
        self.assertEqual(models['providers']['cliproxyapi'], plugin)
        self.assertEqual(auth['cliproxyapi'], credential)
        self.assertEqual(auth[PI_PROVIDER], {'type': 'api_key', 'key': 'fixture-secret'})
        self.assertEqual(settings['packages'], documents['settings.json']['packages'])
        self.assertEqual(settings['theme'], 'keep')
        self.assertEqual(settings['defaultProvider'], PI_PROVIDER)
        self.assertNotIn('enabledModels', settings)
        restore_agent('pi', directory, backup.read_bytes(), PASSWORD)
        self.assertEqual(config._snapshot(directory, AGENTS['pi'].files), before)

    def test_pi_keeps_oauth_defaults_thinking_and_model_scope_on_repeated_import(self):
        directory = self.root / 'pi-oauth'
        oauth = {'type': 'oauth', 'access': 'fixture-access', 'refresh': 'fixture-refresh', 'expires': 9999999999999}
        settings = {'defaultProvider': 'openai-codex', 'defaultModel': 'gpt-5.1-codex',
                    'defaultThinkingLevel': 'high', 'enabledModels': ['openai-codex/*', 'other/custom:medium']}
        config.atomic_write(directory / 'auth.json', json.dumps({'openai-codex': oauth}).encode())
        config.atomic_write(directory / 'settings.json', json.dumps(settings).encode())
        for _ in range(2):
            apply_agent('pi', directory, payload(), backup=False)
            result = json.loads((directory / 'settings.json').read_bytes())
            self.assertEqual({key: result[key] for key in settings if key != 'enabledModels'},
                             {key: settings[key] for key in settings if key != 'enabledModels'})
            self.assertEqual(result['enabledModels'], settings['enabledModels'] +
                             [PI_PROVIDER + '/claude-fixture-one', PI_PROVIDER + '/claude-fixture-two'])
            self.assertEqual(json.loads((directory / 'auth.json').read_bytes())['openai-codex'], oauth)

    def test_pi_unrestricted_scope_and_unchanged_files_are_not_rewritten(self):
        directory = self.root / 'pi-unrestricted'
        for scope in (None, []):
            settings = {'defaultProvider': 'openai-codex', 'defaultModel': 'gpt-5.1-codex'}
            if scope is not None:
                settings['enabledModels'] = scope
            original = json.dumps(settings).encode()
            config.atomic_write(directory / 'settings.json', original)
            apply_agent('pi', directory, payload(), backup=False)
            self.assertEqual((directory / 'settings.json').read_bytes(), original)
            with mock.patch.object(config, 'atomic_write', wraps=config.atomic_write) as writer:
                apply_agent('pi', directory, payload(), backup=False)
                writer.assert_not_called()

    def test_pi_restore_preserves_refreshed_and_new_accounts_and_user_settings(self):
        directory = self.root / 'pi-restore'
        config.atomic_write(directory / 'auth.json', b'{"openai-codex":{"type":"oauth","refresh":"old"}}')
        config.atomic_write(directory / 'settings.json', b'{"theme":"old"}')
        backup = apply_agent('pi', directory, payload(), PASSWORD)
        auth = json.loads((directory / 'auth.json').read_bytes())
        auth['openai-codex']['refresh'] = 'rotated'
        auth['new-account'] = {'type': 'oauth', 'refresh': 'new'}
        config.atomic_write(directory / 'auth.json', json.dumps(auth).encode())
        settings = {'theme': 'new', 'defaultProvider': 'new-account', 'defaultModel': 'new-model',
                    'enabledModels': ['new-account/*', PI_PROVIDER + '/claude-fixture-one']}
        config.atomic_write(directory / 'settings.json', json.dumps(settings).encode())
        models = json.loads((directory / 'models.json').read_bytes())
        models['providers']['new-provider'] = {'models': [{'id': 'new'}]}
        config.atomic_write(directory / 'models.json', json.dumps(models).encode())
        undo = restore_agent('pi', directory, backup.read_bytes(), PASSWORD)
        restored_auth = json.loads((directory / 'auth.json').read_bytes())
        self.assertEqual(restored_auth, {key: value for key, value in auth.items() if key != PI_PROVIDER})
        settings['enabledModels'] = ['new-account/*']
        self.assertEqual(json.loads((directory / 'settings.json').read_bytes()), settings)
        self.assertEqual(json.loads((directory / 'models.json').read_bytes())['providers'],
                         {'new-provider': models['providers']['new-provider']})
        restore_agent('pi', directory, undo.read_bytes(), PASSWORD)
        self.assertEqual(json.loads((directory / 'auth.json').read_bytes()), auth)

    def test_pi_does_not_overwrite_an_oauth_using_the_reserved_provider(self):
        directory = self.root / 'pi-conflict'
        config.atomic_write(directory / 'auth.json', json.dumps({PI_PROVIDER: {'type': 'oauth'}}).encode())
        before = config._snapshot(directory, AGENTS['pi'].files)
        with self.assertRaisesRegex(ConfigError, '未覆盖原凭据'):
            apply_agent('pi', directory, payload(), backup=False)
        self.assertEqual(config._snapshot(directory, AGENTS['pi'].files), before)

    def test_pi_native_locks_prevent_import_during_login_or_settings_updates(self):
        directory = self.root / 'pi-locked'
        apply_agent('pi', directory, payload(), backup=False)
        before = config._snapshot(directory, AGENTS['pi'].files)
        for filename in ('auth.json', 'settings.json'):
            lock = directory / (filename + '.lock')
            lock.mkdir()
            with self.assertRaisesRegex(ConfigError, 'pi 正在更新'):
                apply_agent('pi', directory, payload('changed'), backup=False)
            self.assertEqual(config._snapshot(directory, AGENTS['pi'].files), before)
            self.assertTrue(lock.is_dir())
            lock.rmdir()
            self.assertFalse(any(directory.glob('*.lock')))

    def test_pi_restore_does_not_resurrect_logged_out_accounts(self):
        directory = self.root / 'pi-logout'
        config.atomic_write(directory / 'auth.json', b'{"openai-codex":{"type":"oauth","refresh":"old"}}')
        backup = apply_agent('pi', directory, payload(), PASSWORD)
        (directory / 'auth.json').unlink()
        restore_agent('pi', directory, backup.read_bytes(), PASSWORD)
        result = directory / 'auth.json'
        self.assertTrue(not result.exists() or json.loads(result.read_bytes()) == {})

    def test_claude_code_keeps_existing_model_choices_and_picker_settings(self):
        original = {'availableModels': ['opus', 'existing-custom'], 'modelPicker': {'keep': True},
                    'availableModelsMatch': 'prefix', 'permissions': {'allow': ['Read']}}
        rendered = render_agent('claude-code', {'settings.json': {'data': json.dumps(original).encode()}},
                                payload(), self.root)
        result = json.loads(rendered['settings.json'])
        self.assertEqual(result['availableModels'], ['opus', 'existing-custom', 'claude-fixture-one', 'claude-fixture-two'])
        for key in ('modelPicker', 'permissions', 'availableModelsMatch'):
            self.assertEqual(result[key], original[key])

    def test_mixed_import_writes_compatible_models_and_backup_restores_original(self):
        from cliproxy_config.agents import CLAUDE_PROFILE
        rows = [model_row('gpt-image-fixture'), model_row('gpt-vision', input_modalities=['text', 'image']),
                model_row('claude-vision', input_modalities=['text', 'image']),
                model_row('text-embedding-fixture'), model_row('protocol-mismatch', supported_endpoints=['/v1/audio/speech'])]
        mixed = make_connection('https://fixture.invalid/v1', 'key', rows, rows[0]['id'])
        for agent, definition in AGENTS.items():
            with self.subTest(agent=agent):
                directory = self.root / agent
                first = definition.files[0]
                original = b'# original\n' if agent in ('codex', 'hermes') else b'{"keep":true}\n'
                config.atomic_write(directory / first, original)
                before = config._snapshot(directory, definition.files)
                backup = apply_agent(agent, directory, mixed, PASSWORD)
                after = config._snapshot(directory, definition.files)
                combined = b'\n'.join(row['data'] for row in after.values())
                for excluded in (b'gpt-image-fixture', b'text-embedding-fixture', b'protocol-mismatch'):
                    self.assertNotIn(excluded, combined)
                self.assertIn(b'claude-vision', combined)
                if agent == 'claude':
                    profile = json.loads(after[CLAUDE_PROFILE]['data'])
                    self.assertEqual([r['name'] for r in profile['inferenceModels']], ['claude-vision'])
                else:
                    self.assertIn(b'gpt-vision', combined)
                if agent == 'pi':
                    models = json.loads(after['models.json']['data'])['providers'][PI_PROVIDER]['models']
                    self.assertEqual(models[0]['input'], ['text', 'image'])
                    self.assertTrue(models[0]['reasoning'])
                    self.assertEqual(models[0]['contextWindow'], 272000)
                restore_agent(agent, directory, backup.read_bytes(), PASSWORD)
                self.assertEqual(config._snapshot(directory, definition.files), before)

    def test_preview_redacts_key_and_rejects_non_chat_models(self):
        for agent in AGENTS:
            preview = preview_agent(agent, self.root, payload())
            self.assertNotIn('fixture-secret', preview)
        bad = make_connection('https://fixture.invalid/v1', 'key',
                              [model_row('gpt-image-test')], 'gpt-image-test')
        for agent in AGENTS:
            with self.subTest(agent=agent), self.assertRaises(ConfigError):
                apply_agent(agent, self.root / agent, bad, PASSWORD)
            self.assertFalse((self.root / agent).exists())

    def test_bad_config_and_wrong_backup_password_never_write(self):
        for agent in ('claude-code', 'pi', 'hermes'):
            directory = self.root / agent
            definition = AGENTS[agent]
            first = definition.files[0]
            config.atomic_write(directory / first, b'[broken')
            before = config._snapshot(directory, definition.files)
            with self.assertRaises(ConfigError):
                apply_agent(agent, directory, payload(), PASSWORD)
            self.assertEqual(config._snapshot(directory, definition.files), before)
            (directory / first).unlink()
            backup = apply_agent(agent, directory, payload(), PASSWORD)
            before = config._snapshot(directory, definition.files)
            with self.assertRaises(ConfigError):
                restore_agent(agent, directory, backup.read_bytes(), 'wrong-password')
            self.assertEqual(config._snapshot(directory, definition.files), before)
            other = self.root / 'elsewhere'
            with self.assertRaises(ConfigError):
                restore_agent(agent, other, backup.read_bytes(), PASSWORD)
            self.assertFalse(other.exists())

    def test_malformed_native_backups_never_overwrite_working_configuration(self):
        from cliproxy_config.crypto import decode_document, encode_document
        import base64
        for agent in ('claude', 'claude-code', 'pi', 'hermes'):
            with self.subTest(agent=agent):
                directory = self.root / ('corrupt-backup-' + agent)
                backup = apply_agent(agent, directory, payload(), backup_path=self.root / (agent + '.cproxybackup'))
                before = config._snapshot(directory, AGENTS[agent].files)
                document = decode_document(backup.read_bytes())
                first = AGENTS[agent].files[0]
                document['files'][first] = {'data': base64.b64encode(b'[broken').decode(), 'mode': 0o600}
                with self.assertRaises(ConfigError):
                    restore_agent(agent, directory, encode_document(document).encode())
                self.assertEqual(config._snapshot(directory, AGENTS[agent].files), before)

    def test_pi_mid_write_failure_rolls_back_all_files(self):
        directory = self.root / 'pi'
        for name in AGENTS['pi'].files:
            config.atomic_write(directory / name, b'{"keep": true}')
        before = config._snapshot(directory, AGENTS['pi'].files)
        writer = config.atomic_write
        failed = False
        def faulty(path, content, mode=0o600):
            nonlocal failed
            if path.name == 'settings.json' and not failed:
                failed = True
                raise OSError('fixture')
            return writer(path, content, mode)
        with mock.patch.object(config, 'atomic_write', side_effect=faulty):
            with self.assertRaisesRegex(ConfigError, '自动撤回'):
                apply_agent('pi', directory, payload(), PASSWORD)
        self.assertEqual(config._snapshot(directory, AGENTS['pi'].files), before)

    def test_rejects_wrong_agent_backup_and_respects_directory_environment(self):
        directory = self.root / 'agent'
        backup = apply_agent('pi', directory, payload(), PASSWORD)
        with self.assertRaises(ConfigError):
            restore_agent('claude', directory, backup.read_bytes(), PASSWORD)
        for agent, variable in [('codex', 'CODEX_HOME'), ('pi', 'PI_CODING_AGENT_DIR'),
                                ('hermes', 'HERMES_HOME'), ('claude-code', 'CLAUDE_CONFIG_DIR')]:
            self.assertEqual(default_directory(agent, self.root, {variable: str(directory)}), directory)
            with self.assertRaises(ConfigError):
                default_directory(agent, self.root, {variable: 'relative'})

    def test_existing_new_thread_default_and_legacy_hermes_model_are_migrated(self):
        original = b'[models.new_thread]\nmodel="old"\nreasoning_effort="high"\n'
        rendered = render_agent('codex', {'config.toml': {'data': original}}, payload(), self.root)
        import tomllib
        document = tomllib.loads(rendered['config.toml'].decode())
        self.assertEqual(document['models']['new_thread']['model'], 'claude-fixture-two')
        self.assertNotIn('reasoning_effort', document['models']['new_thread'])
        original = b'# legacy\nmodel: old-model\nterminal:\n  backend: local\n'
        rendered = render_agent('hermes', {'config.yaml': {'data': original}}, payload(), self.root)
        document = YAML().load(rendered['config.yaml'])
        self.assertEqual(document['model']['provider'], 'custom:cliproxyapi')
        self.assertEqual(document['terminal']['backend'], 'local')

class BackupOptionsTests(unittest.TestCase):
    setUp = AgentAdapterTests.setUp
    tearDown = AgentAdapterTests.tearDown
    def test_optional_backup_and_custom_destinations_roundtrip(self):
        from cliproxy_config.crypto import decode_document
        for agent, definition in AGENTS.items():
            for encrypted in (True,False):
                with self.subTest(agent=agent,encrypted=encrypted):
                    directory=self.root/(agent+str(encrypted))
                    first=apply_agent(agent,directory,payload(),backup=False)
                    self.assertIsNone(first)
                    self.assertFalse((directory/'cliproxy-config-backups').exists())
                    original=config._snapshot(directory,definition.files)
                    destination=self.root/'chosen backup folder'/f'{agent}-{encrypted}.cproxybackup'
                    password=PASSWORD if encrypted else None
                    backup=apply_agent(agent,directory,payload('new-key'),password,backup_path=destination)
                    self.assertEqual(backup,destination)
                    self.assertEqual(decode_document(backup.read_bytes(),password)['directory'],str(directory))
                    undo=self.root/'undo'/destination.name
                    restore_agent(agent,directory,backup.read_bytes(),password,backup_path=undo)
                    self.assertTrue(undo.is_file())
                    self.assertEqual(config._snapshot(directory,definition.files),original)

    def test_no_backup_still_rolls_back_failed_writes(self):
        directory=self.root/'pi-no-backup'
        apply_agent('pi',directory,payload(),backup=False)
        before=config._snapshot(directory,AGENTS['pi'].files)
        writer=config.atomic_write
        def faulty(path,content,mode=0o600):
            if path.name=='settings.json' and b'changed-key' in content:
                raise OSError('fixture')
            if path.name=='settings.json' and b'"defaultModel"' in content:
                raise OSError('fixture')
            return writer(path,content,mode)
        with mock.patch.object(config,'atomic_write',side_effect=faulty):
            with self.assertRaisesRegex(ConfigError,'自动撤回'):
                changed = payload('changed-key')
                changed['catalog']['models'] = changed['catalog']['models'][:1]
                changed['model'] = 'claude-fixture-one'
                apply_agent('pi',directory,changed,backup=False)
        self.assertEqual(config._snapshot(directory,AGENTS['pi'].files),before)
        self.assertFalse((directory/'cliproxy-config-backups').exists())

    def test_backup_cannot_overwrite_agent_configuration(self):
        for agent,definition in AGENTS.items():
            directory=self.root/agent
            target=directory/definition.files[0]
            with self.subTest(agent=agent),self.assertRaisesRegex(ConfigError,'不能覆盖'):
                apply_agent(agent,directory,payload(),PASSWORD,backup_path=target)
            self.assertFalse(target.exists())

    def test_codex_backup_cannot_overwrite_existing_account_credentials(self):
        directory = self.root / 'codex'
        directory.mkdir()
        auth = directory / 'auth.json'
        auth.write_bytes(b'{"fixture":"existing-account"}')
        with self.assertRaisesRegex(ConfigError, '不能覆盖'):
            apply_agent('codex', directory, payload(), PASSWORD, backup_path=auth)
        self.assertEqual(auth.read_bytes(), b'{"fixture":"existing-account"}')
        self.assertFalse((directory / 'config.toml').exists())

    def test_desktop_native_profile_and_other_profiles_are_preserved(self):
        from cliproxy_config.agents import CLAUDE_PROFILE,CLAUDE_PROFILE_ID
        directory=self.root/'desktop'
        config.atomic_write(directory/'configLibrary/_meta.json',b'{"entries":[{"id":"keep","name":"Other"}],"appliedId":"keep"}')
        config.atomic_write(directory/'claude_desktop_config.json',b'{"deploymentMode":"1p","mcpServers":{"keep":{}}}')
        apply_agent('claude',directory,payload(),backup=False)
        native=json.loads((directory/CLAUDE_PROFILE).read_bytes())
        self.assertEqual(native['inferenceProvider'],'gateway')
        self.assertEqual(native['inferenceGatewayBaseUrl'],'https://fixture.invalid')
        self.assertEqual(native['inferenceGatewayApiKey'],'fixture-secret')
        self.assertEqual(native['inferenceModels'][0]['name'],'claude-fixture-two')
        self.assertTrue(native['chatTabEnabled'])
        self.assertTrue(native['isClaudeCodeForDesktopEnabled'])
        self.assertFalse(native['modelDiscoveryEnabled'])
        meta=json.loads((directory/'configLibrary/_meta.json').read_bytes())
        self.assertEqual(meta['appliedId'],CLAUDE_PROFILE_ID)
        self.assertEqual(meta['entries'][0]['id'],'keep')
        root=json.loads((directory/'claude_desktop_config.json').read_bytes())
        self.assertEqual(root['deploymentMode'],'3p')
        self.assertIn('keep',root['mcpServers'])
        apply_agent('claude',directory,payload(),backup=False)
        self.assertEqual(len(json.loads((directory/'configLibrary/_meta.json').read_bytes())['entries']),2)

    def test_desktop_rejects_non_claude_models_and_remote_configuration(self):
        mixed=make_connection('https://fixture.invalid/v1','key',[model_row('gpt-fixture')],'gpt-fixture')
        with self.assertRaisesRegex(ConfigError,'仅接受 Claude'):
            apply_agent('claude',self.root/'desktop',mixed,backup=False)
        directory=self.root/'managed-desktop'
        config.atomic_write(directory/'configLibrary/_meta.json',b'{"hybridPointer":"https://managed.invalid/config"}')
        before=config._snapshot(directory,AGENTS['claude'].files)
        with self.assertRaisesRegex(ConfigError,'托管配置'):
            apply_agent('claude',directory,payload(),backup=False)
        self.assertEqual(config._snapshot(directory,AGENTS['claude'].files),before)

    def test_legacy_claude_cli_backups_can_still_be_restored(self):
        from cliproxy_config.crypto import encode_document,decode_document
        directory=self.root/'legacy-claude'
        backup=apply_agent('claude-code',directory,payload(),PASSWORD)
        legacy=decode_document(backup.read_bytes(),PASSWORD)
        legacy['agent']='claude'
        restore_agent('claude-code',directory,encode_document(legacy,PASSWORD).encode(),PASSWORD)
        self.assertFalse((directory/'settings.json').exists())


if __name__ == '__main__':
    unittest.main()
