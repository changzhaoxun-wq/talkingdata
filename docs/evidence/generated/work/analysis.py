#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一季度销售复盘计算脚本（虚构演示数据）
输入: uploads/sales.csv
输出: summary.csv
用法: python analysis.py
"""
import csv
import os

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "uploads", "sales.csv")
OUT = os.path.join(BASE, "summary.csv")

FIELDS = ["scope", "key", "revenue", "cost", "gross_profit", "gross_margin_pct", "orders",
          "aov", "cost_per_order"]


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["revenue"] = float(r["revenue"])
        r["cost"] = float(r["cost"])
        r["orders"] = int(r["orders"])
    return rows


def agg(items):
    rev = sum(i["revenue"] for i in items)
    cost = sum(i["cost"] for i in items)
    gp = rev - cost
    gm = gp / rev * 100 if rev else 0.0
    orders = sum(i["orders"] for i in items)
    aov = rev / orders if orders else 0.0
    cpo = cost / orders if orders else 0.0
    return rev, cost, gp, gm, orders, aov, cpo


def main():
    rows = read_rows(SRC)

    months = sorted({r["month"] for r in rows})
    channels = sorted({r["channel"] for r in rows})

    out = []

    # 总计
    out.append(["总计", "全部"] + list(agg(rows)))

    # 按月
    for m in months:
        sub = [r for r in rows if r["month"] == m]
        out.append(["月度", m] + list(agg(sub)))

    # 按渠道
    for c in channels:
        sub = [r for r in rows if r["channel"] == c]
        out.append(["渠道", c] + list(agg(sub)))

    # 月度 x 渠道
    for m in months:
        for c in channels:
            sub = [r for r in rows if r["month"] == m and r["channel"] == c]
            if not sub:
                continue
            out.append(["月度x渠道", "%s/%s" % (m, c)] + list(agg(sub)))

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for scope, key, rev, cost, gp, gm, orders, aov, cpo in out:
            w.writerow([scope, key, int(rev), int(cost), int(gp), round(gm, 2), orders,
                        round(aov, 2), round(cpo, 2)])

    # 控制台输出，便于人工复查
    print("scope     key           收入     成本     毛利  毛利率%  订单   客单价  单均成本")
    for scope, key, rev, cost, gp, gm, orders, aov, cpo in out:
        print("%-9s %-12s %7.0f %7.0f %7.0f %7.2f %5d %7.1f %8.1f"
              % (scope, key, rev, cost, gp, gm, orders, aov, cpo))
    print("\n已写出: %s" % OUT)


if __name__ == "__main__":
    main()
