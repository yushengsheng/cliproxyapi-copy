# CLIProxyAPI 配置助手

轻量的原生桌面配置工具：从管理网页登录获取客户端密钥及模型，或导入已有模型节点，勾选后配置 GPT / Codex、Claude 桌面版、pi 或 Hermes。保留 Claude Code 命令行适配，和桌面版分开选择。

当前版本 **0.2.1**。原生安装包由 GitHub 托管的各平台干净环境构建，下载见 [GitHub Releases](https://github.com/yushengsheng/cliproxyapi-copy/releases/latest)。本地审核记录见 [RELEASE_AUDIT.md](RELEASE_AUDIT.md)，最终构建结果以 GitHub Actions 为准。根目录 `.app` 是源码启动器，使用本项目 `.venv`；修改源码后关闭配置助手并重新打开即可，无需重新打包。

界面与导入、预览、结果弹窗共用同一套样式，最小窗口为 960×720。耗时操作显示进度并暂时禁用相关按钮，完成或失败后恢复。

## 启动

原生发布包自带 Python：macOS 解压后打开 `CLIProxyAPI-Config.app`；Windows 完整解压后运行 `CLIProxyAPI-Config.exe`，内置登录需系统 WebView2 Runtime。macOS 最低版本以对应 `.check.json` 为准；安装包使用临时签名，未经 Apple 公证，首次打开可能需要在系统隐私与安全性中允许。以下说明适用于源码启动器。

macOS 双击项目根目录的 **CLIProxyAPI 配置助手.app**。请保留在源码目录，不要单独移走。当前启动器依赖同目录的 `.venv`、`launch.py` 和源码。

首次安装或更新运行依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows 源码首次运行可在 PowerShell 中执行：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动器丢失时运行 `.venv/bin/python scripts/local_app.py`。源码也可用 `.venv/bin/python launch.py` 启动；Windows 可双击项目根目录的 **start_clipproxyapi.vbs**，它会使用 `.venv\Scripts\pythonw.exe` 静默启动，不显示黑色终端窗口；也可手动运行 `.venv\Scripts\pythonw.exe launch.py`。需要 Python 3.11 以上且包含 Tk。启动日志位于 `~/Library/Logs/CLIProxyAPI-Config/local-launcher.log`。

## 项目目录

- `CLIProxyAPI 配置助手.app`、`start_clipproxyapi.vbs`、`launch.py`：当前本地程序入口。
- `cliproxy_config/`：界面、网页登录、模型适配、节点加密和配置备份恢复。
- `scripts/local_app.py`：创建源码启动器；`scripts/build.py` 和 `scripts/verify_release.py`：当前原生包的构建与校验工具。
- `tests/`：当前功能的自动化测试；`third_party_licenses/`：第三方许可。
- `.venv/`：当前程序使用的本地运行环境，需保留。

旧版安装包、跨平台拼装的 Windows 便携包脚本、历史构建日志及调试文件已清理。需要原生包时在对应系统安装 `requirements-build.txt` 并使用现有构建工具；`build/`、`dist/` 仅在构建时生成，可再次清理。已有模型节点是用户数据，按原路径保留。

GitHub 工作流默认只构建和校验。发布需手动运行工作流并明确勾选 `publish`，普通推送不会自动创建发行版。

正式发布要求三个原生目标全部通过：macOS arm64、macOS x86_64 和 Windows x64。每个目标执行全套测试、程序自检、原生构建、最终 ZIP 解压运行及内置网页登录验证；汇总时再次核对版本、架构、源码修订、校验值与全部检查项。开发版本、缺失平台、失败报告或重复版本号都会阻止发布。`scripts/release_manifest.py` 生成发布说明和 `SHA256SUMS`，不执行上传。

## 单页操作

1. 填写 HTTPS 管理后台地址，例如 `https://服务器:端口/management.html#/`。
   部署在反向代理子路径时，填写完整页面路径，例如 `https://服务器/proxy/management.html#/`。
2. 点击「获取全部模型」。默认打开内置登录窗口，登录后自动读取客户端密钥并获取模型，**无需 OpenCLI 或浏览器扩展**。
3. 也可点击旁边的「导入模型」：选择文件、粘贴节点文本，或拖入一个文件。程序自动识别明文与加密格式，加密时才询问密码。「导入模型」窗口也提供网页获取入口。
4. 搜索并逐项勾选模型，选择默认模型；搜索不会清除其他已勾选模型。「全选」与「取消勾选」作用于完整列表。
5. 点击「导出模型」可导出所选模型；勾选「加密导出」使用密码加密，取消则导出明文 JSON。
6. 在「一键导入」旁选择目标软件，确认配置目录。按需选择「备份旧配置」及「加密备份」，点击一键导入。自动跳过不兼容模型，结果窗口逐项列出已导入模型与跳过原因；默认模型不兼容时自动改用一个兼容模型并说明。加密时才设置密码，全部跳过时不写配置、不询问密码或备份路径。
7. 备份、导出都通过原生保存对话框自选文件路径，记住各自上次保存的目录；导出也记住文件名。取消保存或取消输入密码，不写目标配置。

「预览配置」显示同样的筛选结果并隐藏密钥。筛选只作用于目标软件的配置，保留原勾选范围，导出仍包含全部勾选模型。「实测默认模型」会发送一条极短消息，可能消耗模型额度；默认模型不适用时提示重新选择，不会偷偷替换模型调用。获取列表、导入导出均不会主动进行模型推理。

导入前完全退出目标软件，完成后重开并新建会话。旧会话可能保留原模型和上下文状态。

Codex 一键导入前会检查现有 `config.toml`。发现顶层 profile、关键配置项类型异常或 TOML 损坏时，会先修复为可写入的基础配置，再导入所选模型；原文件仍按备份选项保存，写入失败会自动撤回。损坏 TOML 无法安全解析其中的其他设置，修复后需要重新确认 MCP、插件等配置；`auth.json` 和 OAuth 登录凭据不会被修改。

## 浏览器读取

内置窗口使用系统 WebView，和外部浏览器的登录状态分开；首次需要在该窗口登录，之后保留该窗口的登录会话。窗口只捕获所填后台同源的管理配置响应，只将**客户端 API 密钥**交给配置助手，管理密钥和 OAuth Token 留在浏览器中。关闭登录窗口会取消获取。

macOS 也可在唯一的浏览器下拉框中选择 Brave 或 Chrome，直接读取该浏览器中已登录的对应后台标签页；没有对应标签页时打开后台。系统可能要求允许浏览器自动化，并需在浏览器「视图 → 开发者」开启「允许来自 Apple 事件的 JavaScript」。读取受限时自动转到内置窗口，无需手动修改授权设置。旧版保存的 Brave / Chrome 选择会在首次打开新版时默认切换为内置窗口；之后仍可主动选择外部浏览器。已移除「浏览器配置」输入框。

Windows 的网页登录使用内置窗口，需要系统 WebView2 Runtime；原生 Windows 包须在 Windows 验证。Linux 源码运行需要系统 WebKitGTK 或 pywebview 支持的 Qt 后端。本次真实网页登录窗口验证在 macOS 完成。

偏好保存在 `~/.config/cliproxyapi-config/preferences.json`，包含后台地址、浏览器、加密选项及保存路径，不保存 API 密钥或密码。内置浏览器使用系统 WebView 的会话存储；支持自定义存储路径的平台使用同目录 `browser-data/`，macOS WebKit 使用系统管理的 WebKit 存储。会话数据应像其他浏览器的用户数据一样妥善保管。

## 软件适配

| 软件 | 默认配置目录 | 写入内容 |
| --- | --- | --- |
| GPT / Codex | `~/.codex`，兼容 `CODEX_HOME` 和原 Linux Manager 目录记录 | Responses 提供商、密钥文件、所选模型的原生能力目录和默认模型 |
| Claude 桌面版 · Chat + Code | macOS `~/Library/Application Support/Claude-3p`；Windows `%LOCALAPPDATA%\Claude-3p` | 原生配置库、已应用配置、第三方网关模式、所选模型及 Chat / Code 开关 |
| pi | `~/.pi/agent`，兼容 `PI_CODING_AGENT_DIR` | Responses 提供商、所选模型与独立凭据；保留已有账号和默认选择 |
| Hermes | `~/.hermes`，兼容 `HERMES_HOME` | YAML 具名提供商、Chat Completions 接口、所选模型及默认值 |
| Claude Code · 命令行 | `~/.claude`，兼容 `CLAUDE_CONFIG_DIR` | Anthropic 网关、调用密钥、模型列表、默认模型与系列别名 |

保留其他提供商、插件、MCP、权限和终端等无关设置；Hermes 保留 YAML 注释。pi 密钥使用字面值凭据，不解释为环境变量或命令。

### pi

导入写入 pi 实际使用的 `models.json`、`auth.json` 和 `settings.json`，模型来源使用独立的 `cliproxyapi-config`。已安装的 CLIProxyAPI 插件会在启动时重新注册 `cliproxyapi`，独立来源可避免其覆盖本工具导入的模型，同时保留插件、已有登录和其他模型来源。

导入后退出并重新打开 pi，输入 `/model`，搜索 `cliproxyapi-config` 选择模型。已有其他来源的默认模型、思考等级和 OAuth 登录保持不变；只有尚未设置默认选择或原来使用本工具来源时，才更新默认模型。`Ctrl+P` 在原有切换范围中加入新模型；原来没有限制范围时，仍可切换全部已认证来源的模型。继续旧会话时，它可能恢复旧的模型来源，需要先通过 `/model` 切换一次。此配置使用 `/v1/responses`，不依赖插件的登录或模型发现。

重复导入不会反复重写未变化的文件。导入和恢复兼容 pi 的原生登录、设置锁，检测到正在更新时停止并提示重试。同名导入来源若已被 OAuth 登录占用，会停止并提示，不覆盖该凭据。Claude Code 也保留已有模型列表、匹配方式与模型选择器设置。Codex、Claude 和 Hermes 导入仍会切换当前使用的线路；各软件自身的多账号能力不同，保留登录不代表其界面支持同时使用所有线路。

### Claude 桌面版

桌面端和命令行的配置入口不同。桌面端不会因为写入 `~/.claude/settings.json` 就切换网关。现在写入 `Claude-3p/configLibrary/` 下本工具独立的配置，更新 `_meta.json` 的 `appliedId`，并设置第三方模式。其他配置库条目、MCP 和普通设置保留。

适配依据本机 **Claude 3.6556.2** 的配置读取机制及官方第三方网关文档；开启 Chat 和 Code，默认模型放在所选模型列表首位，关闭自动发现以保持勾选范围。桌面端**只接受 Claude 模型，最多 200 个**，不接受 GPT / Gemini 等模型。远程托管配置或组织网关策略可能优先于本地配置，检测到时停止写入并说明原因。

这是桌面端原生第三方模式，会使用独立于 claude.ai 登录模式的界面和会话；不迁移或删除原聊天历史。是否能成功对话取决于服务端 Anthropic Messages 接口、模型、上游授权及额度。已完成临时目录配置与恢复验证，未以用户真实线路完成 Chat / Code 的联网对话验收。

### 模型能力

`/v1/models` 获取完整列表，`/v1/models?client_version=0.153.0` 获取服务器提供的原生能力目录。Codex 保留上下文窗口、思考等级、视觉和工具协议，不统一写成 32K 或清空思考选项；默认思考参数校正为该模型支持的值。迁移文件保留这些元数据与连接选项。

一键导入按以下规则筛选；后台明确声明不支持目标接口或不支持 API 调用时，自动跳过并说明。后台未声明接口时仅能判断配置是否适用，不代表已验证真实调用。

| 目标软件 | 可写入的主对话模型 | 自动跳过 |
| --- | --- | --- |
| GPT / Codex | Responses 对话模型，保留真实上下文、思考与视觉能力 | 缺少可靠原生能力目录、旧版 32K 空思考目录或接口不匹配 |
| Claude 桌面版 | Claude 模型路由，使用 Anthropic Messages | 其他厂商模型、接口不匹配及超过 200 个的部分；优先保留兼容的默认模型 |
| Claude Code 命令行 | 可通过 Anthropic Messages 调用的对话模型 | 明确不支持 Messages 的模型 |
| pi | 可通过 Responses 调用的对话模型，保留提供的视觉输入、上下文与思考能力 | 明确不支持 Responses 的模型 |
| Hermes | 可通过 Chat Completions 调用的对话模型 | 明确不支持 Chat Completions 的模型 |

各目标都会跳过独立图片生成、嵌入、专用音视频等非对话模型，按服务端类型、输出模态、接口和已知专用模型名识别；不会因为名称包含 `image` 或支持图片输入而误拒绝正常对话模型。缺少 Codex 能力目录不妨碍其他目标导入；不会编造上下文或思考参数，pi 缺少上下文信息时仍使用原有保守值。Codex 的独立默认 profile 暂不自动改写。

GPT 可以使用图像生成能力，但独立 `gpt-image-*` 不能直接写成 Codex 的主对话模型。它需要 Images API，或作为受支持 Responses 对话模型的 `image_generation` 工具使用；客户端和上游都需要支持这一调用方式。本工具当前配置的是各软件的主对话模型入口，尚未实现独立图片接口或图片工具配置。支持看图的 GPT / Claude 等对话模型可正常导入。参见 [OpenAI 图像生成](https://developers.openai.com/api/docs/guides/image-generation) 和 [Responses 图像生成工具](https://developers.openai.com/api/docs/guides/tools-image-generation)。

结果中的「已导入」表示已写入配置，不表示已通过联网调用。模型在目录中不代表实际可调用，后台也可能保留已下线或额度不足的模型；可主动使用「实测默认模型」验证所选协议。

## 迁移与备份

- 加密节点：`.cproxy`，沿用 AES-256-GCM 与 scrypt，兼容原加密格式。
- 明文节点：`.json`，与加密文件保存同一份连接、模型目录和选项；**包含完整 API 密钥**。
- 配置备份：`.cproxybackup`，按「加密备份」选项保存。恢复时自动识别格式，仅能恢复到原软件、原目录。恢复前再次自选路径保存当前状态，不能覆盖正在恢复的备份。
- 原版 Claude Code 备份可选择「Claude Code · 命令行」恢复；和桌面端备份分别校验。

pi 恢复仅撤回本工具来源的模型、API 凭据和相关默认选择，保留当前其他账号、提供商和设置；不会把 OAuth 凭据恢复成过期备份，也不会删除备份后新增的登录。其他目标仍按原有方式恢复文件快照。

备份可关闭；无论是否备份，写入异常都会尝试回滚本次改动，遇到并发编辑时不覆盖其他程序的修改。备份和节点导出均不能覆盖目标软件的原生配置或 `auth.json` 账号凭据。加密密码不保存，遗忘后无法恢复。明文导出、明文备份和导入后的原生凭据都应按密钥文件保管；macOS 写入权限为仅当前用户可读写。

「复制接口信息」会复制接口地址、完整密钥和所选模型，请只粘贴到可信的软件。

## 本地验证

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python launch.py --self-check
.venv/bin/python -m pip check
```

测试使用临时配置目录，覆盖模型选择、加密与明文往返、文件及粘贴/拖拽入口、保存路径记忆、原生配置保留、密码错误、取消操作、备份恢复和写入失败回滚。macOS 测试还会打开隔离的本地 WebKit 窗口，以 XHR 和 fetch 验证网页捕获，不连接真实后台。GUI 测试需要桌面会话。

本机已安装 pi 时，额外通过其真实 RPC 接口验证模型加载、指定模型切换和循环切换，并加载同名来源扩展验证共存；覆盖 OAuth 与 API 模型共存、原默认及思考偏好、有限和无限切换范围、重复导入。测试阻断网络，不发送对话。未安装 pi 时该项跳过，不影响配置和备份测试。

相关官方文档：[Claude 桌面第三方网关](https://claude.com/docs/third-party/claude-desktop/gateway)、[桌面配置项](https://claude.com/docs/third-party/claude-desktop/configuration)、[Claude Code 网关](https://code.claude.com/docs/en/llm-gateway-connect)、[Codex 自定义提供商](https://learn.chatgpt.com/docs/config-file/config-advanced)、[pi 自定义模型](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md)、[Hermes 提供商](https://hermes-agent.nousresearch.com/docs/integrations/providers)。
