# Hermes QQ Plugin

独立的 Hermes QQ 官方机器人插件。通过原生插件接口注册 QQ 平台、任务工具、执行环境和 CLI，**不修改 Hermes 源码，不在运行时替换核心函数**。

## 安装

先克隆本仓库：`git clone https://github.com/tibbar213/hermes-qq-plugin.git`，再把 [INSTALL_PROMPT.md](INSTALL_PROMPT.md) 交给有目标服务器访问权限的 Agent。主要步骤是复制 `plugins/qq-group-tools/` 到当前 profile 的插件目录、启用插件并配置主人身份；没有旧的 `reference/` 覆盖步骤，也不要求固定某个 Hermes commit。

插件使用 `register_platform()` 接管 `qqbot` 平台。原来的 QQ 凭据、群白名单、会话及任务数据可继续使用。网页来源校验通过 Hermes 正式的工具覆盖接口提供，因此需明确配置 `allow_tool_override: true`；这不涉及修改源码或 monkey patch。

## 功能

- 私聊与群聊；@、关键词、引用机器人消息触发；图片、文件、语音和 QQ 原生按钮。
- 主代理聊天、派发与转述；Hermes 原生后台子代理工作时主代理仍可继续聊天。
- 独立任务目录、短标题、追问复用、发起人提示、结果回传及任务结束撤销权限。
- 查询和沙箱任务直接执行；主人发起的宿主机任务直接授权，其他成员的宿主机任务整项私发主人手机审批，不调用 JEV 或审核模型。
- 查询任务能读取本任务与技能资料、发现工具、调用明确放行的 Tavily 查询工具；不自动信任所有 MCP。
- 故障优先私发主人，固定系统提示使用中文。**通知发送失败后的回落遵循原生 Hermes，不再提供绝不回落群聊的特殊保证。**
- 网页提取结果按 URL 对齐，对变更来源验证真实跳转；沙箱网络隔离、执行清理和任务临时目录清理。
- 可选小米 MiMo ASR/TTS 命令桥，模型与 API 渠道继续由用户原有配置管理。

## 目录

| 路径 | 内容 |
| --- | --- |
| `plugins/qq-group-tools/` | 完整插件；`platform/` 为独立 QQ 适配器 |
| `tests/` | 在干净 Hermes 副本运行的行为测试 |
| `scripts/run_tests.py` | 创建隔离源码副本并调用 Hermes 原生测试运行器 |
| `scripts/mimo_voice.py` | 可选语音命令桥 |
| `deployment/` | Linux 执行代理、网络隔离和镜像模板 |
| `examples/` | 配置与人格示例，不含真实凭据 |

## 运行要求

需要支持平台注册、工具覆盖、执行中间件、终端环境提供方和 CLI 注册的 Hermes。最低要求以接口能力检查为准；当前验证的源码基线见 UPSTREAM.json，不代表任意版本均已测试。平台插件需要在 Hermes 主进程模式加载，不支持插件隔离宿主的 platform 注册。

**纯插件不等于无系统依赖。** QQ 收发不需要改 Hermes；沙箱和宿主机执行还依赖 Docker、root-owned 执行代理、受限 sudo 和网络规则，见 [deployment/README.md](deployment/README.md)。已有部署复用这些依赖；新部署必须先审查模板、替换占位符，不能把 root 执行代理挂进容器。

配置见 [examples/config.example.yaml](examples/config.example.yaml)。群号与 group OpenID 不等同。

终端查询命令（输出 JSON，不修改配置或任务记录）：

- `hermes qq status`：配置情况、网关进程和 QQ 连接快照；快照过期时连接状态为 unknown。
- `hermes qq groups`：授权群及曾观察到的群 OpenID，分别标明授权和缓存来源；缓存标签不保证是真实群名。
- `hermes qq tasks --limit 20`：最近任务的发起人、目标、状态及目录，最多 100 条。

清理命令仍为 `hermes qq-task-cleanup --dry-run`，确认后去掉 `--dry-run`；只清理闲置 14 天且非活跃任务的 `.tmp/`、`.cache/`。

## 验证与许可

本次公开版 213 项行为测试通过，覆盖 QQ 消息、按钮、任务权限、通知和来源校验；真实沙箱子代理、主代理独立回答、授权撤销、结果路由及插件网页提取均已验证。范围和限制见 [验证记录](docs/VALIDATION.md) 和 [已知限制](docs/KNOWN_LIMITATIONS.md)。

MIT，包含修改过的 Nous Research QQ 适配器代码，保留原许可证与归属说明。这是独立社区项目。
