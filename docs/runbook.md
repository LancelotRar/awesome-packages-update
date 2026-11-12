# 运维手册

日常只需要三件事：看运行、补投、查配置。全部命令在仓库根目录执行。

## 查看一次运行

```bash
# 最近几次运行
gh run list --workflow check-releases.yml --limit 5 \
  --json databaseId,event,status,conclusion,createdAt

# 某次的结论行与失败项
gh run view <run-id> --log | grep -E "run_finished|::error::"
```

`event=schedule` 是定时触发，`workflow_dispatch` 是手动。定时运行会被 GitHub
排队，晚几十分钟到数小时都可能，不算失败。

每行日志的格式是 `时间 级别 [入口 run_id] 事件 字段=值`。`run_id` 由 GitHub 运行 ID
与 attempt 拼成（`gh-<run-id>-<attempt>`），可以据此只取某一次运行：

```bash
gh run view <run-id> --log | grep "gh-<run-id>"
```

需要扫描与重试细节时加 `LOG_LEVEL=DEBUG`；需要机器解析时用 `LOG_FORMAT=json`
（每行一个 JSON 对象，此时不输出 Actions 注解）。

## 查看当前标记

```bash
git pull
python -c "import json; s=json.load(open('data/state.json')); print(len(s)); \
print({k:v['marker'] for k,v in list(s.items())[:5]})"
```

`marker` 是上次成功投递时的版本指纹；`failures` 是连续失败计数，达到 3 次会向
`TG_CHAT_ID_ADMIN` 告警。`marker` 与 `last_checked` 里的时间统一是北京时间
（`+08:00`）；非时间型 marker（`tag` 策略的指纹）保持哈希原样。

## 补投单个条目

单个条目因为登录限流、零命中资产等原因失败后，修完配置再强制投一次：

```bash
gh workflow run check-releases.yml -f project=<id> -f force=true
```

`force=true` 忽略 marker 重投，只影响指定的这一条。不要把 `force=true` 和
`project=all` 一起用——那会把全部条目重发一遍。

## 重跑（Re-run）与重复投递

GitHub 的 Re-run 会沿用触发事件时的 `GITHUB_SHA`。如果按旧 SHA checkout，
`data/state.json` 会退回上一次 attempt 之前的版本，已经投递过的条目会再发一遍
（2026-11-11 的 powershell v7.7.0 与 cmfa v2.11.37 就是这样重复的）。

两个 workflow 的 checkout 都显式写成 `ref: ${{ github.ref_name }}`，重跑时取分支
最新提交，state 不会回退。仍建议重跑前确认 `origin/main` 已有上一次的
`update release check state [skip ci]` 提交；只想补投单条时，优先用
`PROJECT=<id> FORCE=true` 的一次性 dispatch，而不是 Re-run all jobs。

## 重置一个条目的 marker

需要跳过某条目的“当前版本”（例如刚补投过、或切换了版本选择逻辑）时：

```bash
python - <<'PY'
import json, pathlib
p = pathlib.Path("data/state.json")
s = json.loads(p.read_text(encoding="utf-8"))
s["<id>"]["marker"] = "<release 的 updated_at，北京时间 +08:00>"
p.write_text(json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
             encoding="utf-8")
PY
git add data/state.json && git commit -m "chore: 重置 <id> 标记" && git push
```

## 审计资产过滤

上游改名或换格式时，过滤式会静默失配。手动跑一次审计：

```bash
GITHUB_TOKEN=$(gh auth token) python scripts/audit_assets.py
```

输出里每条会给出当前 release 与命中数；`::error::` 表示零命中（事件
`assets_unmatched`）或检测失败（事件 `detection_failed`）。零命中或检测失败时，脚本会
把「哪几个条目、哪个过滤式」推到 `TG_CHAT_ID_ADMIN`，同时 workflow 变红。同样的检查
每周一 11:00（UTC+8）在 `audit-assets` workflow 里自动跑一次。本地跑没配
`TG_BOT_TOKEN` / `TG_API_ID` / `TG_API_HASH` / `TG_CHAT_ID_ADMIN` 时只打印日志，不会
发消息。

## 轮换 Telegram 凭据

`TG_BOT_TOKEN_03BOT` 与 `TG_API_HASH` 如怀疑泄露，先到源头重新生成，再覆盖 secret：

```bash
gh secret set TG_BOT_TOKEN_03BOT --repo LancelotRar/awesome-packages-update
# 交互式粘贴新值；命令与输出都不会回显
```

覆盖后手动跑一次 `project=all` 确认投递正常。`GITHUB_TOKEN` 由 Actions 注入，
不用配置。

## 本地能验证什么

本机到 Telegram 的连通性通常不可用，所以：

- **能本地验证**：清单结构（`load_watchlist`）、单元测试、资产过滤是否命中
  （`audit_assets.py`）、渲染文案（`_build_message`）
- **只能看 Actions**：真实投递、告警发送、定时触发

不要用“本地跑一次”替代真实投递的验证；反过来，也不要为了验证文案就往频道发消息。
