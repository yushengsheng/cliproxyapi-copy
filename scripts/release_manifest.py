"""Require every native target to pass before preparing a public release."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cliproxy_config import VERSION

TARGETS = (("macOS-arm64", "Darwin", "arm64"),
           ("macOS-x86_64", "Darwin", "x86_64"),
           ("Windows-x64", "Windows", "x86_64"))


def prepare_release(directory: Path, root: Path = ROOT, run_url: str = "") -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", VERSION):
        raise RuntimeError("正式发布必须使用三段式版本号，不能发布开发版。")
    expected = {f"CLIProxyAPI-Config-{target}-{VERSION}.zip": (system, architecture)
                for target, system, architecture in TARGETS}
    if {path.name for path in directory.glob("*.zip")} != set(expected):
        raise RuntimeError("必须包含且仅包含 macOS arm64、macOS x86_64 和 Windows x64 三个当前版本的安装包。")
    section = re.search(r"^## " + re.escape(VERSION) + r"(?=[\s（(]|$).*?\n(.*?)(?=^## |\Z)",
                        (root / "CHANGELOG.md").read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
    if section is None or not section.group(1).strip():
        raise RuntimeError("缺少当前版本的更新记录。")
    checksums, systems = [], []
    for name, (system, architecture) in expected.items():
        archive = directory / name
        report = json.loads(archive.with_suffix(".check.json").read_text(encoding="utf-8"))
        if not isinstance(report, dict) or not isinstance(report.get("runtime"), dict):
            raise RuntimeError("发布自检报告格式错误：" + name)
        runtime = report.get("runtime", {})
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        arch = {"AMD64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(
            runtime.get("architecture"), runtime.get("architecture"))
        valid = (report.get("archive") == name and report.get("version") == VERSION
                 and report.get("sha256") == digest and report.get("archive_crc") == "ok"
                 and report.get("private_files") == "none" and report.get("licenses") == "ok"
                 and report.get("source_match") == "ok" and report.get("browser_capture") == "ok"
                 and runtime.get("version") == VERSION and runtime.get("frozen") is True
                 and runtime.get("platform") == system and arch == architecture
                 and all(runtime.get(key) == "ok" for key in ("crypto", "migration", "credential_helper", "agents", "layout")))
        if os.environ.get("GITHUB_SHA") and report.get("source_revision") != os.environ["GITHUB_SHA"]:
            valid = False
        minimum = report.get("minimum_system_version")
        if system == "Darwin" and (not isinstance(minimum, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", minimum)):
            valid = False
        if not valid:
            raise RuntimeError("发布验证未通过或报告与安装包不匹配：" + name)
        checksums.append(digest + "  " + name)
        systems.append("- " + name + ("：最低 macOS " + minimum if system == "Darwin" else "：Windows x64 原生包"))
    notes = "# CLIProxyAPI 配置助手 v" + VERSION + "\n\n" + section.group(1).strip() + "\n"
    notes += "\n## 下载及验证\n\n" + "\n".join(systems) + "\n\n"
    notes += ("三个包均在对应原生 runner 中通过测试，并从最终压缩包解压验证源码一致性、"
              "加密、导入与恢复、内置网页登录及本机密钥读取。校验值见 SHA256SUMS；详细验证见同名 .check.json。\n\n"
              "Windows 请完整解压后运行 CLIProxyAPI-Config.exe，无需 Python；内置登录需要 WebView2 Runtime。"
              "macOS 请解压后打开 .app；使用临时签名，未经 Apple 公证，可能需在系统隐私与安全性中允许打开。\n")
    if run_url:
        notes += "\n构建记录：" + run_url + "\n"
    (directory / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="ascii")
    (root / "release-notes.md").write_text(notes, encoding="utf-8")
    return VERSION


if __name__ == "__main__":
    version = prepare_release(ROOT / "dist", run_url=os.environ.get("RUN_URL", ""))
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
            output.write("RELEASE_VERSION=" + version + "\n")
    print("全部平台已通过发布验证：" + version)
