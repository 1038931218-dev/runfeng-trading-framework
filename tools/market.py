#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股行情数据层（团队共用，免依赖，仅标准库）。

数据源（2026-11 实测可用）:
  * 快照/全市场排行: hq.sinajs.cn 与 vip.stock.finance.sina.com.cn
  * 日K(前复权): web.ifzq.gtimg.cn；失效时自动回落 baostock（2026-11-06 起 fqkline 返回 HTTP 501）
  * 东方财富 push2 在本机被断开，不要用。

用法:
  python3 tools/market.py snapshot          # 全市场快照 -> data/snapshot-YYYY-MM-DD.json
  python3 tools/market.py breadth data/snapshot-YYYY-MM-DD.json
  python3 tools/market.py index             # 主要指数
  python3 tools/market.py kline sh600519 250
  python3 tools/market.py quote sh600519,sz000001

K 线环境变量（只影响 kline）:
  KLINE_SOURCE=baostock   强制走 baostock 备源（跳过 gtimg），便于测试/降级
  KLINE_SOURCE=gtimg      只用 gtimg 主源（不回退），便于对比
  未设置 = 先 gtimg，异常时自动回落 baostock
"""
import atexit, datetime, json, os, sys, time, gzip, random, threading
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDR = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

INDICES = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000300": "沪深300", "sh000905": "中证500",
    "sh000852": "中证1000", "sz399905": "中证500(深)", "sh000016": "上证50",
}


def _get(url, timeout=20, retries=3, headers=None):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or HDR)
            r = urllib.request.urlopen(req, timeout=timeout)
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("gbk", "ignore")
        except Exception as e:  # noqa
            last = e
            time.sleep(0.4 + random.random() * 0.6 * (i + 1))
    raise last


def quote(codes):
    """快照: codes = ['sh600519', ...] -> {code: {...}}"""
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    txt = _get(url)
    out = {}
    for line in txt.strip().split("\n"):
        if '="' not in line:
            continue
        head, body = line.split('="', 1)
        code = head.split("hq_str_")[-1].strip()
        f = body.rstrip('";').split(",")
        if len(f) < 32:
            continue
        out[code] = {
            "name": f[0], "open": float(f[1] or 0), "prev_close": float(f[2] or 0),
            "price": float(f[3] or 0), "high": float(f[4] or 0), "low": float(f[5] or 0),
            "volume": float(f[8] or 0), "amount": float(f[9] or 0),
            "date": f[30], "time": f[31],
        }
        o = out[code]
        o["pct"] = round((o["price"] / o["prev_close"] - 1) * 100, 2) if o["prev_close"] else 0.0
    # 备用源：Sina 缺失或价格为 0（集合竞价/异常）时用腾讯补
    bad = [c for c in codes if c not in out or not out[c].get("price")]
    if bad:
        try:
            alt = _quote_tencent(bad)
            for c, v in alt.items():
                if v.get("price"):
                    out[c] = v
        except Exception:  # noqa
            pass
    return out




def _quote_tencent(codes):
    """腾讯实时行情（qt.gtimg.cn）— Sina 返回 0/缺失时的备用源。"""
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    txt = _get(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    out = {}
    for line in txt.strip().split("\n"):
        if '="' not in line:
            continue
        head, body = line.split('="', 1)
        code = head.split("v_")[-1].strip()
        f = body.rstrip('";').split("~")
        if len(f) < 40:
            continue
        try:
            price = float(f[3] or 0)
            prev = float(f[4] or 0)
            out[code] = {
                "name": f[1], "open": float(f[5] or 0), "prev_close": prev,
                "price": price, "high": float(f[33] or 0), "low": float(f[34] or 0),
                "volume": float(f[6] or 0) * 100, "amount": float(f[37] or 0) * 10000,
                "date": f[30][:4] + "-" + f[30][4:6] + "-" + f[30][6:8] if len(f[30]) >= 8 else "",
                "time": f[30][8:10] + ":" + f[30][10:12] + ":" + f[30][12:14] if len(f[30]) >= 14 else "",
            }
            o = out[code]
            o["pct"] = round((price / prev - 1) * 100, 2) if prev else 0.0
        except (ValueError, IndexError):
            continue
    return out

def rank(page=1, num=100, sort="changepercent", asc=0, node="hs_a"):
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"Market_Center.getHQNodeData?page={page}&num={num}&sort={sort}"
           f"&asc={asc}&node={node}&symbol=&_s_r_a=page")
    txt = _get(url)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        import re
        return json.loads(re.sub(r"([{,])(\w+):", r'\1"\2":', txt))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def snapshot(workers=8, max_pages=70):
    """全市场逐页抓取（num=100）。返回 list[dict]，字段已归一。"""
    pages = {}

    def one(p):
        rows = rank(page=p, num=100)
        pages[p] = rows
        return len(rows)

    p = 1
    while p <= max_pages:
        n = one(p)
        if n == 0:
            break
        p += 1
    rows = []
    for k in sorted(pages):
        for r in pages[k]:
            rows.append({
                "symbol": r["symbol"], "code": r["code"], "name": r["name"],
                "price": _num(r["trade"]), "pct": _num(r["changepercent"]),
                "open": _num(r["open"]), "high": _num(r["high"]), "low": _num(r["low"]),
                "prev_close": _num(r.get("settlement")),
                "volume": _num(r["volume"]), "amount": _num(r["amount"]),
                "pe": _num(r["per"]), "pb": _num(r["pb"]),
                "mktcap_yi": round(_num(r["mktcap"]) / 1e4, 2),      # 亿元
                "float_cap_yi": round(_num(r["nmc"]) / 1e4, 2),      # 亿元
                "turnover": _num(r["turnoverratio"]),
            })
    seen, out = set(), []
    for r in rows:
        if r["symbol"] and r["symbol"] not in seen:
            seen.add(r["symbol"])
            out.append(r)
    return out


# ---------------- K 线：gtimg 主源 + baostock 备源 ----------------
# 2026-11-06 起腾讯前复权接口 fqkline 返回 HTTP 501，kline() 自动回落 baostock。
# - 代码转换: sh600519 -> sh.600519, sz000002 -> sz.000002, sh000001 -> sh.000001
# - 复权: baostock adjustflag='2' 前复权 / '1' 后复权 / '3' 不复权；指数无复权概念，'2' 与 '3' 返回一致
# - 成交量: gtimg 单位为「手」；baostock 原始单位为「股」，统一 /100 对齐为「手」（个股已验证 100 倍；
#   指数同除以 100 后与 gtimg 同量级，个别指数存在 3%~4% 的供应商口径差异，见 docstring）
# - baostock 是全局单连接（baostock.common.context.default_socket），非线程安全，用 _BS_LOCK 串行化；
#   login 一次约 5~14 秒、单次查询约 6~12 秒（本机实测），不要在进程内反复 login/logout。
KLINE_SOURCE = (os.environ.get("KLINE_SOURCE") or "").strip().lower()

_BS_LOCK = threading.Lock()
_BS_STATE = {"logged_in": False}
_BS_FREQ = {"day": "d", "week": "w", "month": "m"}
_BS_ADJUST = {"qfq": "2", "hfq": "1", "none": "3", "": "3", None: "3"}
_BS_FIELDS = "date,open,high,low,close,volume"


def _to_baostock_code(symbol):
    """sh600519 -> sh.600519；指数 sh000001 -> sh.000001。"""
    s = (symbol or "").strip().lower()
    if "." in s:
        return s
    if len(s) > 2 and s[:2] in ("sh", "sz"):
        return s[:2] + "." + s[2:]
    if s[:2] in ("bj", "c8", "nq"):
        raise ValueError(f"baostock 不支持北交所/新三板代码: {symbol}")
    raise ValueError(f"无法识别的证券代码: {symbol}")


def _baostock_login(bs):
    if _BS_STATE["logged_in"]:
        return bs
    import contextlib, io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):  # baostock 会 print("login success!")，不能污染 CLI 的 JSON stdout
            res = bs.login()
    except Exception as e:
        raise RuntimeError(f"baostock login 异常: {e}") from e
    if getattr(res, "error_code", "0") != "0":
        if buf.getvalue():
            sys.stderr.write(buf.getvalue())
        raise RuntimeError(f"baostock login 失败: {res.error_code} {res.error_msg}")
    _BS_STATE["logged_in"] = True
    return bs


def _baostock_logout():
    if not _BS_STATE["logged_in"]:
        return
    try:
        import contextlib, io
        import baostock as bs
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()
    except Exception:  # noqa: 退出阶段连接可能已断（实测偶发 Errno 103），忽略
        pass
    finally:
        _BS_STATE["logged_in"] = False


atexit.register(_baostock_logout)


def _kline_baostock(symbol, count=250, period="day", fq="qfq"):
    """baostock 备源：返回格式与 gtimg 一致，按日期升序；volume 已 /100 换算为「手」。"""
    freq = _BS_FREQ.get((period or "day").strip().lower())
    if freq is None:
        raise ValueError(f"baostock 备源不支持 period={period!r}（仅 day/week/month）")
    adjust = _BS_ADJUST.get((fq or "qfq").lower())
    if adjust is None:
        raise ValueError(f"baostock 备源不支持 fq={fq!r}（仅 qfq/hfq/none）")
    code = _to_baostock_code(symbol)
    try:
        import baostock as bs
    except ImportError as e:
        raise RuntimeError(f"baostock 未安装: {e}")

    n = max(int(count or 250), 1)
    end = datetime.date.today()
    start = end - datetime.timedelta(days=max(n * 2, 90))  # 2 倍日历日宽裕覆盖交易日
    fields = _BS_FIELDS

    last_err = None
    with _BS_LOCK:
        for attempt in range(2):
            try:
                _baostock_login(bs)
                res = bs.query_history_k_data_plus(
                    code, fields, start_date=start.isoformat(), end_date=end.isoformat(),
                    frequency=freq, adjustflag=adjust)
                if getattr(res, "error_code", "0") != "0":
                    raise RuntimeError(f"baostock 查询失败: {res.error_code} {res.error_msg}")
                rows = []
                while res.next():
                    rows.append(res.get_row_data())
                if not rows:
                    raise RuntimeError(
                        f"baostock 返回空数据（代码 {code} 可能不支持，如科创50 sh000688）")
                break
            except Exception as e:  # noqa: 连接断开时重登一次
                last_err = e
                _BS_STATE["logged_in"] = False
                if attempt == 1:
                    raise RuntimeError(f"baostock 备源失败({code}): {last_err}") from e

    out = []
    for r in rows:
        try:
            # baostock 字段顺序: date,open,high,low,close,volume
            out.append({"date": r[0], "open": float(r[1]), "close": float(r[4]),
                        "high": float(r[2]), "low": float(r[3]),
                        "volume": float(r[5]) / 100.0})
        except (TypeError, ValueError, IndexError):
            continue
    return out[-n:]


def _kline_gtimg(symbol, count=250, period="day", fq="qfq"):
    """腾讯 gtimg 主源。fqkline 已 501，保留以便接口恢复后自动切回。"""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={symbol},{period},,,{count},{fq}")
    txt = _get(url, headers={"User-Agent": UA})
    d = json.loads(txt)["data"].get(symbol, {})
    key = f"{fq}{period}"
    bars = d.get(key) or d.get(period) or []
    out = []
    for b in bars:
        if len(b) < 6:
            continue
        out.append({"date": b[0], "open": float(b[1]), "close": float(b[2]),
                    "high": float(b[3]), "low": float(b[4]), "volume": float(b[5])})
    return out


def kline(symbol, count=250, period="day", fq="qfq"):
    """日K(前复权). 返回 [{date, open, close, high, low, volume}, ...]（按日期升序）。

    数据源顺序：gtimg fqkline 主源 → 异常（含 HTTP 501）时自动回落 baostock 备源。
    KLINE_SOURCE=baostock 强制备源；KLINE_SOURCE=gtimg 只用主源不回退。

    volume 口径：gtimg 为「手」；baostock 原始为「股」，已 /100 对齐为「手」。
    注意：baostock 个别指数成交量与 gtimg 有 3%~4% 供应商差异；科创50（sh000688）在
    baostock 无数据，备源会明确报错。
    """
    if KLINE_SOURCE == "baostock":
        return _kline_baostock(symbol, count, period, fq)
    if KLINE_SOURCE == "gtimg":
        return _kline_gtimg(symbol, count, period, fq)
    try:
        return _kline_gtimg(symbol, count, period, fq)
    except Exception as gtimg_err:  # noqa: HTTP 501/超时/JSON 异常等
        try:
            return _kline_baostock(symbol, count, period, fq)
        except Exception as bs_err:
            raise RuntimeError(
                f"kline({symbol}) 失败：gtimg={gtimg_err!r}；baostock 备源={bs_err!r}"
            ) from gtimg_err


def breadth(rows):
    """涨跌家数 / 分布 / 成交额。rows 为 snapshot 结果。"""
    b = {"total": len(rows), "up": 0, "down": 0, "flat": 0,
         "limit_up": 0, "limit_down": 0,
         "up5": 0, "up0_5": 0, "down0_5": 0, "down5": 0,
         "amount_yi": round(sum(r["amount"] for r in rows) / 1e8, 1),
         "median_pct": 0.0}
    pcts = []
    for r in rows:
        p = r["pct"]
        if r["price"] <= 0:      # 停牌
            continue
        pcts.append(p)
        if p > 0.01:
            b["up"] += 1
        elif p < -0.01:
            b["down"] += 1
        else:
            b["flat"] += 1
        if p >= 9.8:
            b["limit_up"] += 1
        elif p <= -9.8:
            b["limit_down"] += 1
        if p >= 5:
            b["up5"] += 1
        elif p > 0:
            b["up0_5"] += 1
        elif p <= -5:
            b["down5"] += 1
        elif p < 0:
            b["down0_5"] += 1
    if pcts:
        pcts.sort()
        b["median_pct"] = round(pcts[len(pcts) // 2], 2)
    return b


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "index"
    if cmd == "index":
        q = quote(list(INDICES))
        print(json.dumps(q, ensure_ascii=False, indent=1))
    elif cmd == "quote":
        print(json.dumps(quote(sys.argv[2].split(",")), ensure_ascii=False, indent=1))
    elif cmd == "kline":
        print(json.dumps(kline(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 250),
                         ensure_ascii=False, indent=1))
    elif cmd == "snapshot":
        rows = snapshot()
        b = breadth(rows)
        today = time.strftime("%Y-%m-%d")
        os.makedirs(DATA, exist_ok=True)
        path = os.path.join(DATA, f"snapshot-{today}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "breadth": b, "rows": rows}, f, ensure_ascii=False)
        print("saved", path, "rows", len(rows))
        print(json.dumps(b, ensure_ascii=False))
    elif cmd == "breadth":
        d = json.load(open(sys.argv[2], encoding="utf-8"))
        print(json.dumps(breadth(d["rows"]), ensure_ascii=False, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
