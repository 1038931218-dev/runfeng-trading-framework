# 团队技能包调研（每个角色一页）· 2026-11-07

> 发起：用户「搜索一下网上你们每个角色相关技能包」｜执行：**润策 / 润研 / 润量 / 润控** 分头调研 + **润行** 汇总签发
> 环境边界：Android proot / Ubuntu 24.04 / **aarch64** / Python 3.12.15 / 无 GPU / 东财 push2 不可用 / 腾讯 fqkline 返回 501
> **可信度分级**：**A**＝本机实际安装跑通；**B**＝仓库/PyPI 元数据经 GitHub API 或 PyPI JSON 核实存在；**C**＝仅搜索结果，正文未核验（未见 = 未核实，不是不存在）
> 本文件为调研记录，**不构成投资建议**；第三方 skill/MCP 可能包含任意代码与数据外发，接入前须审阅。

---

## 0. 一页结论

1. **必装 3 个、全是免费无 key**：`baostock`（补腾讯 fqkline 501 的洞，实测能取 2026-11-06 前复权日线）、`akshare`（补宏观/两融/利率/全A估值历史分位/财务，`tools/market.py` 只有行情源）、`empyrical-reloaded + quantstats + arch`（风控指标与 VaR 自动化，6 个包实测跑通）。
2. **交易执行这一格要泼冷水**：手机上这台**通达信没有可编程下单接口**；pytdx 只能读行情且已 8/8 连不上；easytrader 只支持 PC 端同花顺/miniqmt/雪球，**不能接管手机通达信**。A股程序化委托必须走券商柜台（QMT/Ptrade，需开户+权限）。所以「直接超控下单」只能走**无障碍 UI 操作 + 用户密码/指纹确认**，这是现状，不是技能包能解决的。
3. **明确不建议**：`qlib`（无 aarch64 wheel、依赖 20+、无 GPU 环境性价比极低）、`pytdx`（已归档、连接全失败、无交易 API）、`RiskModels`（付费商业模型）、`nautilus_trader`（170MB、手机过重）。
4. **skill 生态鱼龙混杂**：anthropics/skills 是规范基座但没有现成「A股投研」skill；社区合集（awesome-*、boutique-skills、awesome-stock-skills）star 少、质量互抄，**只读不跑**；第三方 MCP 在上线前必须审代码。

---

## 1. 润策（宏观与政策）

| 名称 | 类型 | 链接 | 级 | 用途/接入点 | 难度 | 费用 |
|---|---|---|---|---|---|---|
| AKShare | Python 库 | https://github.com/akfamily/akshare | B | 宏观 GDP/CPI/PPI/PMI/M2/社融、Shibor/LPR、国债收益率、两融、新闻联播文字稿 → 补 `market.py` 没有的宏观层 | 低 | 免费无 key |
| Tushare Pro | 数据 API | https://tushare.pro/document/2 | B | 国家政策库、央行货政报告、新闻联播稿、两融明细、国债/美债收益率、经济数据发布日程、解禁 | 中 | **需 token，积分为主（2000 积分≈200 元/年；公告 1000/年、研报 500/年）** |
| FinClaw `akshare-finance` | Agent Skill | https://github.com/aifinlab/FinClaw/blob/main/skills/akshare-finance/SKILL.md | B | 已把 `macro_china_cpi/pmi/m2` 等取数函数写死的 SKILL.md，可直接改写成本团队 skill | 低 | 免费 |
| akshare-one-mcp / `china-stock-mcp` | MCP Server | https://github.com/zwldarren/akshare-one-mcp ｜PyPI `china-stock-mcp` | B | `get_macro_data`/`get_news_data`/限售解禁，Agent 直接调工具 | 中 | 免费 |
| fredapi | Python 库 | https://github.com/mortada/fredapi | B | 美债 10Y、非农、CPI 自动化（本轮手工查的海外数据） | 低 | **需免费 key** |
| SnowNLP | 情绪库 | https://github.com/isnowfy/snownlp | B | 政策/新闻正负面打分；**2020 年后未更新，仅粗筛** | 低 | 免费 |
| OpenBB | 投研平台 | https://github.com/openbq-org/OpenBB | B | 海外宏观/固收面板；**A股覆盖弱**，边际价值低 | 中高 | 部分源付费 |
| anthropics/skills | Agent Skills 基座 | https://github.com/anthropics/skills | B | SKILL.md 规范与官方示例，**无现成宏观 skill** | 低 | 免费 |

> 未能核实：美林时钟/流动性框架**没有**高质量开源实现（GitHub 仅 2 个低星无 license 仓库）→ 建议自写 20 行脚本用 akshare 数据算，别引入烂尾项目。
> 政策文件解析：未找到成熟专用库，gov.cn 无公开 API；只能靠 tushare 政策库或自抓网页。

## 2. 润研（行业与基本面）

| 名称 | 类型 | 链接 | 级 | 用途/接入点 | 难度 | 费用 |
|---|---|---|---|---|---|---|
| AKShare `stock_value_em` / `stock_a_ttm_lyr` / `stock_a_all_pb` | Python 库 | https://github.com/akfamily/akshare ｜文档 https://akshare.akfamily.xyz/data/stock/stock_valuation.html | B | **补缺口①行业历史估值分位**：个股全历史 PE(TTM)/PB、全A PE/PB 中位数历史自带 `quantileInAllHistory` 分位字段 | 低 | 免费无 key |
| Tushare `daily_basic` / `fina_indicator` / `income` | 数据 API | https://tushare.pro/document/2 | B | **补缺口②个股财务增速**：营收/净利同比、PE/PB/股息率历史、申万行业成分 | 中 | 需 token；2000 积分 200 元/年 |
| pdfplumber | Python 库 | https://github.com/jsvine/pdfplumber | B | 财报/公告 PDF 文本+表格提取（纯 Python） | 低 | 免费 |
| camelot-py | Python 库 | https://github.com/camelot-dev/camelot | B | 三大报表表格抽取（lattice/stream），需 ghostscript/opencv | 中 | 免费 |
| 巨潮资讯（cninfo） | 官方数据源 | http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice | B | A股公告/财报 PDF 原件（权威、免 key）；查询接口有反爬 | 中 | 免费 |
| AKShare 官方 MCP | MCP Server | https://akshare.akfamily.xyz/mcp.html | C | 本地起 MCP 让 Agent 调 akshare 全量接口（抓取两次 SSL 超时，正文未核） | 低-中 | 免费 |
| Tushare 官方 MCP | MCP Server | https://tushare.pro/document/1?doc_id=463 | C | tushare 数据的 MCP 封装（页面确认存在，正文未细读） | 中 | 需 token |
| anthropics/skills | Agent Skills | https://github.com/anthropics/skills | B | 改写「行业估值分位」「财报增速解析」两个团队 skill 的模板 | 低 | 免费（license 字段为空，商用需确认） |

## 3. 润量（技术面与量化）

| 名称 | 类型 | 链接 | 级 | 用途/接入点 | 难度 | 费用 |
|---|---|---|---|---|---|---|
| **baostock 0.9.4** | 数据接口 | http://www.baostock.com | **A** | **实测可用**：login→取 sh.600519 2026-10-28~11-06 前复权日线（11-06 收盘 1453.00 与快照一致）→ 直接补 fqkline 501 | 低 | 免费无 key |
| AKShare 1.20.35 | 数据接口 | https://github.com/akfamily/akshare | B | 分钟线/财务/行业/北向/期货；**多数接口走东财，本机东财被断，需挑新浪/腾讯源** | 低-中 | 免费 |
| AKTools（akshare 官方 MCP） | MCP Server | https://github.com/akfamily/aktools ｜文档 https://akshare.akfamily.xyz/mcp.html | B | **本次唯一实锤的量化类 MCP**，`uvx --from aktools aktools-mcp` 即起 | 低 | 免费 |
| TA-Lib 0.8.3 | 指标库 | https://github.com/ta-lib/ta-lib-python | B | 150+ 技术指标，增强 `screen_tech.py`；官方 aarch64/py3.12 wheel | 低 | BSD 免费 |
| pandas-ta 0.4.71b0 | 指标库 | https://github.com/twopirllc/pandas-ta | B | pandas 扩展指标；0.4.x 仍 beta、与 0.3 API 不兼容 | 低 | 免费 |
| alphalens-reloaded 0.5.1 | 因子分析 | https://github.com/stefan-jansen/alphalens-reloaded | B | 对 mom20/vol20/dd250 做 IC/分位/换手分析，适配 782 只样本 | 低-中 | Apache 免费 |
| backtrader 1.9.78.123 | 回测 | https://github.com/mementum/backtrader | B | 给条件单/止损做历史回测；2023 后低维护，只适合简单回测 | 低 | 免费 |
| pytdx 1.72 | 数据协议 | https://github.com/rainx/pytdx | **A（不可用）** | 行情客户端、**无交易 API**；GitHub 2020-04 已 archive；本机 8/8 行情主站 connect 全失败 | 低 | 免费 |
| qlib 0.9.7 | 量化平台 | https://github.com/microsoft/qlib | **A（装不上）** | 18 个 wheel 无 linux-aarch64、无 sdist；依赖 20+、GB 级、无 GPU；**不建议** | — | 免费 |

## 4. 润控（风控与组合）

| 名称 | 类型 | 链接 | 级 | 用途/接入点 | 难度 | 费用 |
|---|---|---|---|---|---|---|
| **empyrical-reloaded 0.7.2** | 风险指标库 | https://github.com/stefan-jansen/empyrical-reloaded | **A** | 一把算 MaxDD/VaR/CVaR/Sortino/Calmar；本组合复算：年化波动 5.19%、MaxDD -5.5%、日 VaR95 -0.55% | 低 | 免费 |
| **quantstats 0.0.86** | 绩效报表 | https://github.com/ranaroussi/quantstats | **A** | 日报自动生成回撤/VaR/压力期切片 | 低 | 免费 |
| **arch 8.0.0** | 波动预测 | https://github.com/bashtage/arch | **A** | GARCH(1,1)/EWMA 条件波动替代 ATR20；实测示例股票 条件日波动 2.52%、**1 日 VaR95 -4.15%（对应 3.106 元）** | 低 | 免费 |
| Riskfolio-Lib 7.4.1 | 组合优化 | https://github.com/dcajasn/Riskfolio-Lib | **A** | CVaR/CDaR/EVaR/MDD 优化；可替代自造「相关性趋同法」做压力口径 | 中 | 免费 |
| cvxpy 1.9.3 | 求解器 | https://github.com/cvxpy/cvxpy | **A** | 上面两个的凸优化内核，aarch64 cp312 wheel 可直接装 | 低 | 免费 |
| PyPortfolioOpt 1.6.0 | 组合优化 | https://github.com/robertmartin8/PyPortfolioOpt | **A（HRP 有 bug）** | 最小方差/风险平价；**HRP 在当前 scipy 1.18 下报错**，需锁 scipy<1.15 | 低 | 免费 |
| okama-mcp | MCP Server | https://github.com/mbk-dev/okama-mcp | C | 组合指标/有效前沿 MCP；DSH 接入未实测 | 中 | 免费 |
| RiskModels MCP | 商业风险模型 | https://riskmodels.app/ | B | 对标 Barra/Axioma；**付费需 key，对 5044 元账户不划算** | 高 | 付费 |

> 补：**pre-trade risk check** 没有现成轻量库可直接用；`nautilus_trader` 有 RiskEngine 字段清单（max_notional/max_order_rate/trading_state），但 170MB 过重——**照字段自己写 30 行更实际**。
> ⚠️ 反向提醒（润控实测）：用户保留的万科 **3.16 止损距现价仅 -2.47%，落在 1 日 VaR95（-4.15%）之内**，属「正常波动就可能被打掉」；若不想被噪声止损，3.10（≈-4.3%）更符合波动分布。此前「越紧越安全」的建议据此修正。

## 5. 润行（交易执行与通用能力）

| 名称 | 类型 | 链接 | 级 | 结论 |
|---|---|---|---|---|
| easytrader | 交易执行库 | https://github.com/shidenggui/easytrader | B | 支持同花顺客户端/miniqmt/雪球，**PC 端**；**不能接管手机通达信**；2026-10 仍有 push |
| pytdx | 行情库 | https://github.com/rainx/pytdx | A（不可用） | 只能读行情、已归档、连接失败、无交易 API |
| anthropics/skills | Agent Skills 基座 | https://github.com/anthropics/skills | B | 188,063★；无金融现成 skill，仅规范与示例 |
| awesome-stock-skills | skill 合集 | https://github.com/samyakjain0606/awesome-stock-skills | B | 印度股市 Claude Code skills 合集，26★，参考结构可以 |
| boutique-skills | skill 合集 | https://github.com/leecyno1/boutique-skills | C | 按岗位 curated skills（含 finance-services），来源审计可信度未知 |
| open-stocks-mcp | MCP Server | https://pypi.org/project/open-stocks-mcp/ | B | 基于 Robin Stocks 的美股 MCP，A股不适用 |

**结论：手机通达信无公开下单 API。** 现状可行的「代下单」只有两条：
1. **无障碍 UI 操作**（本会话已验证：能读持仓/可用/买入卖出页、能填单），最后一步密码/指纹必须用户本人；这是「人机双签」，不是程序化交易。
2. **券商柜台 QMT/Ptrade**（需开户、资金门槛、单独权限）——不是装个库就能解决。

## 6. 落地优先级（润行定）

| 优先级 | 动作 | 解决什么 | 预计成本 |
|---|---|---|---|
| **P0** | 装 `baostock`，给 `tools/screen_tech.py` 加数据源适配层（约 30 行）：fqkline 501 时自动回落 baostock 前复权日线 | 个股 K 线抓取可用性 | 5 分钟 |
| **P0** | 装 `empyrical-reloaded` + `quantstats` + `arch`，日报自动出 MaxDD/VaR95/CVaR95/GARCH 条件波动 | 风控指标从手算变自动、止损位可按波动分布校验 | 3 分钟 |
| **P1** | 装 `akshare`，新增 `tools/macro.py`（宏观/两融/利率/全A估值分位） | 补本轮「两融当日值/社融发布日/历史估值分位/个股财务增速」四个缺口 | 10 分钟（依赖较重） |
| **P1** | 装 `AKTools` MCP（`uvx --from aktools aktools-mcp`）供 Agent 直接查数据 | 减少写代码取数 | 5 分钟 |
| **P2** | `Riskfolio-Lib` 对 12 只组合做 CVaR 权重对照；`TA-Lib` 增强指标 | 组合与因子层升级 | 半小时 |
| **不接** | qlib、pytdx、RiskModels、nautilus_trader、vnpy、rqalpha（数据依赖账号） | — | — |

## 7. 风险与边界

1. **没有「能自动下单」的合规开源技能包**；任何声称能接管券商/通达信下单的第三方客户端，都存在账号密码外泄与违规风险，**一律不装**。
2. skill/MCP 是「能执行代码的第三方内容」：只装 PyPI/GitHub 官方包并锁版本；社区合集（awesome-*、boutique-*）**只读不跑**。
3. 许可证：anthropics/skills 的 license 字段为空，商用前确认；Riskfolio-Lib BSD-3、PyPortfolioOpt MIT、TA-Lib BSD、nautilus LGPL-3。
4. 数据源可用性会变（本机东财 push2 已断、腾讯 fqkline 501）——所有接入都要保留「主源+备源+缓存」三层，不能单点依赖。
5. 本文件所有链接均经 GitHub API / PyPI JSON / 页面抓取核实「存在」；标 C 的项正文未核验，**标注不等于推荐**。

**签发：润行（总指挥·操盘手）｜2026-11-07**
