"""Read management responses in a native browser window, without a CLI bridge."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

from .config import atomic_write, read_bytes
from .crypto import ConfigError

BROWSERS = ("内置登录窗口", "Brave", "Chrome")


class BrowserUnavailable(ConfigError):
    """An external browser cannot be read; the built-in window can be used."""


def capture_script(origin: str, prefix: str = "") -> str:
    # Capture only the client keys from this origin's management config response.
    # Management authorization remains inside the logged-in browser.
    return r'''(() => {
      if (location.origin !== ORIGIN) return JSON.stringify({status:'wrong_origin'});
      const prefix = PREFIX;
      const existing = window.__cliproxyCapture;
      if (existing) return JSON.stringify(existing);
      const state = window.__cliproxyCapture = {status:'waiting'};
      const valid = u => {
        try { const p = new URL(u, location.href);
          return p.origin === ORIGIN && /^\/v[08]\/management\//.test(p.pathname.slice(prefix.length)) &&
            p.pathname.startsWith(prefix+'/');
        } catch { return false; }
      };
      const config = u => valid(u) && /\/management\/config$/.test(new URL(u, location.href).pathname);
      const accept = body => {
        try {
          if (typeof body === 'string') body = JSON.parse(body);
          const keys = body?.access?.['api-keys'] ?? body?.['api-keys'];
          if (Array.isArray(keys)) {
            state.keys=keys.length <= 256 && keys.every(k=>typeof k==='string' && k.length<=8192) ? keys : [];
            state.status='ok';
          }
        } catch {}
      };
      const originalFetch = window.fetch.bind(window);
      let fetching = false;
      const requestConfig = (u, authorization) => {
        if (!valid(u) || !authorization || fetching || state.status === 'ok') return;
        fetching = true;
        const p = new URL(u, location.href);
        const version = p.pathname.slice(prefix.length).startsWith('/v8/') ? 'v8' : 'v0';
        const path = prefix+'/'+version+'/management/config';
        const alternate = prefix+'/'+(version==='v8' ? 'v0' : 'v8')+'/management/config';
        (async()=>{
          for (const candidate of [path,alternate]) {
            const response=await originalFetch(ORIGIN+candidate,
              {headers:{Authorization:authorization},credentials:'same-origin',redirect:'error'});
            if (response.ok) {accept(await response.json()); break;}
            if (response.status!==404) break;
          }
        })().catch(()=>{}).finally(()=>{fetching=false;});
      };
      window.fetch = function(input, options) {
        const u = typeof input === 'string' || input instanceof URL ? String(input) : input.url;
        const headers = new Headers(options?.headers ?? input?.headers);
        requestConfig(u, headers.get('Authorization'));
        return originalFetch(input, options).then(r => {
          if (config(u) && r.ok) r.clone().json().then(accept).catch(()=>{});
          return r;
        });
      };
      const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
      const setHeader = XMLHttpRequest.prototype.setRequestHeader;
      const requests = new WeakMap();
      XMLHttpRequest.prototype.open = function(method, u, ...args) {
        requests.set(this, {url:u}); return open.call(this, method, u, ...args);
      };
      XMLHttpRequest.prototype.setRequestHeader = function(k,v) {
        const r=requests.get(this);
        if (r && k.toLowerCase() === 'authorization') r.authorization=v;
        return setHeader.call(this,k,v);
      };
      XMLHttpRequest.prototype.send = function(...args) {
        const r=requests.get(this);
        if (r) {
          requestConfig(r.url,r.authorization);
          if (config(r.url)) this.addEventListener('load',()=>{
            if (this.status===200) accept(this.responseType==='json'?this.response:this.responseText);
          }, {once:true});
        }
        return send.apply(this,args);
      };
      Object.defineProperty(state, 'cleanup', {value: () => {
        window.fetch=originalFetch;
        XMLHttpRequest.prototype.open=open;
        XMLHttpRequest.prototype.send=send;
        XMLHttpRequest.prototype.setRequestHeader=setHeader;
        delete window.__cliproxyCapture;
      }});
      // The management UI may finish loading before injection, or cache its config.
      // Reuse its own saved login only inside this origin; never return that credential.
      try {
        let saved=localStorage.getItem('cli-proxy-auth');
        if (saved?.startsWith('enc::v1::')) {
          const bytes=Uint8Array.from(atob(saved.slice(9)),c=>c.charCodeAt(0));
          const mask=new TextEncoder().encode('cli-proxy-api-webui::secure-storage|'+location.host+'|'+navigator.userAgent);
          saved=new TextDecoder().decode(bytes.map((n,i)=>n^mask[i%mask.length]));
        }
        const stored=JSON.parse(saved), auth=stored?.state ?? stored;
        if (auth && (!auth.apiBase || new URL(auth.apiBase).origin===ORIGIN) &&
            typeof auth.managementKey==='string' && auth.managementKey.length<=8192 &&
            auth.managementKey && !/[\r\n]/.test(auth.managementKey)) {
          requestConfig(ORIGIN+prefix+'/v8/management/config','Bearer '+auth.managementKey);
        }
      } catch {}
      // Route changes cause the SPA to issue fresh authenticated management requests.
      // A login made after installation is captured by the same hooks.
      if (location.hash !== '#/login') {
        const previous=location.hash;
        location.hash = previous === '#/config' ? '#/' : '#/config';
        if (previous === '#/config') setTimeout(()=>{location.hash='#/config';},300);
      }
      return JSON.stringify(state);
    })()'''.replace("ORIGIN", json.dumps(origin)).replace("PREFIX", json.dumps(prefix.rstrip("/")))


def _native_eval(page: str, browser: str, script: str, *, open_if_missing=False) -> dict:
    application = {"Brave": "Brave Browser", "Chrome": "Google Chrome"}[browser]
    source = '''on run argv
    set pageURL to item 1 of argv
    set pagePrefix to item 2 of argv
    set scriptSource to item 3 of argv
    tell application "APPLICATION"
        repeat with browserWindow in windows
            repeat with browserTab in tabs of browserWindow
                if URL of browserTab starts with pagePrefix then
                    return execute browserTab javascript scriptSource
                end if
            end repeat
        end repeat
        if item 4 of argv is not "open" then return "{\\"status\\":\\"closed\\"}"
        if (count of windows) is 0 then make new window
        set newTab to make new tab at end of tabs of front window with properties {URL:pageURL}
        activate
        return "{\\"status\\":\\"loading\\"}"
    end tell
end run
'''.replace("APPLICATION", application)
    try:
        response = subprocess.run(["/usr/bin/osascript", "-", page, page.split("#")[0], script,
                                   "open" if open_if_missing else "existing"],
                                  input=source, capture_output=True, text=True, timeout=12)
        if response.returncode:
            raise BrowserUnavailable("无法读取外部浏览器，将改用内置登录窗口。")
        result = json.loads(response.stdout)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise BrowserUnavailable("外部浏览器暂不可读取，将改用内置登录窗口。") from None


def read_native(page: str, browser: str, origin: str) -> dict:
    if sys.platform != "darwin":
        raise BrowserUnavailable("此系统使用内置登录窗口获取网页数据。")
    script = capture_script(origin, urllib.parse.urlsplit(page).path.removesuffix("/management.html"))
    deadline = time.monotonic() + 180
    cleanup = "window.__cliproxyCapture?.cleanup?.(); JSON.stringify({status:'done'})"
    _native_eval(page, browser, cleanup, open_if_missing=True)
    try:
        while time.monotonic() < deadline:
            result = _native_eval(page, browser, script)
            if result.get("status") == "ok":
                return result
            if result.get("status") == "closed":
                raise ConfigError("后台标签页已关闭，已取消网页获取。")
            time.sleep(.6)
    finally:
        try:
            _native_eval(page, browser, cleanup)
        except ConfigError:
            pass
    raise BrowserUnavailable("外部浏览器读取超时，将改用内置登录窗口。")


def read_builtin(page: str, origin: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="cliproxy-browser-") as folder:
        destination = Path(folder).resolve() / "result.json"
        if getattr(sys, "frozen", False):
            command, cwd = [sys.executable, "--browser-reader", page, origin, str(destination)], None
        else:
            # -m resolves the package from the working directory, not from the caller's sys.path.
            command = [sys.executable, "-m", "cliproxy_config.browser", page, origin, str(destination)]
            cwd = Path(__file__).resolve().parents[1]
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                process.wait(timeout=300)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise ConfigError("等待登录超时，请重新获取模型。") from None
            if not destination.is_file():
                raise ConfigError("内置登录窗口无法启动。请检查 pywebview 及系统 WebView 组件，或选择 Brave / Chrome。")
            result = json.loads(read_bytes(destination, 3 * 1024 * 1024))
            if not isinstance(result, dict):
                raise ValueError()
            if result.get("status") != "ok":
                raise ConfigError("已取消网页获取；请在登录窗口完成后台登录后再试。")
            return result
        except OSError:
            raise ConfigError("无法启动内置登录窗口。") from None
        except (ValueError, RecursionError):
            raise ConfigError("登录窗口返回了无效数据，请重新获取。") from None


def browser_main(arguments: list[str]) -> None:
    page, origin, output = arguments[:3]
    import webview
    destination = Path(output).resolve()
    script = capture_script(origin, urllib.parse.urlsplit(page).path.removesuffix("/management.html"))
    finished = threading.Event()
    window = webview.create_window("登录 CLIProxyAPI 后台 · 登录后自动获取模型", page, width=1080, height=760)

    def capture():
        while not finished.wait(.5):
            try:
                value = window.evaluate_js(script)
                result = json.loads(value) if isinstance(value, str) else value
            except Exception:
                # Navigation invalidates the current JS context; reinstall on the next page.
                continue
            if isinstance(result, dict) and result.get("status") == "ok":
                try:
                    atomic_write(destination, json.dumps(result).encode("utf-8"))
                finally:
                    finished.set()
                    window.destroy()
    window.events.closed += lambda: finished.set()
    storage = Path(arguments[3]).resolve() if len(arguments) > 3 else Path.home() / ".config/cliproxyapi-config/browser-data"
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    webview.start(capture, private_mode=False, storage_path=str(storage))
    if not destination.exists():
        atomic_write(destination, b'{"status":"cancelled"}')


if __name__ == "__main__":
    browser_main(sys.argv[1:])
