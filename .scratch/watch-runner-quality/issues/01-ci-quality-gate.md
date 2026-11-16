# 01: 恢复 push/PR 质量门禁（ruff + pytest）

**What to build:** 任意一次 push 或 pull request 都会自动跑静态检查与全部单测，任一失败即红；本地从仓库根目录用同一条命令即可复现同样的结果；测试文件的导入路径由项目配置提供，新增测试不再需要复制路径拼接样板。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] `push` 与 `pull_request` 都触发 CI，且 ruff / pytest 使用钉死的版本
- [x] CI 依次执行 lint（覆盖源码与测试两个目录）与全部单测，任一失败即红
- [x] 测试目录下的导入不依赖逐文件路径拼接；新增一个测试文件无需再复制样板
- [x] 本地命令与 CI 的 lint 范围、测试入口完全一致，且从仓库根目录直接可用
- [x] README 与 AGENTS.md 关于测试位置、测试命令、导入样板的描述与实际一致
- [x] 发布 workflow 保持只做检测、投递、提交状态，不再兼职跑测试
- [ ] 证明：在临时分支推一次，Actions 页面两条检查均通过；临时引入一个 lint 违规后 CI 变红（随后恢复）
  - 未执行：本仓库规则不允许未获授权的推送，需人工在 Actions 上确认

## Comments

已在集成分支 `feat/watch-runner-quality` 实现（`40b054f`）。门禁：`python -m ruff check scripts tests` 与 `python -m pytest tests -q`（144 passed）。

验收项按代码与测试证据勾选；唯一未完成的是在 Actions 上实际推送验证。
两轴评审后的收口提交：`30e7f60`。最终门禁：`ruff check scripts tests` 通过，`pytest tests -q` 145 passed。
