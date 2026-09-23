# WorkBuddy 自动签到（半云端）

每日通过 GitHub Actions 自动完成 WorkBuddy「Buddy 加油站」签到。

## 工作原理
- 本机每 ~25 天运行 `export_token.py` 解密导出 accessToken
- 本仓库 Secrets 保存凭证，Actions 每日用 Bearer 调用签到接口
- 云端脚本 `cloud_signin.py` 完全不依赖本机客户端

## Secret 说明
| 名称 | 来源 |
|---|---|
| WB_ACCESS_TOKEN | 本机 export_token.py 导出 |
| WB_USER_ID | 同上 |
| WB_DOMAIN | 一般 www.workbuddy.cn |
| WB_ENTERPRISE_ID | 个人账号留空 |

## 维护
- accessToken 约 30 天失效，到期前需重跑 export 并更新 Secret
- GitHub 60 天无 commit 会禁用定时任务，偶尔 push 即可
