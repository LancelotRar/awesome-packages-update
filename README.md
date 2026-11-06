# awesome-packages-update

监控一组上游软件仓库的 Release，把新版本及其安装包转发到 Telegram 频道。

单 workflow + 单 job + 清单驱动：新增一个软件只需在 [`projects.yaml`](projects.yaml) 里加一条配置，不用改任何 workflow。

## 工作方式

1. `.github/workflows/check-releases.yml` 每天 07:30（UTC+8）触发，也可以在 Actions 页面手动触发。
2. 检查阶段最多 8 个并发请求 GitHub API；通知阶段逐项串行，避免触发 Telegram 洪泛限制。
3. 每一项检测到更新才投递；投递成功后把标记写回 `data/state.json` 并提交，下一次运行据此判断是否变化。
4. 单项失败不影响其他项；同一项连续失败 3 次才向管理会话告警。

### 检测策略

| 策略 | 标记 | 适用场景 |
| --- | --- | --- |
| `latest` | 最新 Release 的 `updated_at` | 常规发版 |
| `tag` | 指定 tag 下匹配资产的 `(name, size, updated_at)` 指纹 | 长期不换 tag、只原地替换资产的滚动 Release |

#### 按 tag 通配选择

一个仓库同时为多条产品线发 Release 时（例如 `bitwarden/clients` 的 `desktop-*`、`cli-*`、`browser-*`、`web-*`），可以在 `latest` 条目上加可选的 `release_tag_include`（非空字符串列表），只跟随其中一系列：

- 先取仓库的 Release 列表，丢弃 prerelease 和没有 tag 的条目；
- 再用 glob（`*`、`?` 等，不区分大小写）逐条匹配，任一 pattern 命中即可；
- 在命中项中取 `published_at` 最新的一个，读取该 tag 的完整 Release 及其资产；
- 标记仍为该 Release 的 `updated_at`，与不写此字段时相同。

没有任何条目命中时按检测失败处理：本次不投递、标记不推进、下次运行重试，不会静默跳过。该字段只与 `latest` 搭配，写在 `strategy: tag` 条目上会被清单校验拒绝；显式写成 `[]` 同样会被拒绝，省略该字段才表示「不按 tag 过滤」。

```yaml
  - id: bitwarden-win
    repo: bitwarden/clients
    strategy: latest
    release_tag_include: ["desktop-v*"]
    asset_include: "bitwarden+portable+.exe, bitwarden+installer+.exe"
    asset_exclude: "commercial"
    notify:
      title: "bitwarden-win 新版本发布！"
      description: "开源密码管理器的 Windows 桌面客户端"
      group_url: ""
      channel_url: ""
      project_url: "https://github.com/bitwarden/clients"
      tag: "#bitwarden_win"
```

### 资产过滤

`asset_include` / `asset_exclude` 使用同一种语法：

- 逗号分隔 = 任一匹配（OR）
- 加号连接 = 同时匹配（AND）
- `exclude` 优先于 `include`
- 以 `.` 开头的 token 按后缀匹配，其余按不区分大小写的子串匹配

例：`.apk+arm64-v8a, .dmg+arm64` 表示「APK 且文件名含 arm64-v8a」或「DMG 且含 arm64」。

## 新增监控项

在 `projects.yaml` 的 `projects` 下追加一条：

```yaml
  - id: example
    repo: owner/name
    strategy: latest
    asset_include: ".apk"
    asset_exclude: ""
    notify:
      title: "Example 新版本发布！"
      description: "一句话介绍"
      group_url: "https://t.me/example_group"
      channel_url: "https://t.me/example_channel"
      project_url: "https://github.com/owner/name"
      tag: "#example"
```

`id` 只能用小写字母、数字和 `-`，且不可重复。`notify` 下的字段留空时，对应行不会出现在消息里。

`notify.tag` 统一写成 `#` + `id`，并把 `id` 里的 `-` 换成 `_`：Telegram 话题标签只接受字母、数字和下划线，`bitwarden-win` 因此写作 `#bitwarden_win`。

首次接入时建议先按当前版本写入 `data/state.json` 的 `marker`，避免切换后重复推送一次旧版本；或者接受首次运行推送一次当前最新版。

## 仓库 secrets

| Secret | 用途 |
| --- | --- |
| `TG_BOT_TOKEN_03BOT` | Telegram bot token（@BotFather 申请） |
| `TG_CHAT_ID_UPDATE_CHANNEL` | 投递目标 chat id，多个用逗号分隔 |
| `TG_API_ID` / `TG_API_HASH` | Telethon 应用凭据 |
| `TG_CHAT_ID_ADMIN` | 连续失败告警的目标 chat id；未设置时只在日志里告警 |

`GITHUB_TOKEN` 由 Actions 自动注入，用于提升 GitHub API 限额。

## 本地运行

```bash
pip install -r scripts/requirements.txt

# 只检查，不投递
PROJECT=all python scripts/run_checks.py

# 单测
python -m pytest scripts/tests -q
```

本地运行同样需要 `TG_BOT_TOKEN` / `TG_CHAT_ID` / `TG_API_ID` / `TG_API_HASH`；仅做检测时可以只设 `GITHUB_TOKEN`（可选）。

## 目录

```
.github/workflows/check-releases.yml   唯一入口
projects.yaml                          监控清单
data/state.json                        持久化标记
scripts/run_checks.py                  主流程
scripts/watchlist.py                   清单加载与校验
scripts/detector.py                    latest / tag 检测
scripts/state.py                       data/state.json 原子读写
scripts/github_api.py                  GitHub API 客户端
scripts/notifier.py                    Telegram 投递
scripts/tests/                         单元测试
docs/adr/                              架构决策记录
docs/agents/                           agent skill 约定
docs/progress/                         实施记录
```
