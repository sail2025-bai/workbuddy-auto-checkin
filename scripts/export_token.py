#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 登录凭证本机导出工具（半云端方案 · 本机侧）

作用：在本机（WorkBuddy 客户端已登录）解密登录态，导出云端签到所需的明文凭证，
供填入 GitHub Actions Secret。

依赖 signin.py 的解密逻辑（复用客户端原生接口 storage.loggerGet 在隐藏子进程中
就地解密，密钥只走内存管道，不落盘）。

输出一行 JSON：
  {"WB_ACCESS_TOKEN": "...", "WB_USER_ID": "...", "WB_DOMAIN": "...", "WB_ENTERPRISE_ID": "..."}

安全提示：
  - 输出含明文 accessToken，等同于你的登录态，请勿外传、勿提交到代码仓库、勿打印到公开日志。
  - accessToken 寿命约 30 天，到期前需重新运行本工具并更新 Secret（建议每 25 天一次）。
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signin  # noqa: E402


def main():
    auth_file = signin.find_auth_file()[0]
    if not auth_file:
        print(json.dumps({"error": "未找到登录态文件，请确认 WorkBuddy 客户端已登录"},
                         ensure_ascii=False))
        return 1
    session = signin.load_session(auth_file)
    token = (session.get("auth") or {}).get("accessToken")
    if isinstance(token, dict):
        # 加密信封：调用客户端原生接口就地解密
        token = signin._run_auth_helper(
            signin.find_workbuddy_runtime(),
            {"operation": "decrypt", "value": token},
        )["accessToken"]
    account = session.get("account") or {}
    out = {
        "WB_ACCESS_TOKEN": token,
        "WB_USER_ID": account.get("uid"),
        "WB_DOMAIN": (session.get("auth") or {}).get("domain"),
        "WB_ENTERPRISE_ID": account.get("enterpriseId"),
    }
    if not out["WB_ACCESS_TOKEN"] or not out["WB_USER_ID"] or not out["WB_DOMAIN"]:
        print(json.dumps({"error": "凭证不完整，请确认 WorkBuddy 客户端已登录"},
                         ensure_ascii=False))
        return 1
    # 仅输出凭证 JSON，不附加任何多余文本，便于复制
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
