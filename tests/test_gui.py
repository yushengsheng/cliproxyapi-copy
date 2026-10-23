from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from cliproxy_config.agents import AGENTS
from cliproxy_config.crypto import ConfigError, decrypt, encrypt
from cliproxy_config.gateway import Gateway, make_connection
from cliproxy_config.gui import App
from test_config import PASSWORD
from model_fixtures import model_row


class DesktopFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = App(preferences_path=self.root / 'preferences.json')
        self.app.withdraw()
        mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename', side_effect=lambda **kw: str(self.root / kw.get('initialfile','backup.cproxybackup'))).start()
        self.error = mock.patch('cliproxy_config.gui.messagebox.showerror').start()
        self.gateway = Gateway('https://fixture.invalid/v1', ('fixture-key',),
            (model_row('claude-first'), model_row('claude-second')))

    def tearDown(self):
        self.app.destroy()
        mock.patch.stopall()
        self.temp.cleanup()

    def complete(self):
        deadline = time.monotonic() + 10
        while self.app.busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.app.update()
        self.assertFalse(self.app.busy)

    def fetch(self):
        self.app.url.set('https://fixture.invalid/management.html#/')
        with mock.patch('cliproxy_config.gui.read_browser', return_value=self.gateway):
            self.app._fetch()
            self.complete()

    def test_reads_all_models_then_only_exports_checked_models(self):
        self.fetch()
        self.assertEqual(len(self.app.tree.get_children()), 2)
        self.assertFalse(self.app.selected)
        self.app._toggle('1')
        self.assertEqual(self.app.model.get(), 'claude-second')
        self.app.search.set('claude-first')
        self.assertEqual(self.app.selected, {'claude-second'})
        self.assertEqual([r['slug'] for r in self.app._payload()['catalog']['models']], ['claude-second'])
        self.app._select_all(True)
        self.assertEqual(self.app.selected, {'claude-first', 'claude-second'})
        self.app._toggle('1')
        self.assertEqual(self.app.model.get(), 'claude-first')
        self.app.password.set(PASSWORD)
        self.app.confirm.set(PASSWORD)
        self.app.directory.set(str(self.root / 'client'))
        export = self.root / 'selected.cproxy'
        with mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename', return_value=str(export)):
            self.app._export()
            self.complete()
        self.assertEqual([r['slug'] for r in decrypt(export.read_bytes(), PASSWORD)['catalog']['models']], ['claude-first'])
        self.error.assert_not_called()

    def test_browser_address_and_failed_refresh_clear_old_credentials(self):
        self.fetch()
        self.app._select_all(True)
        self.app.browser.set('Chrome')
        self.assertFalse(hasattr(self.app, 'profile'))
        self.assertIsNone(self.app.gateway)
        self.assertFalse(self.app.selected)
        self.fetch()
        with mock.patch('cliproxy_config.gui.read_browser', side_effect=ConfigError('fixture')):
            self.app._fetch()
            self.complete()
        self.assertIsNone(self.app.gateway)
        self.assertEqual(self.app.key_choice.get(), '')
        self.assertFalse(self.app.keys.cget('values'))

    def test_four_agents_can_apply_to_temp_directories_and_preview_hides_keys(self):
        self.fetch()
        self.app._select_all(True)
        for agent, definition in AGENTS.items():
            with self.subTest(agent=agent):
                directory = self.root / agent
                self.app.agent.set(definition.label)
                self.app.directory.set(str(directory))
                self.app._preview()
                self.assertNotIn('fixture-key', self.app.preview.get('1.0', 'end'))
                self.app.password.set(PASSWORD)
                self.app.confirm.set(PASSWORD)
                self.app._apply()
                self.complete()
                self.assertTrue(all((directory / name).is_file() for name in definition.files))
        self.error.assert_not_called()

    def test_encrypted_import_preserves_capabilities_and_connection_options(self):
        rows = [model_row('custom', context=1000000, tool_mode='direct', use_responses_lite=False)]
        payload = make_connection('https://fixture.invalid/v1', 'fixture-key', rows, 'custom')
        payload['model_settings'].update(model_reasoning_effort='high', model_auto_compact_token_limit=800000)
        payload['provider_settings']['stream_idle_timeout_ms'] = 300000
        source = self.root / 'capabilities.cproxy'
        source.write_text(encrypt(payload, PASSWORD), encoding='utf-8')
        with mock.patch('cliproxy_config.gui.filedialog.askopenfilename', return_value=str(source)), \
             mock.patch('cliproxy_config.gui.simpledialog.askstring', return_value=PASSWORD):
            self.app._import_file()
            self.complete()
        self.assertEqual(self.app._payload(), payload)
        with mock.patch('cliproxy_config.gui.fetch_models') as fetch:
            self.app._key_changed()
        fetch.assert_not_called()
        self.assertEqual(self.app._payload(), payload)
        self.app._preview()
        self.assertNotIn('fixture-key', self.app.preview.get('1.0', 'end'))
        self.app._source_changed()
        self.assertIsNone(self.app.imported_payload)
        self.error.assert_not_called()

    def test_default_window_fits_all_controls(self):
        self.app.update_idletasks()
        self.assertLessEqual(self.app.winfo_reqheight(), self.app.minsize()[1])
        self.assertLessEqual(self.app.winfo_reqwidth(), self.app.minsize()[0])

class UnifiedDesktopFlowTests(unittest.TestCase):
    setUp = DesktopFlowTests.setUp
    tearDown = DesktopFlowTests.tearDown
    complete = DesktopFlowTests.complete
    fetch = DesktopFlowTests.fetch
    def test_plain_text_paste_and_file_drop_preserve_model_metadata(self):
        import json
        from types import SimpleNamespace
        from cliproxy_config.crypto import encode_document
        payload=make_connection(self.gateway.base_url,'fixture-key',list(self.gateway.models),'claude-second')
        self.app._import()
        with mock.patch.object(self.app,'clipboard_get',return_value=encode_document(payload)):
            self.app._paste_import()
        self.app._import_content(self.app.import_text.get('1.0','end'))
        self.complete()
        self.assertEqual(self.app._payload(),payload)
        source=self.root/'含 空格的节点.json'
        source.write_text(encode_document(payload),encoding='utf-8')
        self.app._select_all(False)
        self.app._drop_files(SimpleNamespace(data=self.app.tk.call('list',str(source))))
        self.complete()
        self.assertEqual(self.app.selected,{'claude-first','claude-second'})
        self.assertEqual(self.app._payload(),payload)
        self.error.assert_not_called()

    def test_invalid_or_cancelled_import_keeps_existing_selection(self):
        self.fetch();self.app._select_all(True)
        original=self.app._payload()
        self.app._import_content('{broken')
        self.assertEqual(self.app._payload(),original)
        self.error.reset_mock()
        with mock.patch('cliproxy_config.gui.simpledialog.askstring',return_value=None):
            self.app._import_content(encrypt(original,PASSWORD))
        self.assertEqual(self.app._payload(),original)
        self.assertFalse(self.app.busy)
        with mock.patch('cliproxy_config.gui.simpledialog.askstring',return_value='wrong-password'):
            self.app._import_content(encrypt(original,PASSWORD));self.complete()
        self.assertEqual(self.app._payload(),original)

    def test_plain_export_and_no_backup_import_do_not_ask_for_password(self):
        from cliproxy_config.crypto import decode_document
        self.fetch();self.app._select_all(True)
        self.app.export_encrypted.set(False)
        destination=self.root/'chosen exports'/'models.json'
        with mock.patch.object(self.app,'_password',side_effect=AssertionError('must not ask')), \
             mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename',return_value=str(destination)):
            self.app._export();self.complete()
        self.assertEqual(decode_document(destination.read_bytes()),self.app._payload())
        self.app.backup_enabled.set(False)
        directory=self.root/'no-backup-client';self.app.directory.set(str(directory))
        with mock.patch.object(self.app,'_password',side_effect=AssertionError('must not ask')), \
             mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename',side_effect=AssertionError('no backup dialog')):
            self.app._apply();self.complete()
        self.assertTrue((directory/'config.toml').is_file())
        self.assertFalse((directory/'cliproxy-config-backups').exists())
        self.error.assert_not_called()

    def test_remembers_both_paths_across_restart_without_saving_keys(self):
        import json
        self.fetch();self.app._select_all(True)
        self.app.export_encrypted.set(False);self.app.backup_encrypted.set(False)
        exported=self.root/'export dir'/'models.json'
        backup=self.root/'backup dir'/'before.cproxybackup'
        with mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename',return_value=str(exported)):
            self.app._export();self.complete()
        self.app.directory.set(str(self.root/'client'))
        with mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename',return_value=str(backup)):
            self.app._apply();self.complete()
        saved=(self.root/'preferences.json').read_text(encoding='utf-8')
        self.assertNotIn('fixture-key',saved)
        self.assertNotIn(PASSWORD,saved)
        self.assertNotIn('profile',json.loads(saved))
        restarted=App(preferences_path=self.root/'preferences.json');restarted.withdraw()
        try:
            self.assertEqual(restarted._initial_directory('export'),str(exported.parent))
            self.assertEqual(restarted._initial_directory('backup'),str(backup.parent))
            self.assertFalse(restarted.export_encrypted.get())
            self.assertFalse(restarted.backup_encrypted.get())
        finally:restarted.destroy()
        self.error.assert_not_called()

    def test_cancelled_backup_destination_does_not_write_configuration(self):
        self.fetch();self.app._select_all(True)
        self.app.backup_encrypted.set(False)
        target=self.root/'cancelled-client';self.app.directory.set(str(target))
        with mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename',return_value=''):
            self.app._apply()
        self.assertFalse(target.exists())
        self.assertFalse(self.app.busy)
        self.error.assert_not_called()


    def test_malformed_legacy_backup_does_not_write_or_prompt_for_saving(self):
        import json
        self.app.agent.set(AGENTS['claude-code'].label)
        target=self.root/'invalid-backup-target';self.app.directory.set(str(target))
        source=self.root/'broken.cproxybackup'
        source.write_text(json.dumps({'kind':'backup','version':1,'agent':'claude',
            'directory':str(target),'files':[{}]}),encoding='utf-8')
        with mock.patch('cliproxy_config.gui.filedialog.askopenfilename',return_value=str(source)), \
             mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename',side_effect=AssertionError('must not save')):
            self.app._restore()
        self.assertFalse(target.exists())
        self.error.assert_called_once()


class CompatibleImportFlowTests(unittest.TestCase):
    setUp = DesktopFlowTests.setUp
    tearDown = DesktopFlowTests.tearDown
    complete = DesktopFlowTests.complete
    fetch = DesktopFlowTests.fetch

    def mixed_selection(self):
        rows = (model_row('gpt-image-fixture'), model_row('gpt-vision', input_modalities=['text', 'image']),
                model_row('claude-vision'), model_row('text-embedding-fixture'))
        self.gateway = Gateway(self.gateway.base_url, self.gateway.keys, rows)
        self.fetch()
        self.app._select_all(True)
        self.app.agent.set(AGENTS['claude'].label)
        self.app.directory.set(str(self.root / 'claude-client'))

    def test_all_selected_import_reports_each_model_and_exports_keep_full_selection(self):
        import json
        from cliproxy_config.agents import CLAUDE_PROFILE
        from cliproxy_config.crypto import decode_document
        self.mixed_selection()
        selected = set(self.app.selected)
        self.app._preview()
        preview = self.app.preview.get('1.0', 'end')
        self.assertIn('可导入 1 个，跳过 3 个', preview)
        self.assertIn('Images API', preview)
        self.assertIn('改用 claude-vision', preview)
        self.assertNotIn('fixture-key', preview)
        self.app.backup_enabled.set(False)
        with mock.patch('cliproxy_config.gateway._request', side_effect=AssertionError('no paid requests')):
            self.app._apply()
            self.complete()
        self.assertEqual(self.app.last_import_outcome, 'success')
        self.assertEqual(self.app.model.get(), 'claude-vision')
        self.assertEqual(self.app.selected, selected)
        records = {values[0]: values[1:] for item in self.app.result_tree.get_children()
                   for values in [self.app.result_tree.item(item, 'values')]}
        self.assertEqual(records['claude-vision'][0], '已导入')
        self.assertIn('未进行联网调用', records['claude-vision'][1])
        self.assertEqual(records['gpt-image-fixture'][0], '已跳过')
        self.assertIn('Images API', records['gpt-image-fixture'][1])
        self.assertIn('仅接受 Claude', records['gpt-vision'][1])
        self.assertIn('向量', records['text-embedding-fixture'][1])
        self.assertIn('已改用 claude-vision', self.app.result_summary.get())
        profile = json.loads((self.root / 'claude-client' / CLAUDE_PROFILE).read_bytes())
        self.assertEqual([r['name'] for r in profile['inferenceModels']], ['claude-vision'])
        for encrypted in (False, True):
            self.app.export_encrypted.set(encrypted)
            self.app.password.set(PASSWORD)
            self.app.confirm.set(PASSWORD)
            destination = self.root / ('all.cproxy' if encrypted else 'all.json')
            with mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename', return_value=str(destination)):
                self.app._export()
                self.complete()
            payload = decode_document(destination.read_bytes(), PASSWORD if encrypted else None)
            self.assertEqual({row['slug'] for row in payload['catalog']['models']}, selected)
        self.error.assert_not_called()

    def test_all_incompatible_skip_password_backup_and_configuration_writes(self):
        self.gateway = Gateway(self.gateway.base_url, self.gateway.keys, (model_row('gpt-image-fixture'),))
        self.fetch()
        self.app._select_all(True)
        target = self.root / 'must-not-exist'
        self.app.directory.set(str(target))
        with mock.patch.object(self.app, '_password', side_effect=AssertionError('no password')), \
             mock.patch.object(self.app, '_backup_location', side_effect=AssertionError('no backup')), \
             mock.patch('cliproxy_config.gui.apply_agent', side_effect=AssertionError('no writes')), \
             mock.patch('cliproxy_config.gui.test_model', side_effect=AssertionError('no paid request')):
            self.app._apply()
            self.app._test()
        self.assertEqual(self.app.last_import_outcome, 'empty')
        self.assertIn('未修改任何配置', self.app.result_summary.get())
        self.assertFalse(target.exists())
        self.assertFalse(self.app.busy)
        self.error.assert_not_called()

    def test_missing_codex_catalog_is_allowed_for_claude_then_reported_for_codex(self):
        self.gateway = Gateway(self.gateway.base_url, self.gateway.keys,
                               ({'id': 'claude-custom', 'owned_by': 'fixture'},))
        self.fetch()
        self.app._select_all(True)
        self.app.backup_enabled.set(False)
        self.app.agent.set(AGENTS['claude'].label)
        self.app.directory.set(str(self.root / 'claude'))
        self.app._apply()
        self.complete()
        self.assertEqual(self.app.last_import_outcome, 'success')
        self.app.agent.set(AGENTS['codex'].label)
        target = self.root / 'codex'
        self.app.directory.set(str(target))
        self.app._apply()
        self.assertEqual(self.app.last_import_outcome, 'empty')
        self.assertIn('原生上下文', self.app.last_import_plan.skipped[0].reason)
        self.assertFalse(target.exists())

    def test_incompatible_default_is_never_silently_substituted_for_a_paid_probe(self):
        self.mixed_selection()
        with mock.patch('cliproxy_config.gui.test_model') as probe:
            self.app._test()
        probe.assert_not_called()
        self.assertEqual(self.app.model.get(), 'gpt-image-fixture')
        self.error.assert_called_once()
        self.assertIn('请先选择', self.app.status.get())

    def test_write_failure_reports_no_success_and_does_not_change_default(self):
        self.mixed_selection()
        self.app.backup_enabled.set(False)
        with mock.patch('cliproxy_config.gui.apply_agent', side_effect=ConfigError('模拟写入失败')):
            self.app._apply()
            self.complete()
        self.assertEqual(self.app.last_import_outcome, 'failed')
        self.assertEqual(self.app.model.get(), 'gpt-image-fixture')
        records = [self.app.result_tree.item(item, 'values') for item in self.app.result_tree.get_children()]
        self.assertNotIn('已导入', [row[1] for row in records])
        self.assertEqual(records[0][1:], ('未导入', '模拟写入失败'))
        self.assertFalse((self.root / 'claude-client').exists())

    def test_result_details_and_close_button_remain_visible_at_minimum_size(self):
        from cliproxy_config.agents import prepare_import
        self.mixed_selection()
        plan = prepare_import('claude', self.app._payload())
        self.app._show_import_result('claude', plan)
        self.app.result_window.geometry('760x400')
        self.app.update()
        window = self.app.result_window
        button = self.app.result_close
        bottom = button.winfo_rooty() - window.winfo_rooty() + button.winfo_height()
        self.assertTrue(button.winfo_ismapped())
        self.assertLessEqual(bottom, window.winfo_height())
        self.assertGreater(self.app.result_tree.winfo_height(), 30)
        button.invoke()
        self.assertEqual(window.state(), 'withdrawn')


class WorkflowStabilityTests(unittest.TestCase):
    setUp = DesktopFlowTests.setUp
    tearDown = DesktopFlowTests.tearDown
    complete = DesktopFlowTests.complete
    fetch = DesktopFlowTests.fetch

    def test_progress_and_dialog_controls_recover_when_a_running_dialog_is_closed(self):
        import threading
        ready = threading.Event()
        self.app._import()
        buttons = [widget for widget in self.app.widgets if str(widget).startswith(str(self.app.import_window))]
        self.assertEqual(len(buttons), 4)
        try:
            self.app._run(lambda: ready.wait(5), lambda _: self.app.status.set('完成'), '处理中')
            self.assertTrue(self.app.busy)
            self.assertEqual(self.app.progress.winfo_manager(), 'pack')
            self.assertTrue(all(str(widget.cget('state')) == 'disabled' for widget in buttons))
            self.app.import_window.destroy()
        finally:
            ready.set()
        self.complete()
        self.assertEqual(self.app.progress.winfo_manager(), '')
        self.assertEqual(self.app.status.get(), '完成')
        self.app._run(lambda: True, lambda _: self.app.status.set('再次完成'), '再次运行')
        self.complete()
        self.assertEqual(self.app.status.get(), '再次完成')
        self.assertTrue(all(widget.winfo_exists() for widget in self.app.widgets))
        self.error.assert_not_called()

    def test_worker_failure_stops_progress_and_restores_the_controls(self):
        def fail():
            raise ConfigError('模拟失败')
        self.app._run(fail, lambda _: self.fail('must not succeed'), '处理中')
        self.complete()
        self.assertEqual(self.app.progress.winfo_manager(), '')
        self.assertTrue(all(str(widget.cget('state')) != 'disabled' for widget in self.app.widgets))
        self.error.assert_called_once()

    def test_export_does_not_overwrite_codex_account_credentials(self):
        self.fetch()
        self.app._select_all(True)
        self.app.export_encrypted.set(False)
        target = self.root / 'client'
        target.mkdir()
        self.app.directory.set(str(target))
        auth = target / 'auth.json'
        auth.write_bytes(b'{"fixture":"existing-account"}')
        with mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename', return_value=str(auth)):
            self.app._export()
        self.assertEqual(auth.read_bytes(), b'{"fixture":"existing-account"}')
        self.assertFalse(self.app.busy)
        self.error.assert_called_once()

    def test_corrupted_export_filename_preference_falls_back_to_default(self):
        self.fetch()
        self.app._select_all(True)
        self.app.export_encrypted.set(False)
        self.app.preferences['export_filename'] = {'invalid': True}
        with mock.patch('cliproxy_config.gui.filedialog.asksaveasfilename', return_value='') as choose:
            self.app._export()
        self.assertEqual(choose.call_args.kwargs['initialfile'], '模型节点.json')
        self.error.assert_not_called()

    def test_invalid_codex_home_does_not_prevent_opening_the_window(self):
        with mock.patch('cliproxy_config.gui.default_directory', side_effect=ConfigError('CODEX_HOME 无效')):
            app = App(preferences_path=self.root / 'invalid-home-prefs.json')
        try:
            app.withdraw()
            app.update_idletasks()
            self.assertEqual(app.directory.get(), '')
            self.assertIn('手动选择配置目录', app.status.get())
        finally:
            app.destroy()
        self.error.assert_not_called()


class PermissionFreeBrowserTests(unittest.TestCase):
    setUp = DesktopFlowTests.setUp
    tearDown = DesktopFlowTests.tearDown
    complete = DesktopFlowTests.complete

    def test_legacy_browser_preference_defaults_to_builtin_without_losing_paths(self):
        import json
        for browser in ('Brave', 'Chrome'):
            with self.subTest(browser=browser):
                self.app.destroy()
                path=self.root/'preferences.json'
                path.write_text(json.dumps({'browser':browser,'url':'https://fixture.invalid',
                    'backup_directory':str(self.root),'export_directory':str(self.root)}),encoding='utf-8')
                self.app=App(preferences_path=path);self.app.withdraw()
                self.assertEqual(self.app.browser.get(),'内置登录窗口')
                self.assertEqual(self.app._initial_directory('backup'),str(self.root))
                self.assertEqual(self.app._initial_directory('export'),str(self.root))
                self.app.browser.set(browser)
                self.assertTrue(self.app._save_preferences())
                self.app.destroy()
                self.app=App(preferences_path=path);self.app.withdraw()
                self.assertEqual(self.app.browser.get(),browser)
        self.error.assert_not_called()

    def test_permission_denied_opens_builtin_without_an_error_dialog_and_remembers_it(self):
        import json
        from cliproxy_config.browser import BrowserUnavailable
        self.app.url.set('https://fixture.invalid')
        self.app.browser.set('Chrome')
        with mock.patch('cliproxy_config.gui.read_browser',side_effect=[BrowserUnavailable('denied'),self.gateway]) as reader:
            self.app._fetch();self.complete()
        self.assertEqual(reader.call_args_list,[mock.call('https://fixture.invalid','Chrome'),
                                               mock.call('https://fixture.invalid','内置登录窗口')])
        self.assertEqual(self.app.browser.get(),'内置登录窗口')
        self.assertIs(self.app.gateway,self.gateway)
        self.assertEqual(len(self.app.tree.get_children()),2)
        self.assertEqual(json.loads((self.root/'preferences.json').read_text(encoding='utf-8'))['browser'],'内置登录窗口')
        self.error.assert_not_called()

    def test_backend_failure_or_cancellation_does_not_open_another_window(self):
        self.app.url.set('https://fixture.invalid')
        self.app.browser.set('Brave')
        for message in ('HTTP 401：客户端密钥无效。','后台标签页已关闭，已取消网页获取。'):
            with self.subTest(message=message),mock.patch('cliproxy_config.gui.read_browser',side_effect=ConfigError(message)) as reader:
                self.app._fetch();self.complete()
                reader.assert_called_once_with('https://fixture.invalid','Brave')
                self.assertIsNone(self.app.gateway)

    def test_builtin_failure_does_not_retry_or_switch_to_external_browser(self):
        from cliproxy_config.browser import BrowserUnavailable
        self.app.url.set('https://fixture.invalid')
        self.assertEqual(self.app.browser.get(),'内置登录窗口')
        with mock.patch('cliproxy_config.gui.read_browser',side_effect=BrowserUnavailable('fixture')) as reader:
            self.app._fetch();self.complete()
        reader.assert_called_once_with('https://fixture.invalid','内置登录窗口')
        self.error.assert_called_once()


if __name__ == '__main__':
    unittest.main()
