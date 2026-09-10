"""Notifications Slack + Email. Les erreurs de notification n'arrêtent jamais le scanner."""

import smtplib
from email.mime.text import MIMEText
import requests


def send_slack(webhook_url, message):
    if not webhook_url:
        print("[Slack] SLACK_WEBHOOK_URL non configuré — notification ignorée.")
        return
    try:
        resp = requests.post(webhook_url, json={"text": message}, timeout=15)
        resp.raise_for_status()
        print("[Slack] Message envoyé.")
    except Exception as e:
        print(f"[Slack] Échec de l'envoi: {e}")


def send_email(smtp_host, smtp_port, sender, password, recipient, subject, body):
    smtp_host = smtp_host.strip() if isinstance(smtp_host, str) else smtp_host
    smtp_host = smtp_host or "smtp.gmail.com"
    try: smtp_port = int(smtp_port or 465)
    except (TypeError, ValueError): smtp_port = 465
    missing = []
    if not sender: missing.append("EMAIL_SENDER")
    if not password: missing.append("EMAIL_PASSWORD")
    if not recipient: missing.append("EMAIL_RECIPIENT")
    if missing:
        print("[Email] Paramètres incomplets — paramètre(s) manquant(s) : " + ", ".join(missing)); return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject; msg["From"] = sender; msg["To"] = recipient
    try:
        print(f"[Email] Connexion SMTP à {smtp_host}:{smtp_port}...")
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(sender, password); server.sendmail(sender, [recipient], msg.as_string())
        print("[Email] Message envoyé.")
    except smtplib.SMTPAuthenticationError as e:
        print("[Email] Échec d'authentification SMTP. Avec Gmail, utilisez généralement un mot de passe d'application.")
        print(f"[Email] Détail technique : {e}")
    except Exception as e:
        print(f"[Email] Échec de l'envoi: {e}")
