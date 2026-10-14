"""Assemble a self-contained Windows x64 bundle locally, including CPython/Tk."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cliproxy_config import VERSION

RUNTIME_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/20260924/"
               "cpython-3.11.16%2B20260924-x86_64-pc-windows-msvc-install_only_stripped.tar.gz")
# Digest published for this asset by the upstream GitHub release API.
RUNTIME_SHA256 = "f86b3cbd425e1c446b56aa24e20a7be1223c1a8146e5e3a68c8e18d08b76e810"


def download_wheels(wheels: Path):
    if wheels.exists():
        shutil.rmtree(wheels)
    wheels.mkdir(parents=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
                    "--only-binary=:all:", "--platform", "win_amd64", "--implementation", "cp",
                    "--python-version", "311", "--abi", "cp311", "--dest", str(wheels),
                    "-r", str(ROOT / "requirements.txt")], check=True)


def main():
    work = ROOT / "build" / "windows-portable"
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "python-windows-x64.tar.gz"
    if not archive.exists():
        request = urllib.request.Request(RUNTIME_URL, headers={"User-Agent": "cliproxy-config-local-build"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != RUNTIME_SHA256:
        archive.unlink()
        raise SystemExit("Python 运行环境校验失败，已停止构建。")
    extracted = work / "extracted"
    if extracted.exists():
        shutil.rmtree(extracted)
    with tarfile.open(archive) as source:
        source.extractall(extracted, filter="data")
    source_runtime = extracted / "python"
    if not (source_runtime / "python.exe").exists():
        raise SystemExit("Python 运行环境结构不符合预期。")
    wheels = work / "wheels"
    download_wheels(wheels)
    bundle = work / "CLIProxyAPI-Config"
    if bundle.exists():
        shutil.rmtree(bundle)
    runtime = bundle / "runtime"
    shutil.copytree(source_runtime, runtime)
    site = runtime / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for wheel in wheels.glob("*.whl"):
        with zipfile.ZipFile(wheel) as source:
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in source.namelist()):
                raise SystemExit("依赖包结构不安全，已停止构建。")
            source.extractall(site)
    shutil.copytree(ROOT / "cliproxy_config", bundle / "cliproxy_config", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "launch.py", bundle)
    shutil.copy2(ROOT / "README.md", bundle / "使用说明.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", bundle)
    shutil.copytree(ROOT / "third_party_licenses", bundle / "licenses")
    (bundle / "启动配置助手.cmd").write_bytes(
        b'@echo off\r\nstart "" "%~dp0runtime\\pythonw.exe" -E -s "%~dp0launch.py"\r\n')
    (bundle / "启动配置助手.vbs").write_text(
        'Option Explicit\nDim folder, shell, fs\nSet fs = CreateObject("Scripting.FileSystemObject")\n'
        'folder = fs.GetParentFolderName(WScript.ScriptFullName)\nSet shell = CreateObject("WScript.Shell")\n'
        'shell.Run Chr(34) & folder & "\\runtime\\pythonw.exe" & Chr(34) & " -E -s " & Chr(34) & folder & "\\launch.py" & Chr(34), 0, False\n',
        encoding="ascii")
    required = [runtime / "pythonw.exe", runtime / "DLLs" / "_tkinter.pyd",
                site / "cryptography" / "hazmat" / "bindings" / "_rust.pyd"]
    if any(not path.is_file() for path in required) or not list(runtime.rglob("tk.tcl")):
        raise SystemExit("运行依赖不完整，未生成交付压缩包。")
    if any(path.name in {"config.toml", "auth.json", "linux-manager-cliproxyapi.key"} for path in bundle.rglob("*")):
        raise SystemExit("交付目录含有本机配置，已停止构建。")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output_path = dist / f"CLIProxyAPI-Config-Windows-x64-portable-{VERSION}.zip"
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in bundle.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(bundle.parent))
    print("Windows x64 便携包已生成（仅完成结构检查，须在 Windows 验证运行）：" + str(output_path))


if __name__ == "__main__":
    main()
