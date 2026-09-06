"""
Notifications — Slack (webhook) + Email (SMTP)
Chaque fonction est "silencieuse" si la config nécessaire est absente,
pour ne jamais faire planter le scan si un seul canal n'est pas configuré.
"""
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
    if not all([smtp_host, sender, password, recipient]):
        print("[Email] Paramètres incomplets — notification ignorée.")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_string())
        print("[Email] Message envoyé.")
    except Exception as e:
        print(f"[Email] Échec de l'envoi: {e}")
