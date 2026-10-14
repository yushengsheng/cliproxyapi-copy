# 第三方组件许可说明

本程序随包提供 CPython、Tcl/Tk，以及 cryptography、cffi、pycparser、tomlkit、tkinterdnd2、truststore。原有版权和许可声明保留在交付目录中。

- macOS：CPython 与 Tcl/Tk 许可位于应用的 `Contents/Resources/licenses`；Python 依赖许可位于 `Contents/Resources/*dist-info/licenses`。
- Windows 原生包：CPython 与 Tcl/Tk 许可位于 `_internal/licenses`，Python 依赖许可位于 `_internal/*dist-info/licenses`。
- Windows 便携包：CPython 许可位于 `runtime/LICENSE.txt`，Tcl/Tk 许可位于 `licenses` 和 `runtime/tcl`；Python 依赖许可位于 `runtime/Lib/site-packages/*dist-info/licenses`。
- 原生程序通过 PyInstaller 构建，其引导程序的许可及特殊例外位于对应平台资源目录的 `pyinstaller-*.dist-info/licenses/COPYING.txt`。

Tcl/Tk 许可文本来自官方仓库的 `core-9-0-4` 标签：

- <https://github.com/tcltk/tcl/blob/core-9-0-4/license.terms>
- <https://github.com/tcltk/tk/blob/core-9-0-4/license.terms>

具体授权条款以各组件随附的原始许可文本为准。
