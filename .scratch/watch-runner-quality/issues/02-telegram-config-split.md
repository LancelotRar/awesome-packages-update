# 02: 拆分 Telegram 连接配置与消息文案

**What to build:** 通知函数直接接收清单里的文案对象，连接配置只承载机器人凭据与目标 chat；每日检查与每周审计共用同一个环境变量工厂；配置缺失或非法时只留下一条 `config_error` 事件并返回退出码 1，而不是在投递或告警中途硬退出；纯文本通知复用已有的逐 chat 发送实现。

**Blocked by:** 01

**Status:** resolved

- [x] 连接配置只包含 bot token、chat id 列表、api id、api hash，不再携带任何文案字段
- [x] 通知函数直接接收清单文案对象，调用方不再逐字段搬运；消息构造参数表随之收缩
- [x] 每日检查与每周审计通过同一个工厂读取环境变量构造连接配置
- [x] 缺少必需变量或 api id 非数字时：记录一条 `config_error`，入口返回 1，且不会在告警阶段硬退出进程
- [x] 纯文本通知复用同一份"逐 chat 发送 + FloodWait + 部分失败"实现，不再复制循环
- [x] 既有对外契约不变：通知文案逐字一致、告警文案一致、每个 chat 各发一次、部分失败返回 False；既有测试无需改断言即可通过
- [x] 缺 secret / 非法 api id 两种配置错误均有测试覆盖，且断言退出码与日志事件

## Comments

已在集成分支 `feat/watch-runner-quality` 实现（`d2c7eaf`）。门禁：`python -m ruff check scripts tests` 与 `python -m pytest tests -q`（144 passed）。
两轴评审后的收口提交：`30e7f60`。最终门禁：`ruff check scripts tests` 通过，`pytest tests -q` 145 passed。
