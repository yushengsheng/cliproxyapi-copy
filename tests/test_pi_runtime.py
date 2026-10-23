"""Load exported files in an installed pi and switch models without sending prompts."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest

from cliproxy_config.agents import PI_PROVIDER, apply_agent
from cliproxy_config.gateway import make_connection
from model_fixtures import model_row

COLLIDING_EXTENSION = '''export default function(pi) {
  pi.unregisterProvider('cliproxyapi');
  pi.registerProvider('cliproxyapi', {
    baseUrl:'https://extension.invalid/v1', api:'openai-responses', apiKey:'fixture-extension-key',
    models:[{id:'extension-only',name:'Extension model',reasoning:false,input:['text'],
      contextWindow:32768,maxTokens:4096,cost:{input:0,output:0,cacheRead:0,cacheWrite:0}}]
  });
}'''


@contextmanager
def pi_rpc(root, directory, extension=None):
    command = [shutil.which('pi'), '--offline', '--mode', 'rpc', '--no-session', '--no-extensions',
               '--no-mcp', '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-approve']
    if extension:
        command.extend(['--extension', str(extension)])
    offline = root / 'offline.mjs'
    offline.write_text("globalThis.fetch = async () => { throw new Error('Offline verification'); };\n", encoding='utf-8')
    node_options = (os.environ.get('NODE_OPTIONS', '') + ' --import=' + str(offline)).strip()
    process = subprocess.Popen(command, cwd=root,
        env=dict(os.environ, PI_CODING_AGENT_DIR=str(directory), PI_OFFLINE='1', PI_TELEMETRY='0', NODE_OPTIONS=node_options),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    records = queue.Queue()
    diagnostics = []

    def read_output():
        for line in process.stdout:
            records.put(line)
        records.put(None)

    def read_errors():
        for line in process.stderr:
            if len(diagnostics) < 20:
                diagnostics.append(line)

    readers = [threading.Thread(target=read_output, daemon=True), threading.Thread(target=read_errors, daemon=True)]
    for reader in readers:
        reader.start()

    def request(kind, **options):
        process.stdin.write(json.dumps(dict(id=kind, type=kind, **options)) + '\n')
        process.stdin.flush()
        while True:
            line = records.get(timeout=15)
            if line is None:
                raise AssertionError('pi exited before replying: ' + ''.join(diagnostics))
            response = json.loads(line)
            if response.get('type') == 'response' and response.get('id') == kind:
                if not response.get('success'):
                    raise AssertionError(response.get('error'))
                return response.get('data')

    try:
        yield request
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


@unittest.skipUnless(shutil.which('pi'), 'pi is not installed; native configuration tests still run')
class PiRuntimeTests(unittest.TestCase):
    def test_native_import_survives_a_same_named_extension_and_supports_model_cycling(self):
        with tempfile.TemporaryDirectory(prefix='cliproxy-pi-runtime-') as temporary:
            root = Path(temporary).resolve()
            directory = root / 'agent'
            payload = make_connection('https://fixture.invalid/v1', 'fixture-key',
                [model_row('fixture-first'), model_row('fixture-second')], 'fixture-first')
            apply_agent('pi', directory, payload, backup=False)
            extension = root / 'provider-collision.ts'
            extension.write_text(COLLIDING_EXTENSION, encoding='utf-8')
            for with_extension in (False, True):
                with self.subTest(with_extension=with_extension):
                    self._check_switching(root, directory, extension if with_extension else None)

    def _check_switching(self, root, directory, extension):
        with pi_rpc(root, directory, extension) as request:
            models = request('get_available_models')['models']
            imported = {row['id'] for row in models if row['provider'] == PI_PROVIDER}
            self.assertEqual(imported, {'fixture-first', 'fixture-second'})
            if extension:
                self.assertIn(('cliproxyapi', 'extension-only'), {(row['provider'], row['id']) for row in models})
            self.assertEqual(request('get_state')['model']['provider'], PI_PROVIDER)
            switched = request('set_model', provider=PI_PROVIDER, modelId='fixture-second')
            self.assertEqual(switched['id'], 'fixture-second')
            self.assertEqual(request('get_state')['model']['id'], 'fixture-second')
            self.assertFalse(request('cycle_model')['isScoped'])

    def test_oauth_models_and_imports_coexist_with_existing_defaults_and_scopes(self):
        with tempfile.TemporaryDirectory(prefix='cliproxy-pi-oauth-') as temporary:
            root = Path(temporary).resolve()
            extension = root / 'provider-collision.ts'
            extension.write_text(COLLIDING_EXTENSION, encoding='utf-8')
            for scoped in (False, True):
                with self.subTest(scoped=scoped):
                    directory = root / str(scoped)
                    directory.mkdir()
                    oauth = {'type': 'oauth', 'access': 'fixture-access', 'refresh': 'fixture-refresh',
                             'accountId': 'fixture-account', 'expires': 9999999999999}
                    (directory / 'auth.json').write_text(json.dumps({'openai-codex': oauth}), encoding='utf-8')
                    with pi_rpc(root, directory, extension) as request:
                        existing = next(row for row in request('get_available_models')['models']
                                        if row['provider'] == 'openai-codex')
                    settings = {'defaultProvider': 'openai-codex', 'defaultModel': existing['id'],
                                'defaultThinkingLevel': 'high'}
                    if scoped:
                        settings['enabledModels'] = ['openai-codex/' + existing['id']]
                    (directory / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
                    connection = make_connection('https://fixture.invalid/v1', 'fixture-key',
                        [model_row('fixture-first'), model_row('fixture-second')], 'fixture-first')
                    for _ in range(2):
                        apply_agent('pi', directory, connection, backup=False)
                    with pi_rpc(root, directory, extension) as request:
                        state = request('get_state')
                        self.assertEqual(state['model']['provider'], 'openai-codex')
                        self.assertEqual(state['model']['id'], existing['id'])
                        self.assertEqual(state['thinkingLevel'], 'high')
                        for provider, model in [('openai-codex', existing['id']),
                                                (PI_PROVIDER, 'fixture-first'), (PI_PROVIDER, 'fixture-second')]:
                            result = request('set_model', provider=provider, modelId=model)
                            self.assertEqual((result['provider'], result['id']), (provider, model))
                        available = request('get_available_models')['models']
                        self.assertIn(('cliproxyapi', 'extension-only'),
                                      {(row['provider'], row['id']) for row in available})
                        seen = set()
                        for _ in range(len(available)):
                            cycled = request('cycle_model')
                            self.assertEqual(cycled['isScoped'], scoped)
                            seen.add((cycled['model']['provider'], cycled['model']['id']))
                        self.assertTrue({('openai-codex', existing['id']), (PI_PROVIDER, 'fixture-first'),
                                         (PI_PROVIDER, 'fixture-second')} <= seen)
                    self.assertEqual(json.loads((directory / 'auth.json').read_bytes())['openai-codex'], oauth)


if __name__ == '__main__':
    unittest.main()
