"""
邮件通知模块（Gmail API）
通过 Gmail API + OAuth2 发送邮件，绕过 SMTP 端口封锁。
供比赛监控和纪录监控共用。

首次使用需要：
1. 在 Google Cloud Console 创建项目并启用 Gmail API
2. 创建 OAuth2 凭据（桌面应用），下载 credentials.json 到本目录
3. 运行本脚本（python email_notifier.py），浏览器会弹出授权页面
4. 授权后会自动生成 token.json，后续无需再次授权
"""

import base64
import logging
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_PATH = SCRIPT_DIR / "credentials.json"
TOKEN_PATH = SCRIPT_DIR / "token.json"

# Gmail API 的权限范围：仅需发送邮件
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _get_gmail_service():
    """
    获取已认证的 Gmail API 服务实例。
    首次运行会弹出浏览器让用户授权，之后使用缓存的 token。
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # token 过期或不存在时重新授权
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                log.error("credentials.json 不存在，请先配置 Gmail API")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)

        # 保存 token 供后续使用
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    # NOTE: cache_discovery=False 避免 file_cache 兼容性警告
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_email(cfg: dict, subject: str, body: str, recipients_key: str = "email_recipients"):
    """
    通过 Gmail API 发送纯文本邮件。

    recipients_key 指定从 cfg 中读取哪个收件人列表字段名。
    找不到指定字段时，自动回退到 "email_recipients"（向后兼容）。
    email_enabled 为 false 或缺失时静默跳过。
    发送失败只 log warning，不抛异常，不影响主流程。
    """
    if not cfg.get("email_enabled"):
        return

    # NOTE: 优先使用指定 key，找不到则回退到通用 email_recipients
    recipients = cfg.get(recipients_key) or cfg.get("email_recipients", [])
    sender = cfg.get("email_sender", "me")

    if not recipients:
        log.warning("%s 为空，跳过发送", recipients_key)
        return

    try:
        service = _get_gmail_service()
        if not service:
            return

        for recipient in recipients:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = recipient

            # Gmail API 要求 base64url 编码的原始邮件
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()

        log.info("📧 邮件已发送: %s", subject)
    except Exception as e:
        log.warning("邮件发送失败: %s", e)


if __name__ == "__main__":
    # 独立运行时，执行 OAuth2 授权流程
    print("=== Gmail API 授权 ===")
    print("浏览器将弹出 Google 授权页面，请登录并允许访问。\n")
    service = _get_gmail_service()
    if service:
        print(f"\n✅ 授权成功！token 已保存到 {TOKEN_PATH}")
        print("邮件通知模块已就绪。")
    else:
        print("\n❌ 授权失败，请检查 credentials.json")
