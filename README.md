# Telegram Mail Plugin

专用于 Telegram 的 AstrBot 邮箱助手插件，支持 IMAP IDLE 或定时轮询检查新邮件，并通过 Telegram inline button 展示附件、全文分页和常用邮件操作。

## 功能

- 多邮箱账号配置，按 Telegram 用户 ID 独立保存。
- IMAP IDLE 或定时轮询检查新邮件并推送到指定 Telegram `chat_id`。
- 邮箱服务端不支持 IMAP IDLE 或连接异常时自动回退为定时轮询；Outlook/OAuth2 账号实时模式固定使用账号级轮询，避免多个文件夹同时维持 IMAP IDLE 会话。
- IMAP、SMTP、OAuth 的常见网络或认证异常会转换为可读错误；后台可恢复错误只记录 warning，并可在 `/mail status` 中查看最近错误。
- IMAP 文件夹名称会按协议正确转义，QQ 邮箱等服务的 `Sent Messages` 等带空格文件夹可正常检查和操作。
- 邮件卡片使用 Telegram Markdown 展示主题、发件人、时间和正文预览，并会转义邮件动态内容，避免格式被邮件正文破坏。
- 附件直接显示在主邮件卡片按钮上，点击后按需发送对应附件；旧的 `Attachments` 回调入口仍保留兼容。
- `More` 按钮展示全文并支持 Prev/Next 翻页。
- `Action` 按钮支持 Reply、Unsubscribe、Block Sender、Archive、Delete、Mark Read、Mark Unread。
- 邮件卡片的回调编辑消息会关闭网页预览，避免长网址触发 Telegram 预览截断问题。
- SMTP 支持 `/mail send` 新建邮件和 `/mail reply` 回复邮件。
- 邮件正文展示前会先过滤常见的 HTML/CSS 噪音和重复头部片段，避免正文开头被一大段无效内容淹没。
- 仅含 HTML 的邮件会将正文中的超链接渲染为 Telegram MarkdownV2 内联链接 `[文字](链接)`，避免把原始 URL 直接拼进消息造成刷屏，链接在 Telegram 中点击即可跳转。
- Outlook 账号可通过 OAuth2 登录，支持 `provider: "outlook"` 或 `auth_type: "oauth2"`；Microsoft 的 `oauth2_client_id` 可以写在插件设置里作为默认值，添加账号时也可以选择手动覆盖；保存账号后可用 `/mail oauth <account_id>` 让 bot 输出授权链接，用户打开浏览器授权后插件会自动保存并刷新 token。Outlook/Microsoft 账号未覆盖插件设置时默认使用 `imap_folder_mode: "auto"` 自动发现邮件文件夹。

## 配置

邮箱账号不要写入插件设置。请由需要使用邮箱的用户私聊 bot 执行 `/mail add`，然后按提示依次选择邮箱类型、输入账号和必要参数；插件会按用户 ID 保存账号凭据、OAuth token、邮件按钮上下文和屏蔽列表。

插件设置中可以提供 Microsoft OAuth 默认值：

```json
{
  "oauth2_client_id": "your-app-client-id"
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
  "oauth2_client_id": "your-app-client-id"
}
```

`provider: "outlook"` 会默认使用 Microsoft 文档中的 IMAP/SMTP 设置：IMAP `outlook.office365.com:993` SSL/TLS，SMTP `smtp-mail.outlook.com:587` STARTTLS，并默认启用 OAuth2。保存账号后执行 `/mail oauth outlook-main`，插件会返回 Microsoft 授权链接和一次性代码；用户授权完成后，access token / refresh token 会保存在插件数据目录下的 SQLite 状态库中，后续 access token 过期会用 refresh token 自动刷新。

### 默认 IMAP 文件夹抓取模式

插件设置可控制所有未在账号 JSON 中指定 `imap_folder_mode` 的账号。账号 JSON 中的同名字段优先级更高。

`configured` 只抓取 `imap_folders` 中的文件夹，适合只接收收件箱：

```json
{
  "imap_folder_mode": "configured"
}
```

### 📬 Auto 模式：包含垃圾邮件

`auto` 通过 IMAP `LIST` 自动发现可收件文件夹，适合有规则自动分类或需要接收垃圾邮件误判的场景。它保留 `Junk`、`Junk Email`、`Spam` 等垃圾邮件文件夹，并排除 Sent、Drafts、Trash、Archive、All Mail、Outbox、Sync Issues 等非收件或易重复文件夹。成功发现的结果默认缓存 3600 秒，避免每次轮询额外建立 `LIST` 会话。

```json
{
  "imap_folder_mode": "auto",
  "imap_folder_refresh_interval": 3600
}
```

自动发现失败时会回退到账号的 `imap_folders`。例如只希望回退到收件箱：

```json
{
  "imap_folders": ["INBOX"]
}
```

Microsoft access token 通常是短期有效，refresh token 因为请求了 `offline_access` 才会返回。Microsoft 在刷新时可能返回新的 refresh token；插件会用新 refresh token 覆盖旧值，如果刷新响应只包含新的 access token，则保留当前已保存的 refresh token。OAuth2 access token 缓存和刷新锁会按用户、账号 ID、邮箱地址隔离，避免不同用户使用相同 `account_id` 时串用 token；完成 `/mail oauth <account_id>` 授权后也会清除旧 access-token 缓存。

Outlook/OAuth2 账号即使开启 `realtime_enabled`，也不会为每个文件夹创建 IMAP IDLE watcher，而是按账号执行一次轮询，串行扫描解析后的所有文件夹，然后按 `poll_interval` 休眠；`/mail status` 中会显示 `mode=oauth2 polling`。这个模式牺牲 IMAP IDLE 的即时性，换取多文件夹和多个 Outlook 账号下更稳定的 OAuth2 IMAP 连接。若 Outlook IMAP 返回 `User is authenticated but not connected`，先在 Outlook 网页版的 **Settings > Mail > Forwarding and IMAP** 开启 IMAP 并保存；若账号已配置在多个 IMAP 客户端，还应在 Microsoft [Recent activity](https://account.live.com/activity) 中确认对应会话为本人操作。插件会在首次失败后进行指数退避，避免持续建立会话和刷屏日志；成功连接后会自动恢复正常轮询。仅当 token 刷新报 `invalid_grant`、`AADSTS700082` 或其它明确的 AADSTS token 过期/撤销信息时，才需要重新执行 `/mail oauth <account_id>`。

群聊可将 `target_chat_id` 设置为 Telegram 负数群 ID，并将 `message_type` 设置为 `group`。话题群可使用 `chat_id#thread_id`。

`realtime_enabled` 默认开启。密码账号开启后插件会为解析后的监听文件夹尝试使用 IMAP IDLE；如果服务端不支持或监听失败，会按 `poll_interval` 定时抓取。Outlook/OAuth2 账号开启后使用账号级轮询，不使用多文件夹 IDLE。`idle_timeout` 用于密码账号定期刷新 IDLE 连接，账号未配置时默认 1740 秒；插件内部会将该时长切成 60 秒一片执行，每片结束后释放后台线程，避免多个 IDLE 文件夹长期占满线程池。若部分邮箱服务商频繁断开或拒绝 IDLE，可开启 `disable_idle_on_error`；开启后同一账号在本次 Bot 启动期间只要出现实时监听相关错误，就会记录日志并停止继续尝试该账号的 IDLE，直到 Bot 重启后再恢复尝试。`/mail status` 会显示当前 `folder_mode` 和解析后的文件夹摘要。

插件会把 IMAP、SMTP 和 OAuth 等阻塞型邮箱网络调用放在独立线程池中执行，避免邮箱服务器或网络异常时占用 AstrBot 默认线程资源并影响 WebUI 或其它渠道连接。默认邮箱网络超时为 15 秒，默认后台线程数为 4；如账号或自动发现文件夹较多，可在插件设置中调整：

```json
{
  "network_timeout": 15,
  "max_workers": 4,
  "disable_idle_on_error": false
}
```

已有 `last_check` 的账号在检查新 UID 时会额外校验邮件头 `Date`。如果邮件 `Date` 早于上次检查时间超过 `historical_mail_grace_seconds` 秒，插件会把该 UID 标记为已处理但不推送，避免 IMAP 重连、UID 状态异常或服务端返回旧 UID 时刷出历史邮件。默认宽限为 86400 秒；如需更严格或更宽松，可在插件设置中调整：

```json
{
  "historical_mail_grace_seconds": 86400
}
```

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
- 不要把邮箱密码或 OAuth token 写入插件设置；使用 `/mail add` 按用户保存。Microsoft OAuth client ID 可以放在插件设置里作为全局默认值。
