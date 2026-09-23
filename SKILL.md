---
name: workbuddy-auto-checkin
description: 在 macOS 上配置 WorkBuddy「Buddy 加油站」每日自动签到。当用户提到「WorkBuddy 自动签到 / Buddy 加油站签到 / 自动领积分 / 每日签到配置 / 签到脚本跑不通 / 登录态加密解不开 / keyblob / sym-v1 / AtRestEncryption / 签到技能在 Mac 上失效」时使用。本技能自带全部脚本（scripts/ 子目录），含纯云端 GitHub Actions 方案与本机自动化方案、完整部署步骤、验证命令与错误码对照。
agent_created: true
---

# WorkBuddy 自动签到配置（macOS）

WorkBuddy「Buddy 加油站」每日签到 = 带登录态 Token 的一次 HTTP 请求。签到接口 `POST https://<auth.domain>/v2/billing/meter/daily-checkin`（幂等，已签返回 `code=10001`）。

**收益**：前 6 天各 100 积分，第 7 天额外 1000；断签会清零连签天数，所以补签兜底比主签更重要。

## 本技能自带文件

全部位于技能目录的 `scripts/` 子目录（与 `SKILL.md` 同级），技能加载后直接可用：

| 文件 | 用途 | 在哪跑 |
|---|---|---|
| `signin.py` | 第三方 `88lin/workbuddy-auto-signin` 主脚本，借客户端原生接口解密登录态 | 本机 |
| `export_token.py` | 本机导出脚本，import signin.py 复用解密逻辑，输出明文凭证 JSON | 本机（每 ~25 天一次） |
| `cloud_signin.py` | 云端签到脚本，纯标准库，读 4 个环境变量直接签，**不依赖本机客户端** | GitHub Actions runner |
| `deploy_github.py` | GitHub 一键部署：建私有仓库 → 加密写 4 个 Secret → 推送 → 触发验证 | 本机（一次） |
| `daily-checkin.yml` | GitHub Actions 工作流模板（北京 09:00 / 21:00） | 推到仓库 `.github/workflows/` |
| `refresh_secret.py` | 仅刷新仓库 Secrets（不碰代码），用于 token 过期后续期 | 本机（每 ~25 天一次） |
| `requirements.txt` | 部署依赖（仅 `pynacl`，用于 Secrets 加密） | 本机 venv |

> 路径约定：下文 `<SKILL>/scripts` 指技能目录下的 `scripts/` 文件夹。

## 〇、新机器前置（部署前必读）

换一台机器、从零部署时，请按此顺序准备。少了任一项，一键部署都会卡住。

1. **本机已安装并登录 WorkBuddy 桌面端**
   `export_token.py` 必须借客户端原生接口（`storage.loggerGet()`）解密登录态，所以客户端必须存在且处于已登录状态。验证：`python3 signin.py doctor` 期望返回 `AUTH_READY`。

2. **准备一个 GitHub fine-grained PAT（这是部署的第一步输入）**
   - 创建入口：GitHub → Settings → Developer settings → **Fine-grained tokens**（或直接开 `https://github.com/settings/tokens?type=beta`）。
   - 权限（`Repository access` 选 **All repositories**）：
     - `Contents` = Read and write（推送代码）
     - `Secrets` = Read and write（写 4 个 Secret）
     - `Workflows` = Read and write（触发验证）
     - `Administration` = Read and write（保险）
   - 有效期建议 **30 天**：部署完成并验证通过后即可吊销，因为运行时只依赖仓库 Secrets，不依赖这个 token。
   - 安全：token 明文只在此处生成一次，别外传、别提交代码。把它直接粘贴给 AI 会话即可，脚本只在进程环境变量里用，不落盘、不回显。
   - **注意**：PAT 必须由你本人在 GitHub 网页生成后提供；技能本身无法替你创建 PAT（那需要你的 GitHub 登录态）。

3. **本机 Python 3.10+**
   部署脚本用 venv 安装 `pynacl`（Secrets 加密用）：`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`。

4. **若本机有 HTTP 代理**（常见于公司网络）
   `git push` 走代理会报 `CONNECT tunnel failed 502`，部署命令前加 `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` 清空代理直连（github.com 直连可达）。详见踩坑第 4 条。

> 完成上面第 2 步拿到 PAT 后，才进入下方「一键部署」——把它作为 `GITHUB_TOKEN` 环境变量传进去即可。

## 一、方案选型（先判客户端版本）

**客户端 5.6.2 起登录态被强制加密**（AtRestEncryption），`auth.accessToken` 变 `{"$wbEncrypted":1,"envelope":"<base64>"}`。这直接决定可用性：

| 方案 | 结论 | 适用 |
|---|---|---|
| `totorosir-workbuddy-checkin`（SkillHub 主流） | ❌ **macOS 不可用**：解密钥逻辑写死 Windows DPAPI，macOS 无 DPAPI 且密钥不落盘；其扫描路径也漏了真实目录 | 仅 Windows |
| 本技能（88lin 路线 + 半云端封装） | ✅ **推荐**：借客户端自带原生接口解密，再封装成云端定时 | macOS / Windows / Linux |

**为什么 totorosir 那套在 macOS 必然失败（勿重复踩）**：macOS 上 `atRestSecretKey` 由客户端进程运行时不落盘（实测全盘扫 41000 文件无命中），无 DPAPI、Keychain 也无对应条目。不要试图逆向硬编码密钥，走本技能路线。

## 二、方案 A：纯云端（GitHub Actions，推荐）

**脱离每日本机依赖**：Mac 关机 / 睡眠 / 出差时也能签到。日常运行完全不依赖本机客户端。

**为什么只能「半云端」**：OIDC 的 issuer/client_id/token_endpoint 在客户端二进制里不是可读字符串（动态拼装或远程下发），登录态密钥不落盘，唯一合法解密途径仍是本地客户端原生接口。故：本机每 ~25 天导出一次 accessToken → 云端每日用该 token 签到。

### 一键部署（推荐）

```bash
cd <SKILL>/scripts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# 若本机有 HTTP 代理，git push 会 502，需清空代理直连：
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  GITHUB_TOKEN='<github_pat_...>' .venv/bin/python deploy_github.py
```

`deploy_github.py` 自动完成：本机解密导出 token → 建私有仓库（已存在则跳过）→ 推送 `cloud_signin.py` + 工作流 → 用仓库公钥（libsodium sealed box）加密写 4 个 Secret → 触发首次验证。token 仅内存、不回显。

**PAT 权限要求**见上方「〇、新机器前置」第 2 步（`Contents`/`Secrets`/`Workflows`/`Administration` 读写 + All repositories）。

### 手动部署（备选）

1. 将 `cloud_signin.py` + `daily-checkin.yml`（yml 放 `.github/workflows/`）推到【私有】仓库；
2. 本机 `python3 export_token.py`，复制输出 JSON；
3. 仓库 Settings → Secrets → Actions 新增 `WB_ACCESS_TOKEN` / `WB_USER_ID` / `WB_DOMAIN` / `WB_ENTERPRISE_ID`（个人账号末项留空）；
4. 手动跑一次 `workflow_dispatch` 验证；之后每天北京 09:00 / 21:00 自动签。

### 维护（唯一本机底线）

- **accessToken 约 30 天寿命**。到期前 ~25 天，本机执行（需 `GITHUB_TOKEN` + 客户端已登录）：
  ```bash
  cd <SKILL>/scripts
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    GITHUB_TOKEN='<pat>' .venv/bin/python refresh_secret.py
  ```
  它只更新 4 个 Secrets、不碰仓库代码（比重跑 `deploy_github.py` 轻量、无 force-push 风险）。
  若也想重推代码（如改了 `cloud_signin.py`/工作流），再重跑 `deploy_github.py`（幂等）。否则云端返回 `AUTH_ERROR`。
- **必须用私有仓库**（fork PR 可读 Secret，公开仓库有泄露风险）。
- GitHub 在仓库 **60 天无 commit 时自动禁用 scheduled**，偶尔 push 或手动重新启用。
- 境外 runner 访问国内 API 实测可通（~1.8s），无需国内 CI。

## 三、方案 B：本机自动化（备选）

依赖本机客户端运行 + 电源保证。适合不希望外传任何凭证的用户。

### 落位与自检

```bash
cp <SKILL>/scripts/signin.py ~/.workbuddy/scripts/        # 若技能外未落位
cd ~/.workbuddy/scripts
<PY> signin.py doctor     # 期望 {"result":"AUTH_READY","credential_format":"sym-v1"}
<PY> signin.py status     # 期望 HTTP 200，data 含 today_checked_in/streak_days/total_credits
<PY> signin.py auto       # 今日已签返回 result=ALREADY，退出码 0
```

`<PY>` 用绝对路径解释器（python3 ≥3.10），如 WorkBuddy 托管路径 `/Users/<user>/.workbuddy/binaries/python/versions/<ver>/bin/python3`。

### 接自动化

用 `automation_update` 建 recurring 任务，命令：
`cd ~/.workbuddy/scripts && <PY> signin.py auto`。
**注意 `rrule` 的 `BYHOUR` 不支持多值**，多点补签需拆条：主签 `FREQ=DAILY;BYHOUR=9;BYMINUTE=0`、补签 `FREQ=DAILY;BYHOUR=21;BYMINUTE=0`。建完用 `date -r <nextRunAt秒>` 核对首次触发（nextRunAt 是毫秒，÷1000）。

### 电源前置（易被忽略）

桌面端自动化是本地定时任务，关机/睡死不执行、错过不补跑。macOS 需保证到点机器是醒的：
```bash
sudo pmset -c sleep 0                              # AC 下禁止空闲休眠（台式机推荐）
sudo pmset repeat wakeorpoweron MTWRFSU 08:55:00   # 定时唤醒兜底
```

## 四、安全红线

- `accessToken` 等同账号密码。`export_token.py` 明文输出仅用于填 Secret，**勿提交代码、勿打印到公开日志、勿外传**。
- 脚本全程不落盘、不打印；汇报时**绝不回显** token 或凭据字段。
- 云端脚本只读环境变量、不回显；日志只输出业务结论（ALREADY/OK + 连签天数/累计积分）。
- 改动脚本前先读源码：确认子进程时限、管道上限、脱敏未被削弱。

## 五、错误码对照

| 返回 | 含义 | 处理 |
|---|---|---|
| `AUTH_READY` | 格式与运行时能力 OK | 继续 |
| `UNSUPPORTED_ENVELOPE` | 加密格式变了 | 更新脚本 |
| `RUNTIME_NOT_FOUND` | 没找到客户端 | 设 `WORKBUDDY_EXE=/Applications/WorkBuddy.app/Contents/MacOS/Electron` |
| `RUNTIME_UNAVAILABLE` | 客户端不暴露 loggerGet | 等脚本更新 |
| `KEY_MISMATCH` | 凭据与所选客户端不匹配 | 多版本共存时用 `WORKBUDDY_EXE` 指定 |
| `DECRYPT_FAILED` / `HELPER_TIMEOUT` | 认证失败 / 子进程超时 | 打开客户端刷新登录，次日重试 |
| `NO_AUTH` / HTTP 401 | 未登录或令牌过期 | 打开客户端刷新，次日自动恢复 |
| `AUTH_ERROR`（云端） | Secret 中 token 失效 | 重跑 `export_token.py` 更新 `WB_ACCESS_TOKEN` |
| `code=10001` | 今日已签 | 正常，非错误 |

## 六、踩坑速查（实测已修）

1. `find_auth_file()` 返回 `(path, looked_in)` 元组，导出脚本取 `[0]`（漏写会 TypeError）。
2. 接口业务字段在 `body.data` 层（`today_checked_in`/`streak_days`/`total_credits`），取顶层会漏判 ALREADY。
3. **GitHub Secrets 公钥是 libsodium sealed box（Curve25519, 32 字节），非 RSA**。`deploy_github.py` 用 `pynacl`：`SealedBox(PublicKey(key, Base64Encoder)).encrypt(val, Base64Encoder)`。用 RSA-OAEP 会 `MalformedFraming`。
4. **本机有 HTTP 代理时 `git push` 走代理报 `CONNECT tunnel failed 502`** → `env -u HTTP_PROXY ...` 清空代理直连（github.com 直连可达）。
5. **fine-grained PAT** `Repository access` 须 All repositories，否则建仓成但 push 403；同名仓库已存在 API 返回 **422**（非 409），脚本需兼容跳过。
6. **`workflow_dispatch` 返回 204 空 body**，API 封装须容忍（勿对 2xx 强制 json.loads）。

## 七、适用版本与边界

- 验证环境：WorkBuddy 桌面端 5.6.2 / macOS（Apple Silicon），Electron 37.10.3。
- 半云端方案已在 GitHub Actions（境外 runner）实测闭环：`ALREADY / streak_days=4 / total_credits=400`。
- 两种方案均幂等，可共存；也可只留云端（关闭本机自动化）。
