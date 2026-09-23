#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy「Buddy 加油站」云端签到脚本（半云端方案 · 云端侧）

完全不依赖本机 WorkBuddy 客户端。
读取由 GitHub Actions Secret 注入的环境变量，直接以 Bearer 调用签到接口。

必需环境变量：
  WB_ACCESS_TOKEN    登录态解密后的 accessToken（由本机 export_token.py 导出）
  WB_USER_ID         X-User-Id（account.uid）
  WB_DOMAIN          X-Domain（auth.domain，一般为 www.workbuddy.cn）
可选环境变量：
  WB_ENTERPRISE_ID   企业/租户 ID（个人账号留空）
  WB_ENDPOINT        签到接口域名，默认 https://copilot.tencent.com

设计要点：
  - 纯标准库，无第三方依赖，能在任意 GitHub Actions / 云函数 runner 跑。
  - 不读取、不打印任何令牌原文（令牌来自环境变量，日志只输出业务结论）。
  - 幂等：当天已签直接返回 ALREADY，不会重复领积分。
  - 不 reverse 任何私有 OIDC endpoint；accessToken 由本机解密后导出。
  - 接口返回结构：{ "code":0, "msg":"OK", "data": { today_checked_in, streak_days, total_credits, ... } }
"""
import os
import sys
import json
import ssl
import urllib.request
import urllib.error

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


def post(path, headers, payload=None, timeout=20):
    url = ENDPOINT + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}
    except urllib.error.URLError as e:
        return -1, {"error": str(e.reason)}
    except Exception as e:  # noqa: BLE001
        return -1, {"error": str(e)}


def dig(o, *keys, default=None):
    cur = o
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def data_of(body):
    """真实业务字段在 body.data 下；返回该层对象，缺失时回退到 body 本身。"""
    if isinstance(body, dict):
        d = body.get("data")
        if isinstance(d, dict):
            return d
    return body if isinstance(body, dict) else {}


def auth_fail(code):
    return {"result": "AUTH_ERROR",
            "report": "令牌失效，请在本机重新运行 export_token.py 并更新 GitHub Secret",
            "http": code, "needs_attention": True}


def main():
    try:
        headers = build_headers()
    except SystemExit as e:
        emit(json.loads(str(e)))
        return 2

    # 1) 查状态
    scode, sbody = post("/v2/billing/meter/checkin-activity-status", headers)
    if scode in (401, 403):
        emit(auth_fail(scode)); return 1
    if scode == -1:
        emit({"result": "NETWORK", "report": "云端网络不可达：%s" % sbody.get("error"),
              "error": sbody.get("error"), "needs_attention": True}); return 1
    if not (200 <= scode < 300):
        emit({"result": "ERROR", "report": "签到状态接口异常 HTTP %s" % scode,
              "http": scode, "status_body": sbody, "needs_attention": True}); return 1

    status = data_of(sbody)
    if dig(status, "today_checked_in") in (True, 1):
        emit({"result": "ALREADY", "report": "今日已签到（云端）",
              "streak_days": dig(status, "streak_days"),
              "total_credits": dig(status, "total_credits"),
              "needs_attention": False})
        return 0

    # 2) 未签 → 领取
    ccode, cbody = post("/v2/billing/meter/daily-checkin", headers)
    if ccode in (401, 403):
        emit(auth_fail(ccode)); return 1
    if ccode == -1:
        emit({"result": "NETWORK", "report": "云端网络不可达：%s" % cbody.get("error"),
              "needs_attention": True}); return 1

    # 3) 成功判定：HTTP 2xx、业务 code==0、服务端判定已签、或返回了今日积分
    claim = data_of(cbody)
    ok = (200 <= ccode < 300) or (dig(cbody, "code") == 0) \
        or dig(claim, "today_checked_in") in (True, 1) \
        or dig(claim, "today_credit") is not None
    if not ok:
        emit({"result": "ERROR", "report": "领取未确认（HTTP %s）" % ccode,
              "http": ccode, "claim_body": cbody, "needs_attention": True})
        return 1

    # 4) 二次查状态确认，拿连续天数 / 累计积分
    scode2, sbody2 = post("/v2/billing/meter/checkin-activity-status", headers)
    fresh = data_of(sbody2) if (200 <= scode2 < 300) else status
    emit({"result": "OK", "report": "云端签到成功",
          "today_credit": dig(fresh, "today_credit"),
          "streak_days": dig(fresh, "streak_days"),
          "total_credits": dig(fresh, "total_credits"),
          "needs_attention": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
