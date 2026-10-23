# 第三方组件许可说明

本地源码启动器使用项目 `.venv` 中的依赖及 Python 安装自带的 Tcl/Tk。运行依赖包括 cryptography、cffi、pycparser、tomlkit、tkinterdnd2、truststore、ruamel.yaml、ruamel.yaml.clib。内置登录窗口使用 pywebview（BSD 3-Clause）及 proxy_tools、bottle、typing_extensions；macOS 使用 PyObjC 系列框架，Windows 使用 pythonnet / clr_loader 和系统 WebView2。原有版权和许可声明保留。ruamel.yaml 用于保留 Hermes YAML 配置的注释及无关设置，采用 MIT 许可；ruamel.yaml.clib 包含 MIT / Expat 许可代码。

构建原生包时，依赖许可随程序提供：

- macOS：CPython 与 Tcl/Tk 许可位于应用的 `Contents/Resources/licenses`；Python 依赖许可位于 `Contents/Resources/*dist-info/licenses`。
- Windows 原生包：CPython 与 Tcl/Tk 许可位于 `_internal/licenses`，Python 依赖许可位于 `_internal/*dist-info/licenses`。
- 原生程序通过 PyInstaller 构建，其引导程序的许可及特殊例外位于对应平台资源目录的 `pyinstaller-*.dist-info/licenses/COPYING.txt`。

Tcl/Tk 许可文本来自官方仓库的 `core-9-0-4` 标签：

- <https://github.com/tcltk/tcl/blob/core-9-0-4/license.terms>
- <https://github.com/tcltk/tk/blob/core-9-0-4/license.terms>

具体授权条款以各组件随附的原始许可文本为准。
