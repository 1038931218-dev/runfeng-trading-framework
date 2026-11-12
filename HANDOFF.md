# 运营状态交接（HANDOFF）· 2026-11-07

> 目的：**换新手机/手机丢失**时，任何人或 AI 拿到本仓库 + 本文件，能在 10 分钟内恢复整套系统。
> 本文件不含真实账户信息；真实持仓/资金由用户在新设备上重新录入（见第 4 步）。

## 1. 这套系统是什么

- 一条 A 股流水线：**宏观 → 行业 → 个股 → 组合风控 → 盘中监控 → 执行发令 → 复盘**
- 5 个角色：润行（总指挥/操盘手）、润策（宏观）、润研（行业）、润量（量化）、润控（风控）
- 数据源：Sina/腾讯行情（东财接口在本机不可用），日K 首选腾讯 fqkline，失败自动回落 baostock
- 无第三方依赖（纯 Python 标准库）即可跑；可选库：baostock / empyrical-reloaded / quantstats / arch / TA-Lib / pandas-ta

## 2. 新设备恢复步骤（10 分钟）

```bash
# 1) 克隆
git clone https://github.com/1038931218-dev/runfeng-trading-desk.git   # 私有版（要权限）
# 或克隆公开框架：git clone https://github.com/1038931218-dev/runfeng-trading-framework.git

# 2) 自检（不需要联网）
cd runfeng-trading-desk && python3 tools/selftest.py

# 3) 生成当日要点（需 data/ 快照，或用仓库内示例快照）
python3 tools/daily_brief.py

# 4) 录入自己账户（不要提交回仓库）
cp data/account-state.example.json data/account-state.json   # 填入持仓/资金
cp data/watchlist.json data/watchlist.json                   # 按自己计划改候选与止损

# 5) 生成计划 + 启动监控
python3 tools/make_plan.py
bash tools/watch_loop.sh data/plan-<日期>.json
```

## 3. 让 AI 接管（任意对话型 AI）

把仓库链接给它，附一句：

> 读 `AGENT_GUIDE.md` 与 `HANDOFF.md`，按其中规则接管这套系统；先用 `tools/selftest.py` 自检。

## 4. 需要用户在新设备上重做的三件事

1. 券商 App 登录（每天开盘前一次，登录态下单免密）；
2. 录入真实持仓/资金到 `data/account-state.json`（不提交）；
3. 确认执行分工：**AI 只发令，交易员（如 Mika）负责在通达信下单**；AI 不点买卖。

## 5. 当前运营设计（2026-11-07 定稿）

- 仓位档：防守（股票 30–40% / 现金 60–70%），当前示例 38/62
- 小资金豁免：单票 ≤20% 且首笔 1 手；最多 2 只；现金 ≥60%；单笔亏损 ≤1%；全止损 ≤3%
- 只买沪深主板；科创板/创业板/北交所不可交易；不碰 ST/PE≤0/成交额<2亿
- 止损 = min(买入价−2×ATR20, MA60×0.97)，且 ≥1×GARCH 1 日 VaR95
- 每日节奏：
  - 09:20 开盘值守：生成/读取计划 → 启动 `watch_loop.sh`（3 分钟轮询）
  - 盘中：命中条件 → 告警 → 生成指令 → 发给交易员 → 回报成交 → 记账
  - 15:05 收盘：停监控、汇总告警、写 `reports/watch-<date>.md`、生成次日计划
- 定时任务：2 个（盘中值守、收盘），跑在独立轻会话；**不给交易员加定时任务**
- 执行通道：交易员固定会话（如 Mika「交易系统备份执行员」），禁止新开对话
- 文件队列 RPC（可选）：`tools/mika_bridge.py`，让外部执行器调用本仓库工具（零 token）

## 6. 红线（恢复后必须重新确认）

- 不碰银证转账、融资融券、交易密码/验证码
- 数字必须可追溯；查不到写「未能核实」；禁止编造
- 不构成投资建议，不承诺收益
- 账户/密钥/验证码永不入库（见 `SECURITY.md`）

## 7. 备忘：本机（旧手机）特有、需新设备重配的

- 手机操控/无障碍权限、应用启动管理（手动管理+自启动）、电池不优化
- 通达信 App + 券商登录
- 外部执行器（Mika）及其交接包目录
- GitHub SSH key（若要从新设备推送）

## 8. 手机看板（2026-11-10 新增）

两种入口，任选：
1. **DSH Go 插件（推荐）**：`integrations/dshgo-dashboard/` 是本仓库备份，安装：
   `cp -r integrations/dshgo-dashboard ~/.dsh/dsh-go/plugins/local-runfeng-dash && dsh plugin --profile web add ~/.dsh/dsh-go/plugins/local-runfeng-dash`
   安装后在对话详情页「插件」页签可见「润锋看板」，输入框上方有状态条；面板里可启动/停止监控。
   插件读取本机 `http://127.0.0.1:8799/data.json`（由 `tools/dashboard.py` 提供）。
2. **网页版**：启动 `python3 tools/dashboard.py`，浏览器打开 `http://127.0.0.1:8799/`。
