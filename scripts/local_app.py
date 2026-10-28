"""Create a tiny macOS source launcher; never copy Python or application code."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import plistlib
import runpy
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "CLIProxyAPI 配置助手.app"
BUNDLE_ID = "local.cliproxyapi.config.source"
ALERT_SCRIPT = '''on run argv
    display alert "CLIProxyAPI 配置助手启动失败" message (item 1 of argv) as critical buttons {"好"} default button "好"
end run
'''
LAUNCHER = r'''#!/bin/sh
umask 077
headless=0
for argument in "$@"; do
    [ "$argument" = "--self-check" ] && headless=1
done
alert() {
    printf '%s\n' "$1" >&2
    if [ "$headless" -eq 0 ]; then
        /usr/bin/osascript - "$1" <<'APPLESCRIPT'
on run argv
    display alert "CLIProxyAPI 配置助手启动失败" message (item 1 of argv) as critical buttons {"好"} default button "好"
end run
APPLESCRIPT
    fi
}
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd) || exit 1
log_dir="$HOME/Library/Logs/CLIProxyAPI-Config"
log_file="$log_dir/local-launcher.log"
if ! mkdir -p "$log_dir" || [ -L "$log_file" ] || ! touch "$log_file" || ! chmod 600 "$log_file"; then
    alert "无法创建启动日志：$log_file"
    exit 1
fi
exec >>"$log_file" 2>&1
if [ ! -x "$task_root/.venv/bin/python" ]; then
    alert "找不到项目的 .venv/bin/python，请按 README.md 准备本地运行环境。此 .app 必须与源码放在同一目录。"
    exit 1
fi
if [ ! -f "$task_root/launch.py" ] || [ ! -f "$task_root/scripts/local_app.py" ]; then
    alert "找不到本地源码，请将 .app 放回项目根目录。"
    exit 1
fi
cd "$task_root" || exit 1
PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
export PATH PYTHONUNBUFFERED=1
if [ "$(/usr/bin/uname -s)" = "Darwin" ]; then
    exec /usr/bin/arch -__PYTHON_ARCH__ "$task_root/.venv/bin/python" "$task_root/scripts/local_app.py" --run "$@"
fi
exec "$task_root/.venv/bin/python" "$task_root/scripts/local_app.py" --run "$@"
'''


def create_launcher(root: Path = ROOT) -> Path:
    root = root.resolve()
    destination = root / APP_NAME
    if destination.is_symlink():
        raise ValueError("本地 .app 入口是符号链接，未覆盖。")
    with tempfile.TemporaryDirectory(prefix=".local-launcher-", dir=root) as temp:
        staged = Path(temp) / APP_NAME
        executable = staged / "Contents/MacOS/launcher"
        executable.parent.mkdir(parents=True)
        # Finder can start shell-based apps under Rosetta; match the local runtime.
        executable.write_text(LAUNCHER.replace("__PYTHON_ARCH__", platform.machine()), encoding="utf-8")
        executable.chmod(0o755)
        (staged / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "CLIProxyAPI 配置助手",
            "CFBundleDisplayName": "CLIProxyAPI 配置助手", "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "launcher", "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1.0", "NSHighResolutionCapable": True,
            "CFBundleGetInfoString": "本地源码启动器：使用同目录 .venv 和 launch.py，不打包业务代码。",
        }))
        previous = Path(temp) / "previous.app"
        if destination.exists():
            destination.rename(previous)
        try:
            staged.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    return destination


def run_source(root: Path, arguments: list[str]) -> None:
    root = root.resolve()
    sys.path.insert(0, str(root))
    sys.argv = [str(root / "launch.py"), *arguments]
    os.chdir(root)
    try:
        runpy.run_path(str(root / "launch.py"), run_name="__main__")
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code in (None, 0):
            raise
        traceback.print_exc()
        if "--self-check" not in arguments:
            log = Path.home() / "Library/Logs/CLIProxyAPI-Config/local-launcher.log"
            message = "本地源码启动失败，请检查运行环境。启动日志：" + str(log)
            try:
                subprocess.run(["/usr/bin/osascript", "-", message], input=ALERT_SCRIPT,
                               text=True, capture_output=True, check=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as alert_error:
                print("无法显示启动错误提示：" + str(alert_error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    if sys.argv[1:2] == ["--run"]:
        run_source(ROOT, sys.argv[2:])
    else:
        print("已创建本地源码启动器：" + str(create_launcher()))
