#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 半云端签到 · GitHub 一键部署脚本（本机执行一次）

前置：把 GitHub Personal Access Token 设为环境变量 GITHUB_TOKEN（需 repo + workflow 权限）。
      token 仅用于「建仓库 / 设 Secret / 推送 / 触发验证」，部署完成后可立即吊销，
      运行时不再依赖它（运行时只依赖仓库 Secrets）。

脚本流程：
  1. 本机 export_token.py 解密导出凭证（仅在内存，绝不打印明文）
  2. 创建私有仓库（已存在则跳过）
  3. 准备云端仓库内容（cloud_signin.py + 工作流 + README + .gitignore）
  4. git push
  5. 用仓库公钥 (RSA-OAEP) 加密写入 4 个 Secrets
  6. 触发 workflow_dispatch 跑首次验证

安全：accessToken 明文只存在于进程内存，日志只输出业务结论。
"""
import os
import sys
import json
import base64
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error

API = "https://api.github.com"
REPO_NAME = "workbuddy-auto-checkin"


def api(method, path, token, data=None):
    url = API + path
    headers = {
        "Authorization": "Bearer %s" % token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "workbuddy-deploy",
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
    # GitHub Actions Secrets 使用 libsodium sealed box（Curve25519 公钥，32 字节），
    # 不是 RSA。公钥以标准 base64 给出，直接用 Base64Encoder 解码即可。
    from nacl.public import PublicKey, SealedBox
    from nacl.encoding import Base64Encoder
    box = SealedBox(PublicKey(public_key_b64, encoder=Base64Encoder))
    return box.encrypt(secret_value.encode("utf-8"), encoder=Base64Encoder).decode("ascii")


def read_local(path):
    return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), path),
                encoding="utf-8").read()


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("请先设置环境变量 GITHUB_TOKEN（GitHub PAT，需 repo + workflow 权限）")

    # 1) 本机导出凭证
    creds = export_credentials()
    print("本机凭证导出成功 | uid=%s domain=%s token长度=%d"
          % (creds.get("WB_USER_ID"), creds.get("WB_DOMAIN"),
             len(creds.get("WB_ACCESS_TOKEN", ""))))

    # 2) 取 owner + 建私有仓库
    st, me = api("GET", "/user", token)
    if st != 200:
        sys.exit("获取 GitHub 账号失败: %s" % me)
    owner = me["login"]
    print("GitHub 账号: %s" % owner)

    st, repo = api("POST", "/user/repos", token,
                   {"name": REPO_NAME, "private": True,
                    "description": "WorkBuddy Buddy 加油站每日自动签到（半云端）",
                    "auto_init": False})
    if st == 201:
        print("私有仓库已创建: https://github.com/%s/%s" % (owner, REPO_NAME))
    elif st in (409, 422):
        print("仓库已存在，跳过创建: https://github.com/%s/%s" % (owner, REPO_NAME))
    else:
        sys.exit("创建仓库失败 HTTP %s: %s" % (st, repo))

    remote = "https://x-access-token:%s@github.com/%s/%s.git" % (token, owner, REPO_NAME)

    # 3) 准备本地仓库内容
    tmp = tempfile.mkdtemp(prefix="wb-gh-")
    try:
        os.makedirs(os.path.join(tmp, ".github", "workflows"), exist_ok=True)
        with open(os.path.join(tmp, "cloud_signin.py"), "w", encoding="utf-8") as f:
            f.write(read_local("cloud_signin.py"))
        with open(os.path.join(tmp, ".github", "workflows", "daily-checkin.yml"),
                  "w", encoding="utf-8") as f:
            f.write(read_local("daily-checkin.yml"))
        readme = (
            "# WorkBuddy 自动签到（半云端）\n\n"
            "每日通过 GitHub Actions 自动完成 WorkBuddy「Buddy 加油站」签到。\n\n"
            "## 工作原理\n"
            "- 本机每 ~25 天运行 `export_token.py` 解密导出 accessToken\n"
            "- 本仓库 Secrets 保存凭证，Actions 每日用 Bearer 调用签到接口\n"
            "- 云端脚本 `cloud_signin.py` 完全不依赖本机客户端\n\n"
            "## Secret 说明\n"
            "| 名称 | 来源 |\n"
            "|---|---|\n"
            "| WB_ACCESS_TOKEN | 本机 export_token.py 导出 |\n"
            "| WB_USER_ID | 同上 |\n"
            "| WB_DOMAIN | 一般 www.workbuddy.cn |\n"
            "| WB_ENTERPRISE_ID | 个人账号留空 |\n\n"
            "## 维护\n"
            "- accessToken 约 30 天失效，到期前需重跑 export 并更新 Secret\n"
            "- GitHub 60 天无 commit 会禁用定时任务，偶尔 push 即可\n"
        )
        with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
            f.write(readme)
        with open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("# 本机工具不推到云端仓库\nsignin.py\nexport_token.py\ndeploy_github.py\n__pycache__/\n*.log\n")

        # 4) git 提交 + 推送
        def git(*args):
            r = subprocess.run(["git", "-C", tmp, *args], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit("git %s 失败:\n%s" % (args, (r.stderr or r.stdout).strip()[:400]))
            return r.stdout.strip()
        git("init", "-q")
        git("config", "user.email", "noreply@github.com")
        git("config", "user.name", "WorkBuddy Auto Checkin")
        git("add", "-A")
        git("commit", "-q", "-m", "init: WorkBuddy cloud checkin")
        git("branch", "-M", "main")
        git("remote", "add", "origin", remote)
        git("push", "-q", "-u", "origin", "main", "--force")
        print("代码已推送到 https://github.com/%s/%s" % (owner, REPO_NAME))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 5) 写 Secrets（用仓库公钥加密）
    st, pk = api("GET", "/repos/%s/%s/actions/secrets/public-key"
                 % (owner, REPO_NAME), token)
    if st != 200:
        sys.exit("获取公钥失败 HTTP %s: %s" % (st, pk))
    key_id = pk["key_id"]
    for name in ("WB_ACCESS_TOKEN", "WB_USER_ID", "WB_DOMAIN", "WB_ENTERPRISE_ID"):
        val = creds.get(name) or ""
        enc = encrypt_secret(pk["key"], val)
        st, r = api("PUT", "/repos/%s/%s/actions/secrets/%s"
                    % (owner, REPO_NAME, name), token,
                    {"encrypted_value": enc, "key_id": key_id})
        if st not in (201, 204):
            sys.exit("写入 Secret %s 失败 HTTP %s: %s" % (name, st, r))
        print("Secret 已写入: %s%s" % (name, " (空)" if not val else ""))

    # 6) 触发首次验证
    st, r = api("POST",
                "/repos/%s/%s/actions/workflows/daily-checkin.yml/dispatches"
                % (owner, REPO_NAME), token, {"ref": "main"})
    if st == 204:
        print("已触发首次验证运行（workflow_dispatch）。请在 Actions 页查看结果。")
    else:
        print("触发验证返回 HTTP %s（可手动在 Actions 页点 Run workflow）: %s" % (st, r))

    print("\n完成。部署后可在 GitHub 立即吊销此 PAT：Settings > Developer settings > PAT")
    print("运行时不依赖 PAT，只依赖仓库 Secrets。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
