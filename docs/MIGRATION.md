# 纯插件迁移

2.0 将原 QQ 平台定制移入插件的 platform/ 子包，任务与适配器使用插件自己的模块，不再导入旧的 plugins.platforms.qqbot.task_workspaces。注册保留 qqbot 平台标识，继续使用原群/私聊 OpenID 与数据库。

网页来源校验经 register_tool(override=True) 注册，用户通过 allow_tool_override 明确授权；原生内置工具的代码不改动。插件不采用运行时 rebinding 或 monkey patch。

移除旧 gateway 通知路由、base 平台属性、config_defaults 与内置 web_extract 的定制补丁。仅恢复属于该扩展的差异，保留其他改动。通知私聊失败后的行为回到上游原有逻辑；不再承诺绝不回落群聊。

安装 Agent 可根据当前 Hermes 接口做插件内适配，但不得为实现安装偷偷给核心打补丁。需要上游新增接口时应明确说明最低能力要求。无需固定到某一个 commit。

旧代码与插件的备份必须保留在项目外，不进入开源包。数据库保持原位置，不用旧快照覆盖迁移后新产生的数据。移除插件前先处理活跃任务，再停用并重载，平台注册由 Hermes 的注册生命周期恢复。
