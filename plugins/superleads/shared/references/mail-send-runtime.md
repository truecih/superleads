# 定时发信运行时（Gmail）

本参考只服务于 `skills/sending-mail/SKILL.md`，供用户在**明确授权后**配置定时自动发信。它与只读入站能力 `mail.read` 完全分离：发信仅通过 SMTP 提交一封新邮件，不读取、回复、标记、移动、归档或删除邮箱中的任何邮件。

## 1. 能力边界（必须先对用户说清）

- 只在用户**明确配置并确认**收件人、内容、频率后发送；未经确认不自动发。
- 不群发垃圾邮件、不发送未经请求的营销轰炸；收件人总数上限 20。
- 凭据保密：凭据只从环境变量读取，绝不写入配置文件、图谱、schema、日志或对话。
- 默认 `--dry-run`：不传 `--send` 时不会联网，只校验参数并给出摘要。
- 发信脚本不接触邮箱内容，因此不改变现有 `mail.read` 的只读证据语义。

## 2. Gmail 配置步骤

1. 开启账号两步验证：https://myaccount.google.com/security
2. 生成「应用专用密码」：https://myaccount.google.com/apppasswords
   - 该 16 位密码**不是** Gmail 登录密码；不要使用登录密码。
3. 由宿主/DSS 会话在环境中提供（示例，Windows PowerShell）：
   ```powershell
   $env:SUPERLEADS_SMTP_USER = "you@gmail.com"
   $env:SUPERLEADS_SMTP_PASSWORD = "xxxxxxxxxxxxxxxx"  # 应用专用密码
   ```
   定时任务每次执行前需由宿主注入这些环境变量；不要把密码写进脚本或计划文本。

## 3. 环境变量

| 变量 | 必填(发送时) | 说明 |
| --- | --- | --- |
| `SUPERLEADS_SMTP_USER` | 是 | 登录邮箱 |
| `SUPERLEADS_SMTP_PASSWORD` | 是 | Gmail 应用专用密码 |
| `SUPERLEADS_SMTP_FROM` | 否 | 发件人；缺省用 USER |
| `SUPERLEADS_SMTP_HOST` | 否 | 覆盖主机，默认 `smtp.gmail.com` |
| `SUPERLEADS_SMTP_PORT` | 否 | 覆盖端口，默认 `587` |
| `SUPERLEADS_SMTP_SECURITY` | 否 | `starttls`（默认）或 `ssl` |

## 4. 非敏感配置文件（`--config`）

只放主机/端口/收件人/正文等，不放任何密码字段。结构遵循 `../../shared/schemas/mail-send-config.schema.json`。

示例 `mail-config.json`：
```json
{
  "from_addr": "you@gmail.com",
  "to": ["buyer@example.com"],
  "subject": "每周客户线索简报",
  "body_file": "C:/path/to/report.txt"
}
```

## 5. 调用方式

自检（无网络）：
```bash
python scripts/send_mail.py --self-test
```

预演（无网络，只校验）：
```bash
python scripts/send_mail.py --provider gmail --config mail-config.json --dry-run
```

实际发送（必须由定时任务在环境中注入凭据）：
```bash
python scripts/send_mail.py --provider gmail --config mail-config.json --send
```

返回值为 JSON 摘要；失败时返回带 `error_code` 的 JSON。摘要中不含任何凭据或完整正文（仅 200 字预览）。

## 6. 与 DSH 定时任务配合

用户确认收件人、正文和频率后，由宿主创建 DSH 定时任务（`schedule_create`），在任务提示词中写明“使用 sending-mail 技能与已配置的 `mail-config.json` 发送”，并在执行环境中注入上述两个环境变量。定时任务仅执行单封/小批量、有明确业务目的的发送。
