# Telegram Mail Plugin

专用于 Telegram 的 AstrBot 邮箱助手插件，优先使用 IMAP IDLE 实时监听新邮件，不支持时自动回退为定时轮询，并通过 Telegram inline button 展示附件、全文分页和常用邮件操作。

## 功能

- 多邮箱账号配置，按 Telegram 用户 ID 独立保存。
- IMAP IDLE 实时监听新邮件并推送到指定 Telegram `chat_id`。
- 邮箱服务端不支持 IMAP IDLE 或连接异常时自动回退为定时轮询。
- 邮件卡片展示发件人、主题、时间、正文预览和附件数量。
- `Attachments` 按钮列出附件并按需发送。
- `More` 按钮展示全文并支持 Prev/Next 翻页。
- `Action` 按钮支持 Reply、Unsubscribe、Block Sender、Archive、Delete、Mark Read、Mark Unread。
- SMTP 支持 `/mail send` 新建邮件和 `/mail reply` 回复邮件。
- Outlook 账号可通过 OAuth2 登录，支持 `provider: "outlook"` 或 `auth_type: "oauth2"`；Microsoft 的 `oauth2_client_id` 和 `oauth2_client_secret` 可以写在插件设置里作为默认值，添加账号时也可以选择手动覆盖；保存账号后可用 `/mail oauth <account_id>` 让 bot 输出授权链接，用户打开浏览器授权后插件会自动保存并刷新 token。

## 配置

邮箱账号不要写入插件设置。请由需要使用邮箱的用户私聊 bot 执行 `/mail add`，然后按提示依次选择邮箱类型、输入账号和必要参数；插件会按用户 ID 保存账号凭据、OAuth token、邮件按钮上下文和屏蔽列表。用户 ID 取 AstrBot `unified_msg_origin` 的最后一段；不同平台和 bot 实例不会再共享同一份邮箱状态。

插件设置中可以提供 Microsoft OAuth 默认值：

```json
{
  "oauth2_client_id": "your-app-client-id",
  "oauth2_client_secret": "optional-app-secret"
}
```

Gmail 示例：

```json
[
  {
    "account_id": "gmail-main",
    "display_name": "Gmail Main",
    "enabled": true,
    "target_chat_id": "123456789",
    "platform_id": "telegram",
    "message_type": "friend",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "imap_tls": true,
    "imap_user": "your@gmail.com",
    "imap_password": "app-password",
    "imap_folders": ["INBOX"],
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_tls": "ssl",
    "smtp_user": "your@gmail.com",
    "smtp_password": "app-password",
    "from_address": "your@gmail.com",
    "archive_folder": "[Gmail]/All Mail",
    "trash_folder": "[Gmail]/Trash",
    "poll_interval": 300,
    "realtime_enabled": true,
    "idle_timeout": 1740
  }
}
```

添加 Gmail 账号时，命令会逐步询问：

1. 邮箱类型，输入 `gmail`。
2. 账号邮箱。
3. 密码或应用专用密码。
4. 目标会话 ID。

如果你仍想直接粘贴 JSON，命令也保留兼容入口。

执行时压成一行：

```text
/mail add {"account_id":"gmail-main","display_name":"Gmail Main","enabled":true,"target_chat_id":"123456789","platform_id":"telegram","message_type":"friend","imap_host":"imap.gmail.com","imap_port":993,"imap_tls":true,"imap_user":"your@gmail.com","imap_password":"app-password","imap_folders":["INBOX"],"smtp_host":"smtp.gmail.com","smtp_port":465,"smtp_tls":"ssl","smtp_user":"your@gmail.com","smtp_password":"app-password","from_address":"your@gmail.com","archive_folder":"[Gmail]/All Mail","trash_folder":"[Gmail]/Trash","poll_interval":300,"realtime_enabled":true,"idle_timeout":1740}
```

Outlook 示例：

```json
{
  "account_id": "outlook-main",
  "display_name": "Outlook Main",
  "provider": "outlook",
  "enabled": true,
  "target_chat_id": "123456789",
  "platform_id": "telegram",
  "message_type": "friend",
  "imap_user": "your@outlook.com",
  "imap_folders": ["INBOX"],
  "smtp_user": "your@outlook.com",
  "oauth2_client_id": "your-app-client-id",
  "oauth2_client_secret": "optional-app-secret"
}
```

`provider: "outlook"` 会默认使用 Microsoft 文档中的 IMAP/SMTP 设置：IMAP `outlook.office365.com:993` SSL/TLS，SMTP `smtp-mail.outlook.com:587` STARTTLS，并默认启用 OAuth2。保存账号后执行 `/mail oauth outlook-main`，插件会返回 Microsoft 授权链接和一次性代码；用户授权完成后，access token / refresh token 会保存到插件数据目录的 `state.json` 用户分桶中，后续过期会自动刷新。

插件仍会读取旧版本插件设置中的 `accounts_json` 以便兼容迁移，但不建议继续使用。旧配置属于全局账号，不能做到用户隔离。

群聊可将 `target_chat_id` 设置为 Telegram 负数群 ID，并将 `message_type` 设置为 `group`。话题群可使用 `chat_id#thread_id`。

`realtime_enabled` 默认开启。开启后插件会为每个 `imap_folders` 文件夹尝试使用 IMAP IDLE；如果服务端不支持或监听失败，会按 `poll_interval` 定时抓取。`idle_timeout` 用于定期刷新 IDLE 连接，账号未配置时默认 1740 秒。

## 命令

- `/mail status` 查看账号状态。
- `/mail check [account_id]` 立即检查新邮件。
- `/mail add` 进入交互式添加流程，支持 `gmail`、`outlook`、`qq`。
- `/mail remove <account_id>` 删除当前用户的邮箱账号和本地状态。
- `/mail send <account_id> <to> | <subject> | <body>` 发送新邮件。
- `/mail reply <token> <body>` 回复按钮提示中的邮件。
- `/mail oauth <account_id>` 发起 OAuth2 浏览器授权。
- `/mail blocklist [account_id]` 查看本地屏蔽列表。
- `/mail unblock <account_id> <sender-or-domain>` 解除本地屏蔽。

## 安全说明

- `Unsubscribe` 只展示退订链接或 mailto，不会自动请求外部链接。
- `Block Sender` 是插件本地屏蔽，不会创建邮箱服务端规则。
- 密码应使用邮箱服务商提供的应用专用密码。
- 不要把邮箱密码或 OAuth token 写入插件设置；使用 `/mail add` 按用户保存。Microsoft OAuth client 可以放在插件设置里作为全局默认值。
