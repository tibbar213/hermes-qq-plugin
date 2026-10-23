# 给安装 Agent 的提示词

请安装当前项目，或把我的旧 QQ 定制部署迁移为纯插件。仓库地址或项目目录由我提供，不猜地址。先阅读 README、docs/MIGRATION.md、deployment/README.md、UPSTREAM.json 和目标 Hermes 的 AGENTS.md。

先核对目标机器、运行用户、实际 Hermes 源码、profile/HERMES_HOME、版本、插件目录和服务；复用现有的模型、QQ 凭据和白名单。只需要收集缺少的主人 OpenID、允许的群/私聊 OpenID、关键词及可选模型/MCP/语音设置。凭据用受限文件或隐藏输入，不让用户贴到聊天里，不打印整份配置或原始日志。

## 必须遵循的安装方式

1. 检查 Hermes 是否提供 register_platform、register_tool 的 override 参数、register_middleware、register_terminal_environment_provider 和 register_cli_command。检查插件主进程模式以及原生后台子代理、审批和会话能力。不能为绕过不兼容而运行时修改核心函数或源文件；缺少接口时给出具体升级要求，不强制降级到参考 commit。
2. 备份已有插件、配置及一致性任务数据库。把本项目 plugins/qq-group-tools/ 安装到当前 profile 的 plugins/qq-group-tools/，启用插件。已存在同名定制时先比较最新功能，不能直接覆盖别人的新增功能。
3. 配置 plugins.entries.qq-group-tools.settings.owner_id 为我的真实 QQ OpenID，并明确设置该插件 allow_tool_override: true，以启用正式注册的 web_extract 覆盖。默认 jev_auto_approve: false，使用普通模型；只有我选择时才启用 JEV。没有有效审核结果时转人工，不能默认通过。
4. 按 examples 合并 QQ 和工具集配置，保留原 model、MCP 与 voice。不要恢复作者的服务器域名、Key、OpenID、记忆或日志，也不要另造 API 优先级。已有群与会话沿用 qqbot 平台名。
5. 若需要沙箱/宿主执行，先检查现有 broker、镜像、网络规则、受限 sudo 与清理服务。新安装时审查 deployment 模板，按真实 UID、任务根目录、技能路径和构建后的不可变镜像 ID 替换占位符；校验 root 所有权、visudo 和网络冲突。不要提供无限 sudo，不要把 Docker socket、宿主机凭据或 broker 暴露给沙箱。依赖未就绪时明确提示，不回退到宿主机执行。
6. 可选语音使用 scripts/mimo_voice.py，通过 Hermes command provider 和 env_passthrough 传 MIMO_API_KEY；转换格式需要 ffmpeg。Tavily 只放行明确的查询工具，其他 MCP 单独判断。
7. 不保留“私信失败绝不回落群聊”的自定义核心补丁，通知遵循原生 Hermes。优先私聊和中文故障提示仍在插件里。

## 旧部署迁移

先确认旧源码改动的归属，只恢复已迁入本插件的改动；不能一条 git reset 丢掉其他工作。保留 QQ SQLite、会话、任务目录和当前模型配置。恢复前后做文件级备份和差异检查。临时目录定时器应调用原生插件命令 `hermes qq-task-cleanup`，不再导入旧源码中的自定义模块。无活跃任务时才重载。

## 验证与交付

先按 scripts/run_tests.py 的说明在隔离源码副本运行测试；测试只使用模拟身份和临时 HERMES_HOME。不要在生产源码里复制测试 fixture。可选 JEV 提供方缺失时明确记录跳过范围。

实际验证插件接管 qqbot 平台、原生 web_extract 覆盖、一次真实文件读取与 MCP 查询；验证后台子代理、权限撤销和正确成员/会话回传。查询任务不能越界或写入，未知 MCP 不能自动放行。确认清理 dry-run 不触碰交付文件或活跃任务。

真实 QQ 消息测试让我发消息，或先获得我对具体测试消息的授权。确认 @、关键词、引用、选择按钮和手机审批。主代理只需开工回应一次，完成后自然转述，不刷持续进度消息。

最终报告实际通过项、未验证项、插件与配置位置、系统依赖、备份与回滚步骤。安装成功必须以运行证据为准，不能把复制文件或配置成功当作完成。发布仓库不属于安装授权。
