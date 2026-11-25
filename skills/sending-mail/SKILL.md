---
name: sending-mail
description: "Use when the user explicitly asks to send an outbound email or to configure scheduled automatic email sending (daily/weekly reports, follow-ups) via the Superleads Gmail SMTP sender. Not for reading, replying to, or processing inbound mail (that is the read-only mail.read / using-superleads intake path). Requires explicit recipient/content/frequency confirmation and environment-supplied credentials."
---

# 通过 Gmail 发送 / 定时发送邮件

仅在用户**明确要求发信或配置定时自动发信**时使用本技能。入站邮件的读取与线索化属于只读 `mail.read` 路径，不走这里；本能力只提交一封新邮件，不读取、回复、标记、移动或删除邮箱中的邮件。

## 前置条件（缺一不可）

1. 用户已明确给出并确认：收件人、邮件内容、发送频率（或单次发送）。
2. 用户使用个人 Gmail，并已开启两步验证、生成「应用专用密码」。
3. 宿主在环境中提供 `SUPERLEADS_SMTP_USER` 与 `SUPERLEADS_SMTP_PASSWORD`（应用专用密码）；**不得**让用户把密码写进配置、图谱、对话或任何文件。

未满足以上条件时，只说明需要哪些信息，不猜测、不擅自发送。

## 执行

按 `../../shared/references/mail-send-runtime.md` 完成配置与调用，要点：

- 非敏感配置写入符合 `../../shared/schemas/mail-send-config.schema.json` 的 JSON（禁止任何密码/令牌字段）。
- 先运行 `python scripts/send_mail.py --self-test` 做本地自检。
- 再运行 `--dry-run` 预演并核对收件人、主题、正文摘要。
- 仅在实际发送时加 `--send`；默认 `--dry-run` 不会联网。
- 收件人总数上限 20；不群发垃圾邮件，不做未经请求的营销轰炸。

## 定时自动发送

用户确认频率后，由宿主创建 DSH 定时任务（`schedule_create`），在任务提示词中写明使用本技能与已配置文件，并在执行环境注入两个凭据环境变量。定时任务只执行有明确业务目的的单封/小批量发送。

## 边界

- 凭据保密：不回显、不落盘、不写入图谱或 schema；失败摘要只含错误码。
- 不改变 `mail.read` 只读证据语义；不发信前不声称已发送。
- 仅返回有界 JSON 摘要（收件人数、主机、主题、是否发送），不输出完整正文或凭据。
