"""Verify and run the exact ZIP archive produced on a native release runner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import types
import zipfile

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cliproxy_config import VERSION
from cliproxy_config.config import FILES


def fingerprint(code):
    return (code.co_code, code.co_names, code.co_varnames, code.co_argcount, code.co_posonlyargcount,
            code.co_kwonlyargcount, code.co_flags, code.co_freevars, code.co_cellvars, code.co_exceptiontable,
            tuple(fingerprint(value) if isinstance(value, types.CodeType) else value for value in code.co_consts))


def main():
    archives = list((ROOT / "dist").glob(f"*-{VERSION}.zip"))
    if len(archives) != 1:
        raise SystemExit("验证目录必须仅含当前平台的一个发布包。")
    archive = archives[0]
    protected = set(FILES) | {"auth.json"}
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:
            raise RuntimeError("压缩包完整性校验失败。")
        names = source.namelist()
        if any(Path(name).name in protected or name.endswith((".cproxy", ".cproxybackup")) for name in names):
            raise RuntimeError("交付包混入客户端配置文件。")
        for package in ("cryptography", "cffi", "pycparser", "tomlkit", "tkinterdnd2", "truststore", "pyinstaller"):
            if not any("/" + package + "-" in name and "/licenses/" in name for name in names):
                raise RuntimeError("交付包缺少许可证：" + package)
        for suffix in ("/licenses/CPython/LICENSE.txt", "/licenses/Tcl.txt", "/licenses/Tk.txt", "/THIRD_PARTY_NOTICES.md"):
            if not any(name.endswith(suffix) for name in names):
                raise RuntimeError("交付包缺少许可说明：" + suffix)
    report = {"version": VERSION, "archive": archive.name,
              "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "archive_crc": "ok", "private_files": "none", "licenses": "ok"}
    with tempfile.TemporaryDirectory(prefix="cliproxy-release-中文 空格-") as temporary:
        directory = Path(temporary).resolve()
        if sys.platform == "darwin":
            subprocess.run(["ditto", "-x", "-k", str(archive), str(directory)], check=True)
            bundle = directory / "CLIProxyAPI-Config.app"
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
            info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
            report["minimum_system_version"] = info["LSMinimumSystemVersion"]
            binary = bundle / "Contents/MacOS/CLIProxyAPI-Config"
        elif sys.platform == "win32":
            with zipfile.ZipFile(archive) as source:
                source.extractall(directory)
            binary = directory / "CLIProxyAPI-Config/CLIProxyAPI-Config.exe"
        else:
            raise SystemExit("请在交付包对应系统中运行验证。")
        reader = CArchiveReader(str(binary))
        pyz = reader.open_embedded_archive(next(name for name in reader.toc if name.endswith(".pyz")))
        for path in (ROOT / "cliproxy_config").glob("*.py"):
            if path.name == "__main__.py":
                continue
            module = "cliproxy_config" if path.name == "__init__.py" else "cliproxy_config." + path.stem
            if fingerprint(pyz.extract(module)) != fingerprint(compile(path.read_bytes(), str(path), "exec", dont_inherit=True)):
                raise RuntimeError("交付包内嵌代码落后于源码：" + path.name)
        result_path = directory / "runtime-self-check.json"
        environment = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH"):
            environment.pop(name, None)
        result = subprocess.run([str(binary), "--self-check", "--self-check-report", str(result_path)],
                                cwd=directory, env=environment, capture_output=True, text=True, timeout=60)
        if not result_path.is_file():
            raise RuntimeError("交付程序没有生成运行自检报告。" + result.stderr)
        runtime = json.loads(result_path.read_text(encoding="utf-8"))
        if result.returncode or runtime.get("version") != VERSION or not runtime.get("frozen"):
            raise RuntimeError("交付程序自检失败：" + json.dumps(runtime, ensure_ascii=False))
        for name in ("crypto", "migration", "credential_helper", "layout"):
            if runtime.get(name) != "ok":
                raise RuntimeError("交付程序自检未完成：" + name)
        report.update(source_match="ok", runtime=runtime)
    output = archive.with_suffix(".check.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
