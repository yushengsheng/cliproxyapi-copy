# CLIProxyAPI 配置助手

轻量的本机配置迁移工具，适用于 Linux Manager「一键配置 GPT」生成的 **GPT / Codex 桌面客户端配置**。读取正在使用的线路，在 Windows、macOS 之间通过密码加密的文件或文本迁移。它操作 `.codex` 配置，不会修改普通 ChatGPT 聊天的登录信息。

采用 Python + Tk 原生桌面界面，无 Electron、无后台服务。所有加解密在本机完成，不上传配置。只在点击「测试连接」时向所配置的服务请求 `/v1/models`，不发送对话，不消耗模型推理额度。

## 使用

从 [GitHub Releases](https://github.com/yushengsheng/cliproxyapi-copy/releases) 下载对应系统的成品。macOS 按 Apple Silicon（arm64）或 Intel（x86_64）选择，解压后打开 `.app`；Windows x64 原生包解压完整目录后运行 `CLIProxyAPI-Config.exe`。成品自带 Python 和 Tk，无需安装 Python，不要只复制单个可执行文件。

macOS 最低系统版本见每次 Release 的说明。构建脚本会核对包内原生库，将实际最低系统版本写入应用元数据；当前拖拽库可能要求 macOS 14。macOS 成品采用临时签名，未经 Apple 公证，必要时在系统隐私与安全性设置中允许打开。

另有可在 macOS 组装的 Windows x64 便携包，解压后使用「启动配置助手.cmd」或 `.vbs`，请保留旁边的 `runtime` 目录。此方式的结构检查不代表 Windows 实机验证；GitHub Release 使用 Windows runner 原生构建并运行验证的 `.exe` 包。

### 在源电脑导出

1. 打开程序，在应用列表中点击 GPT / Codex 的图标。程序自动定位配置并读取当前线路，无需选择应用路径或配置目录。
2. 查看地址、默认模型和模型数量。点击「读取本机配置」可刷新。界面不显示 API 密钥。
3. 输入两次加密密码，至少 8 个字符，建议 12 个以上。
4. 点击「加密并保存文件」，自行选择路径，生成 `.cproxy` 文件；或点击「复制加密内容」生成可传输的加密文本。

### 在目标电脑导入

1. 安装好对应桌面客户端，完全退出该客户端，打开本工具。
2. 在应用列表中点击这台电脑上 GPT / Codex 的图标，程序会自动定位配置；无需查找路径。应用安装位置与用户配置位置互相独立，换用户名或移动应用不影响默认配置定位。
3. 在「导入并应用」页选择文件、拖入一个文件，或点击「从剪贴板导入」。支持粘贴加密文本、文件路径，也支持 Finder / Windows 资源管理器中复制的单个文件。
4. 输入密码并点击「解密配置」。成功后可查看线路摘要，必要时点击「测试连接」。
5. 点击「备份并应用」。程序加密备份当前配置，再更新线路、密钥、默认模型和模型目录，保留其他提供商、插件和 MCP 设置。
6. 重新打开桌面客户端，并新建会话使用迁移后的线路。

密码错误、配置损坏或不受支持时，不会写入客户端配置。文件内容或密码变化后，必须重新解密才能应用。加密和联网在后台线程运行，操作中暂不允许关闭窗口。

粘贴导入会把完整密文保存在内存中，界面只显示短预览，避免较大的模型目录在文本框排版时阻塞界面。点击「从剪贴板导入」、在文本框使用系统粘贴菜单或快捷键均采用同一导入流程。

### 恢复

点击「恢复加密备份」，从当前目录下 `cliproxy-config-backups` 选择 `.cproxybackup` 文件，输入**生成该备份时使用的导入密码**。恢复前也请退出客户端。恢复操作会再次备份当前状态，可以撤销恢复。

恢复备份保存的是原文件，仅能在原配置目录恢复；跨电脑迁移使用 `.cproxy` 线路配置。

## 安全和兼容边界

- 迁移文件和恢复备份采用 AES-256-GCM 认证加密，密码使用 scrypt 派生密钥（N=32768，r=8，p=1，约 32 MiB 工作内存），每次使用独立随机盐和 nonce。密码不写入文件，也不保存到程序设置；忘记密码无法恢复。
- 导出的线路文件只包含 API 地址、客户端 API 密钥、默认模型、模型目录及相关模型/传输选项，不包含账号登录 Token、聊天历史、服务器 SSH 密钥、服务器管理密钥。
- 导出文件不能覆盖客户端配置、账号凭据、密钥或模型目录；检查包含自定义密钥/模型路径及带 `..` 的等价路径。
- 密码保护迁移文件与备份。应用后，桌面客户端仍需读取本机 `.key` 凭据文件：macOS 设为仅当前用户可读写；Windows 使用当前用户配置目录继承的访问权限，建议使用默认用户目录。对本机已登录用户的读取不做额外密码锁定。
- macOS 凭据读取使用 `/bin/cat`；Windows 使用系统自带的 Windows PowerShell，通过编码后的固定读取命令支持中文、空格、引号等路径。跨电脑不会复用原电脑路径，也不会执行导入文件携带的命令。
- 要求有效的 HTTPS 接口，地址以 `/v1` 结尾。测试连接不跟随重定向，使用系统证书信任。迁移不会自动更换密钥、刷新服务器订阅或模型目录；服务器变更后请在源电脑重新配置并导出。
- 当前支持 Linux Manager 或本工具生成的 `linux-manager-cliproxyapi` 提供商，协议为 Responses。默认 profile、额外认证方式、符号链接目录会明确拒绝，避免误改配置。
- 自动定位支持当前 `CODEX_HOME`、Linux Manager 已记录的自定义目录及用户主目录下的 `.codex`。程序只读取明确的位置，不遍历硬盘。若客户端用独立启动器设置了仅对它生效的环境变量，本工具需在相同环境中启动才能识别该特殊配置。
- 应用列表只显示可配置的 GPT / Codex。macOS 识别系统及用户「应用程序」目录中的应用，包括一层分组目录；Windows 识别常用安装位置、应用注册信息和 Microsoft Store 安装包。图标读取失败时显示替代图标，配置功能不受影响。
- 多台电脑都必须能访问服务器，且密钥仍有效。工具不安装目标客户端、不改变 Linux 服务，也不改变 ChatGPT 账号权益。
- 写入前保存加密备份，普通写入异常会自动撤回；若进程被强制终止或系统断电，请用备份恢复。应用和恢复时请完全退出目标客户端，并避免另一个程序同时编辑配置。

## 源码运行

需要 **Python 3.11 以上，含 tkinter**。推荐 Python 官方安装包；部分精简 Python 不包含 Tk。运行依赖及其传递依赖的版本固定在 `requirements.txt`。

macOS：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python launch.py
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\pythonw.exe launch.py
```

## 本地构建

PyInstaller 的原生构建须在对应系统执行。构建只收集程序代码与依赖，不读取本机配置。成品自带 Python、Tk，无需目标电脑另外安装 Python。

macOS：

```sh
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python scripts/build.py
```

Windows：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe scripts\build.py
```

输出位于 `dist`。Windows 解压完整目录后运行 `CLIProxyAPI-Config.exe`，不要只复制单独的 exe。macOS 解压后打开 `.app`；本地构建采用临时签名，未经 Apple 公证，若系统阻止打开，在「系统设置 → 隐私与安全性」允许该程序。

另提供 `scripts/build_windows_portable.py`，可以在 macOS 上组装 Windows x64 便携包。它从 Astral 的 Python Build Standalone 官方仓库下载固定版本的 CPython/Tk，核对上游公布的 SHA-256，再从 PyPI 下载固定版本的 Windows 依赖。需要初次联网，之后成品离线运行。此方式不生成自定义 exe 启动器，以 `.cmd` / `.vbs` 启动系统原生 `pythonw.exe`。

重复构建会从已校验的压缩包重新解包运行环境，并清理旧依赖轮子，避免缓存混入旧版本。Windows 启动入口禁用 Python 环境变量和用户级包目录，使用便携包内的依赖。

默认按构建机架构打包。Intel Mac 请在 Intel 环境构建，或用具有完整 universal2 Python、Tk 和所有依赖的环境构建；Windows 推荐 x64。跨系统路径与配置生成均有单元测试，是否经过 Windows 实机验证须以交付时的验证说明为准。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python launch.py --self-check
```

测试覆盖加密往返、密码错误、篡改、文件限制、路径重建、设置保留、加密恢复、写入回滚、Windows 凭据命令、配置变化检测，以及桌面导出/拖放/粘贴/解密/应用流程。测试写入临时目录，不修改真实客户端配置；桌面流程测试需要可用的桌面会话。

`--self-check` 还会在临时目录实际执行导出、导入、备份恢复和本机密钥读取命令，不请求 API。可附加 `--self-check-report 路径` 保存 JSON 结果。

## GitHub 构建与发布

`.github/workflows/release.yml` 在全新 macOS arm64、macOS Intel 和 Windows x64 runner 中安装固定依赖，运行全部源码与桌面流程测试，再原生打包。随后从最终压缩包解压运行，检查内嵌代码、许可证和配置隐私，并执行程序自检。

三个平台全部成功后才发布当前 `VERSION` 对应的 Release，附带平台压缩包、逐包 `.check.json` 验证报告及 `SHA256SUMS`。已存在的 Release 不会自动覆盖，发布下一版需要更新 `cliproxy_config/__init__.py` 中的版本号和 `CHANGELOG.md`。
