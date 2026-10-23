"""Native-reader lifecycle and a real local WebKit login/response capture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from cliproxy_config.browser import BrowserUnavailable, _native_eval, read_builtin, read_native
from cliproxy_config.crypto import ConfigError


class BrowserReaderTests(unittest.TestCase):
    def test_native_reader_resets_and_cleans_up_on_success_and_failure(self):
        with mock.patch('cliproxy_config.browser.sys.platform', 'darwin'), \
             mock.patch('cliproxy_config.browser._native_eval', side_effect=[{}, {'status':'ok','keys':['client']}, {}]) as execute:
            self.assertEqual(read_native('https://fixture.invalid/management.html','Brave','https://fixture.invalid')['keys'], ['client'])
            self.assertIn('cleanup', execute.call_args.args[2])
            self.assertTrue(execute.call_args_list[0].kwargs['open_if_missing'])
        with mock.patch('cliproxy_config.browser.sys.platform', 'darwin'), \
             mock.patch('cliproxy_config.browser._native_eval', side_effect=[{}, {'status':'closed'}, {}]) as execute:
            with self.assertRaisesRegex(ConfigError, '取消'):
                read_native('https://fixture.invalid/management.html','Chrome','https://fixture.invalid')
            self.assertIn('cleanup', execute.call_args.args[2])

    def test_applescript_uses_arguments_and_hides_browser_error_details(self):
        with mock.patch('cliproxy_config.browser.subprocess.run', return_value=mock.Mock(returncode=0, stdout='{"status":"ok","keys":[]}')) as run:
            self.assertEqual(_native_eval('https://fixture.invalid/management.html','Chrome','some_script()')['status'],'ok')
            args=run.call_args.args[0]
            self.assertEqual(args[0], '/usr/bin/osascript')
            self.assertIn('some_script()', args)
            self.assertNotIn('some_script()', run.call_args.kwargs['input'])
        with mock.patch('cliproxy_config.browser.subprocess.run', return_value=mock.Mock(returncode=1,stderr='secret')):
            with self.assertRaises(BrowserUnavailable) as result:
                _native_eval('https://fixture.invalid','Brave','some_script()')
            self.assertNotIn('secret',str(result.exception))

    def test_cancel_and_missing_runtime_are_distinguished(self):
        def launch(args,**kw):
            Path(args[-1]).write_text('{"status":"cancelled"}', encoding='utf-8')
            return mock.Mock()
        with mock.patch('cliproxy_config.browser.subprocess.Popen', side_effect=launch):
            with self.assertRaisesRegex(ConfigError, '已取消'):
                read_builtin('https://fixture.invalid','https://fixture.invalid')
        with mock.patch('cliproxy_config.browser.subprocess.Popen', return_value=mock.Mock()):
            with self.assertRaisesRegex(ConfigError, '无法启动'):
                read_builtin('https://fixture.invalid','https://fixture.invalid')

    def test_malformed_native_and_builtin_results_have_clear_errors(self):
        with mock.patch('cliproxy_config.browser.subprocess.run', return_value=mock.Mock(returncode=0, stdout='[]')):
            with self.assertRaises(BrowserUnavailable):
                _native_eval('https://fixture.invalid/management.html', 'Chrome', 'script()')
        for value in ('[]', '{broken'):
            def launch(args, **kwargs):
                Path(args[-1]).write_text(value, encoding='utf-8')
                return mock.Mock()
            with self.subTest(value=value), mock.patch('cliproxy_config.browser.subprocess.Popen', side_effect=launch):
                with self.assertRaisesRegex(ConfigError, '无效数据'):
                    read_builtin('https://fixture.invalid', 'https://fixture.invalid')

    @unittest.skipUnless(sys.platform == 'darwin', 'Real WebKit integration requires macOS')
    def test_real_webview_reads_xhr_and_fetch_without_a_cli_or_extension(self):
        for transport in ('xhr','fetch','saved-login','obfuscated-login','fetch-url','subpath'):
            with self.subTest(transport=transport), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve()
                method = ("const x=new XMLHttpRequest();x.open('GET','/v0/management/config');"
                          "x.setRequestHeader('Authorization','Bearer fixture-management');x.send();") if transport=='xhr' else (
                          "fetch('/v0/management/config',{headers:{Authorization:'Bearer fixture-management'}});")
                prefix = '/proxy' if transport == 'subpath' else ''
                if transport == 'fetch-url':
                    method = "fetch(new URL('/v0/management/config',location.origin),{headers:{Authorization:'Bearer fixture-management'}});"
                if prefix:
                    method = method.replace('/v0/management/config', prefix+'/v0/management/config')
                    method = "fetch('/v0/management/config',{headers:{Authorization:'Bearer fixture-management'}});" + method
                if transport in ('saved-login','obfuscated-login'):
                    method = """let data=JSON.stringify({state:{apiBase:location.origin,managementKey:'fixture-management'}});
                    OBFUSCATE
                    localStorage.setItem('cli-proxy-auth',data);""".replace('OBFUSCATE',
                        "const mask=new TextEncoder().encode('cli-proxy-api-webui::secure-storage|'+location.host+'|'+navigator.userAgent);"
                        "const bytes=new TextEncoder().encode(data);"
                        "data='enc::v1::'+btoa(String.fromCharCode(...bytes.map((n,i)=>n^mask[i%mask.length])));"
                        if transport=='obfuscated-login' else '')
                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        self.send_response(200)
                        if self.path.startswith((prefix+'/v0/management/',prefix+'/v8/management/')):
                            self.send_header('Content-Type','application/json');self.end_headers()
                            self.wfile.write(b'{"access":{"api-keys":["fixture-client"]},"management":{"secret-key":"never-export"}}')
                        elif prefix and self.path.startswith('/v0/management/'):
                            self.send_header('Content-Type','application/json');self.end_headers()
                            self.wfile.write(b'{"api-keys":["wrong-scope-client"]}')
                        else:
                            self.send_header('Content-Type','text/html');self.end_headers()
                            body=method if transport in ('saved-login','obfuscated-login') else 'setTimeout(()=>{'+method+'},1500);'
                            self.wfile.write(('<title>Local browser test</title><script>'+body+'</script>').encode())
                    def log_message(self,*args): pass
                server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                origin='http://127.0.0.1:'+str(server.server_port)
                output=root/'result.json'
                try:
                    result=subprocess.run([sys.executable,'-m','cliproxy_config.browser',
                        origin+prefix+'/management.html#/config',origin,str(output),str(root/'browser-data')],
                        capture_output=True,timeout=20)
                    self.assertEqual(result.returncode,0,result.stderr.decode()[-500:])
                    self.assertEqual(json.loads(output.read_bytes()),{'status':'ok','keys':['fixture-client']})
                    self.assertNotIn('never-export',output.read_text(encoding='utf-8'))
                    self.assertEqual(output.stat().st_mode & 0o777,0o600)
                finally:
                    server.shutdown();server.server_close();thread.join(timeout=2)
