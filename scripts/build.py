"""Build locally on the target OS; package code and runtime, never user settings."""
from __future__ import annotations

import argparse
from importlib.metadata import distribution
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import sysconfig
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cliproxy_config import VERSION


def run(arguments):
    subprocess.run(arguments, cwd=ROOT, check=True, timeout=60 if "--self-check" in arguments else None)


def minimum_macos_version(bundle: Path) -> str:
    minimum = (11, 0, 0)
    seen = set()
    magic_numbers = {bytes.fromhex(value) for value in (
        "feedface", "cefaedfe", "feedfacf", "cffaedfe",
        "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}
    for path in bundle.rglob("*"):
        if not path.is_file() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        with path.open("rb") as handle:
            if handle.read(4) not in magic_numbers:
                continue
        result = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True, check=True)
        versions = re.findall(r"\bminos\s+(\d+(?:\.\d+)*)", result.stdout)
        versions += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\d+(?:\.\d+)*)", result.stdout)
        for value in versions:
            parts = tuple(int(part) for part in value.split("."))
            minimum = max(minimum, parts + (0,) * (3 - len(parts)))
    return ".".join(str(part) for part in (minimum if minimum[2] else minimum[:2]))


def ensure_static_crypto():
    if sys.platform != "darwin":
        return
    package = distribution("cryptography")
    bindings = next(package.locate_file(path) for path in package.files
                    if Path(path).name.startswith("_rust.") and str(path).endswith(".so"))
    linked = subprocess.run(["otool", "-L", str(bindings)], capture_output=True, text=True, check=True)
    openssl = re.search(r"^\s+(/[^\n]*?/libssl[^\s]*\.dylib)\s", linked.stdout, re.MULTILINE)
    if openssl is None:
        return
    prefix = Path(openssl.group(1)).parent.parent
    if not all((prefix / "lib" / name).is_file() for name in ("libssl.a", "libcrypto.a")):
        raise SystemExit("加密库动态链接 OpenSSL，但未找到对应静态库，无法生成可靠的独立程序。")
    environment = os.environ.copy()
    environment.update(OPENSSL_STATIC="1", OPENSSL_DIR=str(prefix))
    subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps",
                    "--no-binary=cryptography", "--no-cache-dir", "cryptography==" + package.version],
                   env=environment, check=True, timeout=600)
    linked = subprocess.run(["otool", "-L", str(bindings)], capture_output=True, text=True, check=True)
    if re.search(r"/lib(?:ssl|crypto)[^\s]*\.dylib", linked.stdout):
        raise SystemExit("加密库仍动态链接 OpenSSL，已停止发布构建。")


def windows_version_file() -> Path:
    version = tuple(int(part) for part in re.match(r"(\d+)\.(\d+)\.(\d+)", VERSION).groups()) + (0,)
    fields = {"FileDescription": "CLIProxyAPI 配置助手", "FileVersion": VERSION,
              "InternalName": "CLIProxyAPI-Config", "OriginalFilename": "CLIProxyAPI-Config.exe",
              "ProductName": "CLIProxyAPI 配置助手", "ProductVersion": VERSION}
    strings = ",\n".join("StringStruct(" + repr(key) + ", " + repr(value) + ")" for key, value in fields.items())
    content = ("VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(version) + ", prodvers=" + repr(version) +
               ", mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)), kids=["
               "StringFileInfo([StringTable('080404b0', [" + strings + "])]), "
               "VarFileInfo([VarStruct('Translation', [2052, 1200])])])\n")
    path = ROOT / "build/windows-version.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="构建自带运行环境的桌面程序")
    parser.add_argument("--target-arch", choices=("arm64", "x86_64", "universal2"))
    options = parser.parse_args()
    if sys.platform not in ("darwin", "win32"):
        parser.error("请在 macOS 或 Windows 本机上构建相应安装包。")
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    ensure_static_crypto()
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
               "--name", "CLIProxyAPI-Config", "--collect-all", "tkinterdnd2", "--collect-all", "webview",
               "--osx-bundle-identifier", "local.cliproxyapi.config", "--distpath", str(ROOT / "dist"),
               "--workpath", str(ROOT / "build"), str(ROOT / "launch.py")]
    if options.target_arch:
        if sys.platform != "darwin":
            parser.error("--target-arch 仅用于 macOS；请在对应架构的 Windows 上构建。")
        command.extend(["--target-arch", options.target_arch])
    if sys.platform == "win32":
        command.extend(["--version-file", str(windows_version_file())])
    for package in ("cryptography", "cffi", "pycparser", "tomlkit", "tkinterdnd2", "truststore", "ruamel.yaml", "ruamel.yaml.clib", "pyinstaller", "pywebview", "proxy_tools", "bottle", "typing_extensions"):
        command.extend(["--copy-metadata", package])
    browser_packages = (("pyobjc-core", "pyobjc-framework-Cocoa", "pyobjc-framework-Quartz",
        "pyobjc-framework-WebKit", "pyobjc-framework-security", "pyobjc-framework-UniformTypeIdentifiers")
        if sys.platform == "darwin" else ("pythonnet", "clr_loader"))
    for package in browser_packages:
        command.extend(["--copy-metadata", package])
    python_license = next((path for path in (Path(sys.base_prefix) / "LICENSE.txt",
                          Path(sysconfig.get_path("stdlib")) / "LICENSE.txt") if path.is_file()), None)
    if python_license is None:
        raise SystemExit("未找到 CPython 许可证，已停止构建。请使用包含许可证的完整 Python 安装。")
    for source, destination in ((python_license, "licenses/CPython"),
                                (ROOT / "third_party_licenses", "licenses"),
                                (ROOT / "THIRD_PARTY_NOTICES.md", ".")):
        command.extend(["--add-data", str(source) + ":" + destination])
    run(command)
    if sys.platform == "darwin":
        bundle = ROOT / "dist" / "CLIProxyAPI-Config.app"
        info_path = bundle / "Contents" / "Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info.update(CFBundleDisplayName="CLIProxyAPI 配置助手", CFBundleShortVersionString=VERSION,
                    CFBundleVersion=VERSION, LSMinimumSystemVersion=minimum_macos_version(bundle), NSHighResolutionCapable=True)
        info_path.write_bytes(plistlib.dumps(info))
        run(["codesign", "--force", "--sign", "-", str(bundle)])
        run(["codesign", "--verify", "--deep", "--strict", str(bundle)])
        run([str(bundle / "Contents" / "MacOS" / "CLIProxyAPI-Config"), "--self-check",
             "--self-check-report", str(ROOT / "build" / "packaged-self-check.json")])
        architecture = options.target_arch or platform.machine()
        archive = ROOT / "dist" / f"CLIProxyAPI-Config-macOS-{architecture}-{VERSION}.zip"
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(archive)])
    else:
        folder = ROOT / "dist" / "CLIProxyAPI-Config"
        run([str(folder / "CLIProxyAPI-Config.exe"), "--self-check",
             "--self-check-report", str(ROOT / "build" / "packaged-self-check.json")])
        shutil.copy2(ROOT / "README.md", folder / "使用说明.md")
        shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", folder)
        architecture = {"AMD64": "x64", "x86_64": "x64", "ARM64": "arm64"}.get(platform.machine(), platform.machine())
        archive = ROOT / "dist" / f"CLIProxyAPI-Config-Windows-{architecture}-{VERSION}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for path in folder.rglob("*"):
                if path.is_file():
                    output.write(path, path.relative_to(folder.parent))
    print("本地构建完成：" + str(archive))


if __name__ == "__main__":
    main()
