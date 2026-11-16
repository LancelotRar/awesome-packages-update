# 04: 投递结果模型与逐项落盘

**What to build:** 每个监控项产出一个显式结果对象，运行摘要、成功/失败/未变化计数与退出码全部从结果派生；每个条目一结算就立即写盘，任务中断、取消或超时都不会让已投递版本在下次运行重发；惰性打开 Telegram 客户端的逻辑只剩一份，投递与告警共用。对外行为与现在一致。

结果类型即摘要单元格文本（来自 spec 中的原型草图，仅保留决策相关部分）：

```python
class OutcomeKind(StrEnum):
    DELIVERED = "✅ delivered"
    UNCHANGED = "➖ unchanged"
    NO_RELEASES = "➖ no releases"
    DISABLED = "⏭ disabled"
    NO_ASSETS = "❌ no assets"
    DETECTION_FAILED = "❌ detection"
    DELIVERY_FAILED = "❌ delivery"

@dataclass(frozen=True)
class Outcome:
    item_id: str
    kind: OutcomeKind
    detail: str = ""
```

**Blocked by:** 01, 02, 03

**Status:** resolved

- [x] 每个监控项按清单顺序产出一个结果对象，摘要、计数、退出码全部由结果列表派生
- [x] 不再存在按 id 的结果字典与并行的失败/投递列表，"未变化"不再由减法推算
- [x] 漏掉结果的启用条目不可能被渲染成"已关闭"；摘要行文本与顺序与现在逐字一致
- [x] 每个条目结算后状态立即持久化；模拟运行中断后，已完成条目的 marker 已在文件中（有测试覆盖）
- [x] 状态文件始终保持完整合法的 JSON；告警在状态安全落盘之后发送，`alerted` 标记可持久化
- [x] 惰性客户端只有一份实现，投递阶段与告警阶段共用；打开失败只尝试一次，且失败原因可解释
- [x] 既定契约不变：连续 3 次失败告警、未配置管理会话只记一条警告、任一条目失败返回退出码 1、未产生投递时不打开客户端、同一客户端复用于所有条目
- [x] 既有主流程测试保持外部视角（退出码、状态文件、摘要、发送记录）并全部通过

## Comments

已在集成分支 `feat/watch-runner-quality` 实现（`4d216b5`）。门禁：`python -m ruff check scripts tests` 与 `python -m pytest tests -q`（144 passed）。
两轴评审后的收口提交：`30e7f60`。最终门禁：`ruff check scripts tests` 通过，`pytest tests -q` 145 passed。
