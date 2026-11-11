# check-release 监控移植到 awesome-packages-update

- 日期：2026-11-06
- 源：`awesome-actions` 分支 `refactor/check-release-runner`（未提交的工作区改动）
- 目标：`LancelotRar/awesome-packages-update`（公开仓库）
- 状态：代码已落盘、单测通过、真实 API 标记核对完成；已提交并推送至 `main`，旧仓库的发布监控已停用；secrets 已配置，首跑、告警实跑与定时运行均已验证（2026-11-07）

---

## 1. 为什么搬

- `awesome-actions` 是私有仓库，Actions 按分钟计费；公开仓库的 standard runner 不消耗付费额度。
- 发布监控与仓库里的其他自动化（CF IP、订阅生成）没有耦合，独立成仓库后清单和调度都更好维护。
- 单 job、清单驱动的结构不变（ADR 0001 的成本前提已不适用，但维护成本理由仍成立，见 ADR 0003）。

## 2. 目录映射

新旧路径一一对应，代码零改写：

| 旧（`awesome-actions/check-release-projects/`） | 新（仓库根目录） |
|---|---|
| `run_checks.py` / `watchlist.py` / `detector.py` / `state.py` / `github_api.py` / `notifier.py` | 同名 |
| `projects.yaml` | `projects.yaml`（2 → 8 条） |
| `data/state.json` | `data/state.json`（2 → 8 条） |
| `requirements.txt` | `requirements.txt` |
| `tests/test_{run_checks,state,watchlist,notifier,github_api}.py` | `tests/` 同名 |
| `.github/workflows/check-releases.yml` | 同名，仅路径调整 |
| `GLOSSARY.md` | 同名 |
| `docs/adr/0001`、`0002` | 同名（原文保留，作为历史记录） |

> 2026-11-06 后续整理：六个模块与单测随后统一移入 `scripts/`，`projects.yaml`、`data/` 仍在仓库根。上表的根目录路径描述的是移植当天的状态。

**未移植**：

- `check_release_telethon.py`、`check_release_module_telethon.py`、`check_release_in_repo_telethon.py`、`persist.py` 及其测试——这些是旧版逐项目 workflow 的脚本，只服务于 `awesome-actions` 里已停用的 workflow。
- `data/last-release-*.txt`——旧标记文件，其值已折算进 `state.json`。
- `github_api.py` 里的 `fetch_file_text` / `fetch_latest_commit` / `list_dir_assets` 属于旧脚本用到的函数，随文件一并带过来了但新流程不调用；已于 2026-11-06 随对应单测一并清理。

## 3. 标记迁移

8 条 `marker` 全部取自旧 `data/last-release-*.txt`（2 条试点来自原 `state.json`），而不是取上游当前值——这样切换后不会重复推送已投递过的版本。

| 项目 | marker 来源 | 值 |
|---|---|---|
| webhtv | 原 state.json | `2026-09-24T05:06:22Z` |
| fongmi-module | 原 state.json（tag 指纹） | `97619b4e…374e` |
| ainanplayer | `last-release-ainanplayer.txt` | `2026-11-03T14:33:47Z` |
| ant-video | `last-release-ant-video.txt` | `2026-09-02T18:19:36Z` |
| bettbox | `last-release-bettbox.txt` | `2026-10-22T07:16:03Z` |
| cmfa | `last-release-cmfa.txt` | `2026-10-21T16:44:10Z` |
| flclash | `last-release-flclash.txt` | `2026-10-24T06:18:34Z` |
| fongmitv | `last-release-fongmi.txt` | `2026-11-01T18:10:16Z` |

> 上表保留迁移当天的原始 UTC 值（`Z`）作为核对证据。2026-11-12 起 `state.json` 的时间字段统一改为北京时间（`+08:00`），旧值在读取时自动换算，见 ADR 0005。

### flclash 的待投递版本（预期行为，不是错误）

上游 FlClash 最新 Release 的 `updated_at` 是 `2026-11-04T07:54:38Z`，但旧标记停在 `2026-10-24T06:18:34Z`。核对 `gh run list` 后确认：旧 workflow 最后一次成功运行是 `2026-11-03T22:29:26Z`，**早于**这次发版，因此该版本从未投递。

结论：保留旧标记，让新仓库首次运行时补投这一次。若把 marker 改成上游当前值，反而会静默丢掉一个版本。

其余 7 项的旧标记与上游当前值一致，说明没有待投递版本。

## 4. 验证证据

| 检查 | 命令 | 结果 |
|---|---|---|
| 单元测试 | `python -m pytest tests -q` | **51 passed** |
| 清单校验 | `load_watchlist('projects.yaml')` | 8 条，全部通过严格校验 |
| 检测等价性 | 对 8 项调用真实 GitHub API | 7 项与旧标记逐字节相同；flclash 差异见上，属待投递 |
| 资产过滤 | fongmi-module tag 指纹 | 10/13 匹配，`thunder` 已排除，与旧值一致 |
| workflow YAML | `yaml.safe_load` | 可解析；cron `30 7 * * *` / `Asia/Shanghai` |

## 5. 后续步骤

| 步骤 | 状态 | 内容 |
|---|---|---|
| 1 | 已完成 | 5 个 secret 已于 2026-11-06 配置（`gh secret list` 可见）。 |
| 2 | 已完成 | 手动 `project=all` 首跑 42201966938：`delivered=1`（flclash）、`unchanged=7`、`failed=0`。 |
| 3 | 已完成 | 旧仓库的发布监控已停用并移除，不再有第二套系统重复推送。 |
| 4 | 已完成 | 2026-11-07 定时运行 42331794872：`delivered=2`（两条均为上游新版本）、`unchanged=33`、`failed=0`；`last_checked` 已前进。触发时刻比 cron 计划晚约 3.5 小时，属 GitHub 调度延迟。 |

> 收尾已完成。期间还做了两轮扩展：导入 `git_softs_download` 清单（8 → 46 条，随后按维护者取舍裁剪到 35 条，见 spec #8），以及首跑暴露的缺陷修复（Telegram 登录限流、资产列表截断、预发布混入、零命中静默成功）。

## 6. 风险与待决

1. **公开仓库的内容**：`projects.yaml` 含 Telegram 群组/频道链接，`docs/` 与运行日志公开。不含任何密钥。
2. **secrets**：已完成——5 个 secret 已配置，三类运行（手动首跑、告警实跑、定时运行）均用到。
3. **告警链路**：已完成——2026-11-06 在 `probe/alert-test` 分支实跑，`alerted` 标记生效（run 42203848750）。
4. **旧脚本退役**：已完成——旧仓库的发布监控脚本与旧标记文件已随该监控一并移除（据维护者告知；本次按约定未再访问该仓库核验）。
5. **首次补投**：已完成——flclash 的 11-04 版本已在首跑投递。
