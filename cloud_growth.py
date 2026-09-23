#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy「成长中心」云端自动化（半云端方案 · 云端侧）

完全不依赖本机 WorkBuddy 客户端。token 来自 GitHub Actions Secret 注入的环境变量，
直接以 Bearer 调成长中心接口，自动完成：
  - Buddy 旅行（领礼物 / 派出发）
  - 任务中心（接单 / 领完成任务奖）
  - 补登卡（断登自动补一张，保住连登）
  - 连登奖励兑换（入门 7d / 进阶 14d / 巅峰 28d）
  - 盲盒抽奖（有次数才抽，一轮一次）
  - Buddy 盲盒（能量够就开）
  - 能量 / 连签状态展示

设计要点：
  - 复用本仓库 scripts/signin.py 中经实战验证的 run_growth() 逻辑，**不重写**，
    以免偏离已验证行为。signin.py 的 get/post/_client_token/dig 等全是标准库实现，
    无任何一处依赖本机客户端，云端可安全 import。
  - 纯标准库，无第三方依赖。
  - 不读取、不打印任何令牌原文；日志只输出业务结论。
  - 各子步骤独立 try，一段失败不影响其余；认证/权限拒绝时结束本轮。
  - 写操作均带幂等/防重放保护：抽奖有次数才抽且一轮一次、补登卡单轮最多一张、
    兑换仅对未 claimed/locked 档位尝试，重复运行安全。
"""
import os
import sys
import json

# 复用 scripts/signin.py 的成熟逻辑（无需本机客户端）
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import signin  # noqa: E402

ENDPOINT = os.environ.get("WB_ENDPOINT", "https://copilot.tencent.com").rstrip("/")
REQUIRED = ("WB_ACCESS_TOKEN", "WB_USER_ID", "WB_DOMAIN")


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def build_headers():
    for k in REQUIRED:
        if not os.environ.get(k):
            raise SystemExit(json.dumps(
                {"result": "CONFIG_ERROR", "report": "缺少环境变量 %s" % k},
                ensure_ascii=False))
    h = {
        "Accept": "application/json",
        "Authorization": "Bearer %s" % os.environ["WB_ACCESS_TOKEN"],
        "Content-Type": "application/json",
        "X-User-Id": os.environ["WB_USER_ID"],
        "User-Agent": "WorkBuddy-Cloud",
    }
    if os.environ.get("WB_DOMAIN"):
        h["X-Domain"] = os.environ["WB_DOMAIN"]
    eid = os.environ.get("WB_ENTERPRISE_ID")
    if eid:
        h["X-Enterprise-Id"] = eid
        h["X-Tenant-Id"] = eid
    return h


def main():
    try:
        headers = build_headers()
    except SystemExit as e:
        emit(json.loads(str(e)))
        return 2

    signin._start_budget("growth")
    try:
        code, out = signin.run_growth(headers, ENDPOINT)
    except Exception as e:  # noqa: BLE001
        emit({"result": "ERROR",
              "report": "成长中心执行异常（%s: %s）" % (type(e).__name__, e),
              "needs_attention": True})
        return 1

    # run_growth 返回 (code, dict)，dict 已是结构化汇报，直接转发
    if isinstance(out, dict):
        out.setdefault("needs_attention", code != 0)
        emit(out)
    else:
        emit({"result": "ERROR", "report": "成长中心返回结构异常",
              "raw": str(out)[:200], "needs_attention": True})
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
