from copy import deepcopy
import unittest

from cliproxy_config.agents import AGENTS, prepare_import
from cliproxy_config.crypto import decode_document, encode_document
from cliproxy_config.gateway import make_connection
from model_fixtures import model_row


def connection(rows, default=None):
    return make_connection('https://fixture.invalid/v1', 'fixture-secret', rows,
                           default or rows[0]['id'], require_capabilities=False)


class ModelSupportTests(unittest.TestCase):
    def test_each_target_filters_mixed_selection_and_keeps_vision_chat(self):
        rows = [model_row('gpt-image-test'), model_row('text-embedding-fixture'),
                model_row('gpt-vision', input_modalities=['text', 'image']), model_row('claude-vision')]
        original = connection(rows)
        for agent in AGENTS:
            with self.subTest(agent=agent):
                plan = prepare_import(agent, original)
                expected = ('claude-vision',) if agent == 'claude' else ('gpt-vision', 'claude-vision')
                self.assertEqual(plan.accepted, expected)
                self.assertTrue(plan.default_changed)
                self.assertEqual(plan.default_model, expected[0])
                reasons = {row.name: row.reason for row in plan.skipped}
                self.assertIn('Images API', reasons['gpt-image-test'])
                self.assertIn('向量', reasons['text-embedding-fixture'])
                self.assertNotIn('fixture-secret', repr(plan))
        self.assertEqual(original, connection(rows))

    def test_explicit_metadata_and_known_independent_names(self):
        images = [model_row('paint-alias', task='image_generation'),
                  model_row('paint-output', architecture={'output_modalities': ['image']}),
                  model_row('paint-endpoint', supported_endpoints=['/v1/images/generations']),
                  model_row('provider/gpt-image-1'), model_row('dall-e-3'), model_row('flux-1'),
                  model_row('gemini-fixture-image-preview')]
        plan = prepare_import('pi', connection(images))
        self.assertIsNone(plan.payload)
        self.assertTrue(all('图像生成' in row.reason for row in plan.skipped))

    def test_image_word_and_image_input_do_not_exclude_conversation_models(self):
        rows = [model_row('my-image-assistant', input_modalities=['text', 'image']),
                model_row('gpt-image-conversation-alias', output_modalities=['text', 'image']),
                model_row('multimodal', task='image-generation', output_modalities=['text', 'image'])]
        plan = prepare_import('codex', connection(rows))
        self.assertEqual(plan.accepted, tuple(row['id'] for row in rows))
        self.assertFalse(plan.skipped)
        self.assertEqual(plan.payload['catalog']['models'], [row['codex'] for row in rows])

    def test_explicit_protocols_are_target_specific(self):
        rows = [model_row('claude-messages', supported_endpoints=[' /v1/messages ']),
                model_row('claude-responses', api='openai-responses'),
                model_row('claude-chat', wire_api='chat_completions')]
        expected = {'codex': 'claude-responses', 'pi': 'claude-responses', 'claude': 'claude-messages',
                    'claude-code': 'claude-messages', 'hermes': 'claude-chat', 'siyuan': 'claude-chat'}
        for agent, name in expected.items():
            with self.subTest(agent=agent):
                plan = prepare_import(agent, connection(rows))
                self.assertEqual(plan.accepted, (name,))
                self.assertTrue(all('接口' in row.reason for row in plan.skipped))

    def test_specialized_models_and_unsupported_endpoints_are_explained(self):
        rows = [model_row('vector-alias', task='feature-extraction'),
                model_row('audio-alias', output_modalities=['audio']),
                model_row('sort-alias', task='reranking'),
                model_row('custom-endpoint', supported_endpoints=['/v1/audio/speech']),
                model_row('whisper-test'), model_row('tts-test'), model_row('sora-test')]
        plan = prepare_import('hermes', connection(rows))
        self.assertIsNone(plan.payload)
        self.assertEqual(len(plan.skipped), len(rows))
        self.assertTrue(all(row.reason for row in plan.skipped))

    def test_absent_codex_capabilities_only_block_codex_and_roundtrip_unchanged(self):
        original = connection([{'id': 'claude-custom', 'task': 'text-generation',
                                'input_modalities': ['text', 'image']}])
        for encrypted in (False, True):
            password = 'fixture-password' if encrypted else None
            payload = decode_document(encode_document(original, password).encode(), password)
            self.assertEqual(payload, original)
            for agent in AGENTS:
                with self.subTest(agent=agent, encrypted=encrypted):
                    plan = prepare_import(agent, payload)
                    if agent == 'codex':
                        self.assertIsNone(plan.payload)
                        self.assertIn('原生上下文', plan.skipped[0].reason)
                    else:
                        self.assertEqual(plan.accepted, ('claude-custom',))

    def test_api_disabled_and_unreliable_legacy_catalog_are_skipped(self):
        disabled = connection([model_row('claude-disabled', supported_in_api=False)])
        for agent in AGENTS:
            with self.subTest(agent=agent):
                self.assertIn('不支持 API', prepare_import(agent, disabled).skipped[0].reason)
        legacy = connection([model_row('claude-legacy', context=32768, levels=(), description='CLIProxyAPI · legacy')])
        self.assertIn('旧版', prepare_import('codex', legacy).skipped[0].reason)
        self.assertEqual(prepare_import('pi', legacy).accepted, ('claude-legacy',))

    def test_default_replacement_discards_previous_model_settings_without_mutation(self):
        payload = connection([model_row('gpt-image-test'), model_row('gpt-chat', context=1000000,
                              levels=('low', 'high'), default_reasoning_level='high')])
        payload['model_settings'].update(model_reasoning_effort='medium', model_context_window=10000,
                                         model_auto_compact_token_limit=8000)
        payload['provider_settings']['stream_idle_timeout_ms'] = 300000
        before = deepcopy(payload)
        plan = prepare_import('codex', payload)
        self.assertEqual(plan.payload['model_settings'], {'model_reasoning_effort': 'high'})
        self.assertEqual(plan.payload['provider_settings'], payload['provider_settings'])
        self.assertEqual(payload, before)
        plan.payload['catalog']['models'][0]['context_window'] = 1
        self.assertEqual(payload, before)

    def test_compatible_default_and_its_settings_are_preserved(self):
        payload = connection([model_row('gpt-image-test'), model_row('claude-vision')], 'claude-vision')
        payload['model_settings'].update(model_reasoning_effort='high', model_context_window=200000)
        plan = prepare_import('claude', payload)
        self.assertFalse(plan.default_changed)
        self.assertEqual(plan.payload['model_settings'], payload['model_settings'])

    def test_desktop_limit_retains_valid_default_and_original_order(self):
        rows = [model_row(f'claude-fixture-{i}') for i in range(203)]
        plan = prepare_import('claude', connection(rows, rows[-1]['id']))
        self.assertEqual(len(plan.accepted), 200)
        self.assertEqual(plan.default_model, rows[-1]['id'])
        self.assertFalse(plan.default_changed)
        self.assertEqual(plan.accepted, tuple(row['id'] for row in rows[:199] + rows[-1:]))
        self.assertTrue(all('200' in row.reason for row in plan.skipped))


if __name__ == '__main__':
    unittest.main()
