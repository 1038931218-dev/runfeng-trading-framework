#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实盘账户「可交易板块」过滤器（2026-11-07，用户确认：仅主板可交易）。

账户权限（用户 2026-11-07 确认）：
  可买：沪深主板  600/601/603/605、000/001/002/003
  禁买：科创板 688/689；创业板 300/301；北交所 8/4/920（bj 前缀）；ST/*ST（团队红线，单独判断）

用法:
  python3 tools/account_filter.py sh601668 sz000002 sh688981 sz300498 bj920085
  python3 tools/account_filter.py --file models/screen-2026-11-06.csv   # 过滤 CSV 并列出不可买
  python3 tools/account_filter.py --json models/portfolio-2026-11-06.json

备注：这里只判断「板块权限」，不判断 ST/流动性/估值/手数——那些由风控清单另做。
卖出已持有的受限板块股票不受本函数限制（买入才受限）。
"""
import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
STAR_PREFIXES = ("688", "689")
GEM_PREFIXES = ("300", "301")
BSE_PREFIXES = ("8", "4", "9")  # bj 前缀或 4/8/9 开头的北交所代码（920 等）


def _split_symbol(symbol):
    """sh601668 / 601668 / bj920085 -> (prefix, 6位代码)"""
    s = (symbol or "").strip().lower()
    if len(s) > 2 and s[:2] in ("sh", "sz", "bj"):
        return s[:2], s[2:]
    return "", s


def board_of(symbol):
    """返回 (board, tradable)；board ∈ 主板/科创板/创业板/北交所/未知"""
    prefix, code = _split_symbol(symbol)
    if not code.isdigit() or len(code) != 6:
        return "未知", False
    if prefix == "bj" or code.startswith(BSE_PREFIXES):
        return "北交所", False
    if code.startswith(STAR_PREFIXES):
        return "科创板", False
    if code.startswith(GEM_PREFIXES):
        return "创业板", False
    if code.startswith(MAIN_BOARD_PREFIXES):
        return "主板", True
    return "未知", False


def is_tradable(symbol):
    return board_of(symbol)[1]


def explain(symbol):
    board, ok = board_of(symbol)
    return f"{symbol}: {board} → {'可买' if ok else '不可买（板块权限）'}"


def _iter_symbols(args, csv_path=None, json_path=None):
    out = list(args)
    if csv_path:
        with open(csv_path, encoding="utf-8") as f:
            out += [row["symbol"] for row in csv.DictReader(f)]
    if json_path:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        out += [p["symbol"] for p in data.get("positions", [])]
    return out


def main():
    ap = argparse.ArgumentParser(description="实盘账户可交易板块过滤器（仅主板）")
    ap.add_argument("symbols", nargs="*", help="代码，如 sh601668 sz000002")
    ap.add_argument("--file", help="CSV（含 symbol 列）")
    ap.add_argument("--json", dest="json_path", help="组合 JSON（含 positions[].symbol）")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    a = ap.parse_args()

    syms = []
    if a.file:
        syms += _iter_symbols([], csv_path=a.file)
    if a.json_path:
        syms += _iter_symbols([], json_path=a.json_path)
    syms += _iter_symbols(a.symbols)
    if not syms:
        ap.print_help()
        return 1

    bad, good = [], []
    for s in syms:
        (good if is_tradable(s) else bad).append(s)
        if not a.quiet:
            print(explain(s))
    print(f"\n可买 {len(good)} 只 | 不可买 {len(bad)} 只")
    if bad:
        print("不可买清单：" + "、".join(bad))
    return 0 if not bad or a.quiet else 0


if __name__ == "__main__":
    sys.exit(main())
