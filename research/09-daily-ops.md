# 每日运维操作卡（低 Token 版）· 2026-11-07

> 润行签发。定时任务唤醒后**只做这些**，不要再读 TEAM.md/README/research 全文；细节需要时再查本文件。
> 全局：`cd <项目目录>`

## 1. 三个命令（覆盖 90% 日常）

| 场景 | 命令 | 输出 / 退出码 |
|---|---|---|
| 开盘 | `python3 tools/daily_ops.py open` | 一行状态 + `CMD`（监控启动命令）+ `NEXT`；非交易日打印 `OPEN SKIP` 直接结束 |
| 盘中被叫醒/巡检 | `python3 tools/daily_ops.py check` | 无告警 `alerts=0` exit 0；有告警 exit **10** 并给 `ACTIONS`/`URGENT` |
| 收盘 | `python3 tools/daily_ops.py close` | 写 `reports/watch-<date>.md`、生成次日 plan；非交易日 `CLOSE SKIP` |

配套：`tools/watch_intraday.py`（监控）、`tools/make_plan.py`（生成计划）、`data/plan-<date>.json`（当日规则）、`data/alerts-<date>.jsonl`（告警流水）。

## 2. 开盘（09:20）三步

```bash
python3 tools/daily_ops.py open          # 1) 拿 CMD
# 2) 确认登录态：mcp__phone__open app=com.tdx.AndroidNew → 交易 → 能读到"资产/可用"
#    读不到 → mcp__phone__handoff 请用户登录一次
# 3) 用 bash run_in_background 跑 CMD 里的 watch_intraday 命令；09:31 模拟盘演示（买 601668 100 / 卖 000651 100）
```
回复 ≤3 行：已登录？监控 job_id；模拟结果。

## 3. 告警处理（check exit 10 时）

1. 读 `CHECK` 行拿到 `action`：`sell_ready`/`emergency_sell` → **卖出**；`buy_ready` → **买入**；`risk_off` → 停止买入并评估减仓；`notify` → 只提示。
2. 打开通达信 → 交易 → 买入/卖出 → 按 plan 的价格/数量填单 → **直接提交（当日免密）**。
3. 成交后：查「当日成交」，把成交价/费用/余额写进 `data/account-state.json`。
4. `mcp__phone__notify` 给用户一句结果。
5. 处理完重启监控：`bash run_in_background python3 tools/watch_intraday.py --interval 180 --max-runtime 21600 --plan data/plan-<今天>.json`。
6. 登录失效才需要用户（`handoff` 登录一次）。

## 4. 收盘（15:05）

```bash
python3 tools/daily_ops.py close         # 写日报 + 生成次日计划
```
然后：读通达信持仓/可用/总资产 → 更新 `data/account-state.json` → 若当日有成交，核对费用 → 回一句摘要（≤4 行）。

## 5. Token 纪律（重要）

- 每次唤醒**先跑 daily_ops**，用它的短输出决策；不要重复 `job_list`/`job_output` 全量读，除非 daily_ops 没覆盖。
- 不重读长文档；需要背景时才读 `research/05`（账户）、`research/07`（下单 SOP）、`research/08`（监控细节）。
- 回复 ≤5 行，不写长总结；细节写文件不写对话。
- 非交易日（daily_ops 已自动判断）直接结束，不做任何生成。

## 6. 故障速查

| 症状 | 处理 |
|---|---|
| App 读不到资产页 | 未登录 → handoff 让用户登录一次 |
| watch 进程不在 | 重启命令见 §3.5 |
| 行情接口失败 | 脚本会重试；持续失败等下一轮 |
| 计划文件缺失 | `open` 会自动 `make_plan` 生成；失败才手工处理 |
| 模拟/实盘下单报 `140150000 交易类型未支持` | 非交易时段（周末/盘后）正常现象，交易时段再试 |

**签发：润行（总指挥·操盘手）｜2026-11-07**
