from copy import deepcopy
import json
import unittest
from unittest import mock

from cliproxy_config.crypto import ConfigError
from cliproxy_config.gateway import (_NoRedirect, _request, fetch_models, make_connection, management_url, read_browser, test_model)
from model_fixtures import model_row


class BrowserGatewayTests(unittest.TestCase):
    def test_url_validation_keeps_public_proxy_port(self):
        page, base = management_url('https://fixture.invalid:9229/management.html#/')
        self.assertEqual(base, 'https://fixture.invalid:9229/v1')
        for invalid in ('http://fixture.invalid', 'https://key@fixture.invalid',
                        'https://fixture.invalid/path', 'https://fixture.invalid?key=secret'):
            with self.subTest(invalid=invalid), self.assertRaises(ConfigError):
                management_url(invalid)

    def test_native_browsers_read_only_client_keys_without_opencli(self):
        for browser in ('Brave', 'Chrome'):
            with mock.patch('cliproxy_config.browser.read_native', return_value={'keys':['fixture-client-key']}) as reader, \
                 mock.patch('cliproxy_config.gateway.fetch_models', return_value=({'id':'a', 'owned_by':'test'},)) as models:
                result = read_browser('https://fixture.invalid:9229/management.html#/', browser)
            self.assertEqual(result.keys, ('fixture-client-key',))
            self.assertNotIn('fixture-client-key', repr(result))
            reader.assert_called_once_with('https://fixture.invalid:9229/management.html#/config', browser,
                                           'https://fixture.invalid:9229')
            models.assert_called_once_with('https://fixture.invalid:9229/v1', 'fixture-client-key')

    def test_redirect_does_not_forward_credentials(self):
        import urllib.request
        request = urllib.request.Request('https://fixture.invalid/v1/models', headers={'Authorization': 'fixture-key'})
        self.assertIsNone(_NoRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://other.invalid'))

    def test_management_page_subpath_keeps_the_browser_origin_separate(self):
        with mock.patch('cliproxy_config.browser.read_builtin', return_value={'keys': ['key']}) as reader, \
             mock.patch('cliproxy_config.gateway.fetch_models', return_value=()):
            gateway = read_browser('https://fixture.invalid/proxy/management.html#/', '内置登录窗口')
        reader.assert_called_once_with('https://fixture.invalid/proxy/management.html#/config', 'https://fixture.invalid')
        self.assertEqual(gateway.base_url, 'https://fixture.invalid/proxy/v1')

    def test_builtin_window_and_invalid_keys(self):
        with mock.patch('cliproxy_config.browser.read_builtin', return_value={'keys':['key','key']}), \
             mock.patch('cliproxy_config.gateway.fetch_models', return_value=()):
            self.assertEqual(read_browser('https://fixture.invalid','内置登录窗口').keys, ('key',))
        for keys in ([], ['bad\nkey'], [None], ['a']*257):
            with mock.patch('cliproxy_config.browser.read_builtin', return_value={'keys':keys}), self.assertRaises(ConfigError):
                read_browser('https://fixture.invalid','内置登录窗口')
        with self.assertRaises(ConfigError):
            read_browser('https://fixture.invalid','unknown-browser')

    def test_model_catalog_validates_selection_and_defaults(self):
        rows = [model_row('b', 'provider'), model_row('a', 'provider')]
        listing = [{k: row[k] for k in ('id', 'owned_by')} for row in rows]
        with mock.patch('cliproxy_config.gateway._request', side_effect=[
                {'data': listing}, {'models': [r['codex'] for r in rows]}]) as request:
            fetched = fetch_models('https://fixture.invalid/v1', 'key')
            self.assertEqual([r['id'] for r in fetched], ['a', 'b'])
            self.assertEqual(fetched[0]['codex'], rows[1]['codex'])
            self.assertEqual(request.call_args.args[2], '/models?client_version=0.153.0')
        payload = make_connection('https://fixture.invalid/v1', 'key', [rows[0]], 'b')
        self.assertEqual([r['slug'] for r in payload['catalog']['models']], ['b'])
        with self.assertRaises(ConfigError):
            make_connection('https://fixture.invalid/v1', 'key', [rows[0]], 'a')
        self.assertEqual(payload['catalog']['models'][0], rows[0]['codex'])
        self.assertEqual(payload['model_settings']['model_reasoning_effort'], 'medium')
        payload['catalog']['models'][0]['context_window'] = 1
        self.assertEqual(rows[0]['codex']['context_window'], 272000)

    def test_claude_header_is_only_sent_for_messages_and_discovery_keeps_all_families(self):
        fixtures = [model_row('gpt-fixture', 'openai'), model_row('gemini-fixture', 'antigravity'),
                    model_row('claude-fixture', 'anthropic')]
        native_rows = [{k: row[k] for k in ('id', 'owned_by')} for row in fixtures]
        requests = []
        def open_request(request, timeout):
            requests.append(request)
            # Reproduce the gateway behavior that caused the regression: its model
            # listing changes IDs when the Anthropic protocol header is present.
            rows = native_rows if not request.has_header('Anthropic-version') else [
                {'id': 'claude-renamed-' + row['id'], 'owned_by': row['owned_by']} for row in native_rows]
            response = mock.MagicMock()
            body = {'models': [row['codex'] for row in fixtures]} if 'client_version=' in request.full_url else {'data': rows}
            response.__enter__.return_value.read.return_value = json.dumps(body).encode()
            return response
        opener = mock.Mock()
        opener.open.side_effect = open_request
        with mock.patch('cliproxy_config.gateway.urllib.request.build_opener', return_value=opener):
            rows = fetch_models('https://fixture.invalid/v1', 'key')
            self.assertEqual({row['id'] for row in rows}, {row['id'] for row in native_rows})
            for path in ('/responses', '/chat/completions', '/messages'):
                _request('https://fixture.invalid/v1', 'key', path, {'model': 'fixture'})
        self.assertFalse(requests[0].has_header('Content-type'))
        self.assertEqual([request.get_header('Anthropic-version') for request in requests],
                         [None, None, None, None, '2023-06-01'])
        self.assertTrue(all(request.get_header('Authorization') == 'Bearer key' for request in requests))

    def test_real_probe_requires_completed_response_not_merely_model_listing(self):
        payload = make_connection('https://fixture.invalid/v1', 'key', [model_row('model')], 'model')
        with mock.patch('cliproxy_config.gateway._request', return_value={'status': 'incomplete', 'output': []}):
            with self.assertRaises(ConfigError):
                test_model(payload, 'responses')
        with mock.patch('cliproxy_config.gateway._request', return_value={'status': 'completed', 'output': [{'type': 'message'}]}) as request:
            payload['model_settings']['model_reasoning_effort'] = 'none'
            self.assertIn('调用成功', test_model(payload, 'responses'))
            self.assertEqual(request.call_args.args[3]['reasoning'], {'effort': 'medium'})

    def test_server_catalog_keeps_context_reasoning_and_tool_protocols(self):
        fixtures = [
            model_row('gpt-6.1-sol', 'openai', 500000, ('low', 'medium', 'high', 'xhigh', 'max'),
                      default_reasoning_level='low', tool_mode='code_mode_only'),
            model_row('claude-opus-5-5', 'anthropic', 1000000, ('low', 'medium', 'high', 'xhigh', 'max'),
                      tool_mode='direct', use_responses_lite=False, apply_patch_tool_type=None),
        ]
        for row in fixtures:
            with self.subTest(model=row['id']):
                payload = make_connection('https://fixture.invalid/v1', 'key', fixtures, row['id'])
                self.assertEqual(payload['catalog']['models'], [r['codex'] for r in fixtures])
                self.assertEqual(payload['model_settings']['model_reasoning_effort'], row['codex']['default_reasoning_level'])
                self.assertNotEqual(payload['model_settings']['model_reasoning_effort'], 'none')

    def test_missing_or_legacy_capabilities_fail_instead_of_guessing(self):
        invalid = [{'id': 'unknown', 'owned_by': 'unknown'}, model_row('unknown', context=32768, levels=(),
                    description='CLIProxyAPI · unknown')]
        for row in invalid:
            with self.subTest(row=row), self.assertRaisesRegex(ConfigError, '重新获取'):
                make_connection('https://fixture.invalid/v1', 'key', [row], 'unknown')
        for catalog in ({'models': {}}, {'models': [{'slug': 'a'}, {'slug': 'a'}]}):
            with self.subTest(catalog=catalog), mock.patch('cliproxy_config.gateway._request',
                    side_effect=[{'data': [{'id': 'a'}]}, catalog]), self.assertRaises(ConfigError):
                fetch_models('https://fixture.invalid/v1', 'key')

    def test_discovery_preserves_adapter_metadata_without_inventing_a_codex_catalog(self):
        row = {'id': 'paint-alias', 'owned_by': 'fixture', 'task': 'image_generation',
               'supported_endpoints': ['/v1/images/generations'], 'input_modalities': ['text'],
               'output_modalities': ['image'], 'supported_in_api': False, 'private_backend_field': 'must not migrate'}
        for catalog in ({}, {'data': []}, {'models': []}):
            with self.subTest(catalog=catalog), mock.patch('cliproxy_config.gateway._request',
                    side_effect=[{'data': [row]}, catalog]):
                models = fetch_models('https://fixture.invalid/v1', 'key')
                payload = make_connection('https://fixture.invalid/v1', 'key', models, row['id'], require_capabilities=False)
                portable = payload['catalog']['models'][0]
                self.assertEqual(portable['supported_endpoints'], row['supported_endpoints'])
                self.assertEqual(portable['task'], row['task'])
                self.assertEqual(portable['output_modalities'], row['output_modalities'])
                self.assertFalse(portable['supported_in_api'])
                self.assertNotIn('context_window', portable)
                self.assertNotIn('private_backend_field', portable)

    def test_only_optional_catalog_discovery_tolerates_not_found(self):
        import urllib.error
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://fixture.invalid', 404, 'missing', {}, None)
        with mock.patch('cliproxy_config.gateway.urllib.request.build_opener', return_value=opener):
            self.assertEqual(_request('https://fixture.invalid/v1', 'key', '/models?client_version=0.153.0', allow_not_found=True), {})
            with self.assertRaisesRegex(ConfigError, 'HTTP 404'):
                _request('https://fixture.invalid/v1', 'key', '/responses', {'model': 'fixture'})
            opener.open.side_effect = urllib.error.HTTPError('https://fixture.invalid', 401, 'denied', {}, None)
            with self.assertRaisesRegex(ConfigError, 'HTTP 401'):
                _request('https://fixture.invalid/v1', 'key', '/models?client_version=0.153.0', allow_not_found=True)

    def test_invalid_server_capabilities_are_rejected(self):
        for changes in ({'context_window': True}, {'context_window': 0},
                        {'supported_reasoning_levels': [{'effort': 'invalid'}]},
                        {'default_reasoning_level': 'none'}):
            row = deepcopy(model_row('model'))
            row['codex'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                make_connection('https://fixture.invalid/v1', 'key', [row], 'model')


if __name__ == '__main__':
    unittest.main()
