import html
import logging
import smtplib
import socket
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)

def build_support_email_html(
    username: str,
    user_email: str,
    category: str,
    subject: str,
    message: str,
    timestamp_str: str,
) -> str:
    escaped_message = html.escape(message).replace("\n", "<br/>")
    escaped_subject = html.escape(subject)
    escaped_username = html.escape(username)
    escaped_email = html.escape(user_email)
    escaped_category = html.escape(category.title())

    category_colors = {
        "Bug": "#f87171",
        "Feature": "#60a5fa",
        "Question": "#fbbf24",
        "Feedback": "#34d399",
    }
    badge_color = category_colors.get(escaped_category, "#a78bfa")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8"/>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0e14; color: #e1e2ec; margin: 0; padding: 24px; }}
        .card {{ max-width: 620px; margin: 0 auto; background: #151821; border: 1px solid #232733; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }}
        .header {{ background: linear-gradient(135deg, #0d1e3d 0%, #151821 100%); padding: 24px; border-bottom: 1px solid #232733; }}
        .header h2 {{ margin: 0 0 6px 0; color: #adc6ff; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }}
        .header p {{ margin: 0; color: #8c909f; font-size: 13px; }}
        .body {{ padding: 24px; }}
        .meta-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
        .meta-table td {{ padding: 8px 0; font-size: 14px; border-bottom: 1px solid #1f232f; }}
        .meta-table td.label {{ color: #8c909f; width: 110px; font-weight: 500; }}
        .meta-table td.value {{ color: #e1e2ec; font-weight: 600; }}
        .badge {{ display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 12px; font-weight: 700; background: {badge_color}22; color: {badge_color}; border: 1px solid {badge_color}44; }}
        .message-box {{ background: #0b0e14; border: 1px solid #232733; border-radius: 8px; padding: 18px; margin-top: 14px; font-size: 14px; line-height: 1.65; color: #f1f2f8; word-break: break-word; }}
        .footer {{ padding: 16px 24px; background: #0f121a; border-top: 1px solid #1f232f; font-size: 12px; color: #697082; text-align: center; }}
        .footer a {{ color: #adc6ff; text-decoration: none; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <h2>Stock Sentinel Support Ticket</h2>
          <p>Direct user inquiry submitted from Stock Sentinel</p>
        </div>
        <div class="body">
          <table class="meta-table">
            <tr>
              <td class="label">Category</td>
              <td class="value"><span class="badge">{escaped_category}</span></td>
            </tr>
            <tr>
              <td class="label">Submitted By</td>
              <td class="value">{escaped_username} &lt;{escaped_email}&gt;</td>
            </tr>
            <tr>
              <td class="label">Subject</td>
              <td class="value">{escaped_subject}</td>
            </tr>
            <tr>
              <td class="label">Submitted At</td>
              <td class="value">{timestamp_str} UTC</td>
            </tr>
          </table>

          <div style="font-weight: 600; color: #adc6ff; margin-bottom: 4px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em;">Message</div>
          <div class="message-box">
            {escaped_message}
          </div>
        </div>
        <div class="footer">
          Hit <strong>Reply</strong> in your email client to respond directly to <a href="mailto:{escaped_email}">{escaped_email}</a>.
        </div>
      </div>
    </body>
    </html>
    """

def send_via_smtp(
    to_email: str,
    reply_to: str,
    subject: str,
    html_content: str,
) -> Dict[str, Any]:
    """Send email directly using standard Python SMTP (e.g. Gmail App Password).
    Includes automatic retries for transient DNS/network glitches and port 465/587 compatibility.
    """
    user = settings.SMTP_USER
    password = settings.SMTP_PASSWORD
    host = settings.SMTP_HOST or "smtp.gmail.com"
    port = int(settings.SMTP_PORT or 587)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Stock Sentinel Support <{user}>"
    msg["To"] = to_email
    msg["Reply-To"] = reply_to

    plain_text = f"{subject}\n\nSubmitted by: {reply_to}\n(Please reply directly to this email to respond)"
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    max_attempts = 3
    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            if port == 465:
                server_ctx = smtplib.SMTP_SSL(host, port, timeout=15.0)
            else:
                server_ctx = smtplib.SMTP(host, port, timeout=15.0)

            with server_ctx as server:
                if port != 465 and settings.SMTP_USE_TLS:
                    server.starttls()
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
                logger.info(f"[SMTP] Support email successfully delivered to {to_email} via {host}:{port}")
                return {"status": "success", "provider": "smtp"}
        except (socket.gaierror, socket.timeout, OSError, smtplib.SMTPConnectError) as exc:
            last_err = exc
            logger.warning(
                f"[SMTP Warning] Transient connection/DNS failure on attempt {attempt}/{max_attempts} "
                f"({exc}). Retrying in 1.5s..."
            )
            time.sleep(1.5)
        except Exception as exc:
            logger.error(f"[SMTP Error] Non-retryable SMTP error ({host}:{port}): {exc}")
            return {"status": "error", "provider": "smtp", "detail": str(exc)}

    logger.error(f"[SMTP Error] Failed to send email via SMTP after {max_attempts} attempts: {last_err}")
    return {"status": "error", "provider": "smtp", "detail": str(last_err)}


def send_support_email(
    username: str,
    user_email: str,
    category: str,
    subject: str,
    message: str,
) -> Dict[str, Any]:
    """Send user support request to developer via configured SMTP."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    html_content = build_support_email_html(
        username=username,
        user_email=user_email,
        category=category,
        subject=subject,
        message=message,
        timestamp_str=now_utc,
    )

    formatted_subject = f"Stock Sentinel Support - {subject}"
    to_email = settings.DEV_NOTIFICATION_EMAIL or settings.SMTP_USER

    # Standard SMTP (Gmail App Password)
    if settings.SMTP_USER and settings.SMTP_PASSWORD:
        return send_via_smtp(
            to_email=to_email,
            reply_to=user_email,
            subject=formatted_subject,
            html_content=html_content,
        )

    # Fallback: Local simulation mock
    logger.warning(
        f"[Email Mock] SMTP credentials (SMTP_USER/SMTP_PASSWORD) are not configured. "
        f"Logged support email from {username} <{user_email}>: [{category}] {subject}"
    )
    return {
        "status": "success",
        "simulated": True,
        "message": "Message logged locally (no email credentials configured)",
    }
