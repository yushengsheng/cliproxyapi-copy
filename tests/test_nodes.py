import json
import unittest
from cliproxy_config.crypto import ConfigError, decode_document, encode_document, is_encrypted
from cliproxy_config.config import validate_connection
from cliproxy_config.gateway import make_connection
from model_fixtures import model_row


class ModelNodeFormatTests(unittest.TestCase):
    def test_invalid_unicode_text_reports_a_format_error(self):
        from cliproxy_config.crypto import parse_document
        with self.assertRaisesRegex(ConfigError, '有效的 JSON'):
            parse_document('\ud800')

    def test_plain_encrypted_and_old_envelopes_roundtrip_exact_capabilities(self):
        from cliproxy_config.crypto import encrypt
        payload=make_connection('https://fixture.invalid/v1','node-secret',[model_row('claude-fixture')],'claude-fixture')
        payload['provider_settings']['stream_idle_timeout_ms']=300000
        for content,password in [(encode_document(payload),None),
                                  (encode_document(payload,'fixture-password'),'fixture-password'),
                                  (encrypt(payload,'legacy-password'),'legacy-password')]:
            with self.subTest(encrypted=password is not None):
                self.assertEqual(is_encrypted(content),password is not None)
                self.assertEqual(validate_connection(decode_document(content,password)),payload)
                self.assertEqual(decode_document(('\ufeff'+content).encode('utf-8'),password),payload)
                if password is not None:self.assertNotIn('node-secret',content)

    def test_wrong_password_malformed_and_unrecognized_inputs_fail(self):
        for content in ('', '[]', '{}', '{broken', '{"kind":"other"}', b'\xff\xfe'):
            with self.subTest(content=content),self.assertRaises(ConfigError):decode_document(content)
        encrypted=encode_document({'kind':'connection'},'fixture-password')
        for password in (None,'wrong-password'):
            with self.assertRaises(ConfigError):decode_document(encrypted,password)
        document=json.loads(encrypted);document['cipher']='unknown'
        with self.assertRaises(ConfigError):decode_document(json.dumps(document),'fixture-password')
