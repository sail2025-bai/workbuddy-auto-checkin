#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 半云端签到 · 仅刷新 GitHub Secrets（本机执行一次）

前置：
  - GITHUB_TOKEN 环境变量（fine-grained 需 Secrets: write；classic 需 repo）
  - 本机 WorkBuddy 客户端已登录（export_token.py 需借其原生接口解密登录态）
用途：accessToken 约 30 天过期。到期前本机重跑 export_token.py 解密导出，
      再用本脚本把新 WB_ACCESS_TOKEN 等写入仓库 Secrets。
      【不碰仓库代码、不 git push】，比重跑 deploy_github.py 更轻量、无 force-push 风险。

安全：accessToken 明文仅在进程内存；日志只输出业务结论，绝不回显 token。

可选环境变量：
  WB_REPO   仓库名（默认 workbuddy-auto-checkin）；仓库不在本人名下时覆盖。
"""
import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

API = "https://api.github.com"
REPO = os.environ.get("WB_REPO", "workbuddy-auto-checkin")


def api(method, path, token, data=None):
    url = API + path
    headers = {
        "Authorization": "Bearer %s" % token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "workbuddy-refresh",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"message": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return -1, {"message": str(e)}


def export_credentials():
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, os.path.join(here, "export_token.py")],
                       capture_output=True, text=True, cwd=here)
    if r.returncode != 0:
        sys.exit("export_token.py 失败:\n" + (r.stderr or r.stdout).strip()[:400])
    return json.loads(r.stdout.strip())


def encrypt_secret(public_key_b64, secret_value):
    # GitHub Actions Secrets 用 libsodium sealed box（Curve25519, 32 字节），非 RSA。
    from nacl.public import PublicKey, SealedBox
    from nacl.encoding import Base64Encoder
    box = SealedBox(PublicKey(public_key_b64, encoder=Base64Encoder))
    return box.encrypt(secret_value.encode("utf-8"), encoder=Base64Encoder).decode("ascii")


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("请设置环境变量 GITHUB_TOKEN（需 Secrets: write 权限）")

    st, me = api("GET", "/user", token)
    if st != 200:
        sys.exit("获取 GitHub 账号失败: %s" % me)
    owner = me["login"]

    creds = export_credentials()
    print("本机凭证导出成功 | uid=%s domain=%s token长度=%d"
          % (creds.get("WB_USER_ID"), creds.get("WB_DOMAIN"),
             len(creds.get("WB_ACCESS_TOKEN", ""))))

    st, pk = api("GET", "/repos/%s/%s/actions/secrets/public-key" % (owner, REPO), token)
    if st != 200:
        sys.exit("获取仓库公钥失败 HTTP %s: %s" % (st, pk))
    key_id = pk["key_id"]

    for name in ("WB_ACCESS_TOKEN", "WB_USER_ID", "WB_DOMAIN", "WB_ENTERPRISE_ID"):
        val = creds.get(name) or ""
        enc = encrypt_secret(pk["key"], val)
        st, r = api("PUT", "/repos/%s/%s/actions/secrets/%s" % (owner, REPO, name),
                    token, {"encrypted_value": enc, "key_id": key_id})
        if st not in (201, 204):
            sys.exit("写入 Secret %s 失败 HTTP %s: %s" % (name, st, r))
        print("Secret 已更新: %s%s" % (name, " (空)" if not val else ""))

    print("\n完成。云端下次运行将使用新 token，无需改代码或重新部署。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
