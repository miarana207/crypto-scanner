"""V4.2 — Notifications Slack + Email.

Les erreurs de notification ne doivent jamais arrêter le scanner.
"""

import smtplib
from email.mime.text import MIMEText

import requests


def send_slack(webhook_url, message):
    """Envoie un message Slack sans faire échouer le scanner."""

    if not webhook_url:
        print(
            "[Slack] SLACK_WEBHOOK_URL non configuré — "
            "notification ignorée."
        )
        return False

    if not message:
        print(
            "[Slack] Message vide — notification ignorée."
        )
        return False

    try:
        response = requests.post(
            webhook_url,
            json={"text": str(message)},
            timeout=15,
        )

        response.raise_for_status()

        print("[Slack] Message envoyé.")
        return True

    except requests.RequestException as error:
        print(
            f"[Slack] Échec de l'envoi : {error}"
        )
        return False

    except Exception as error:
        print(
            f"[Slack] Erreur inattendue : {error}"
        )
        return False


def send_email(
    smtp_host,
    smtp_port,
    sender,
    password,
    recipient,
    subject,
    body,
):
    """Envoie un email sans faire échouer le scanner."""

    smtp_host = (
        smtp_host.strip()
        if isinstance(smtp_host, str)
        else smtp_host
    )

    smtp_host = smtp_host or "smtp.gmail.com"

    try:
        smtp_port = int(smtp_port or 465)
    except (TypeError, ValueError):
        smtp_port = 465

    missing = []

    if not sender:
        missing.append("EMAIL_SENDER")

    if not password:
        missing.append("EMAIL_PASSWORD")

    if not recipient:
        missing.append("EMAIL_RECIPIENT")

    if missing:
        print(
            "[Email] Paramètres incomplets — "
            "paramètre(s) manquant(s) : "
            + ", ".join(missing)
        )
        return False

    msg = MIMEText(
        str(body or ""),
        "plain",
        "utf-8",
    )

    msg["Subject"] = str(
        subject or "Crypto Scanner V4.2"
    )

    msg["From"] = sender
    msg["To"] = recipient

    try:
        print(
            f"[Email] Connexion SMTP à "
            f"{smtp_host}:{smtp_port}..."
        )

        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=30,
        ) as server:

            server.login(
                sender,
                password,
            )

            server.sendmail(
                sender,
                [recipient],
                msg.as_string(),
            )

        print("[Email] Message envoyé.")
        return True

    except smtplib.SMTPAuthenticationError as error:
        print(
            "[Email] Échec d'authentification SMTP. "
            "Avec Gmail, utilisez généralement "
            "un mot de passe d'application."
        )
        print(
            f"[Email] Détail technique : {error}"
        )
        return False

    except smtplib.SMTPException as error:
        print(
            f"[Email] Erreur SMTP : {error}"
        )
        return False

    except Exception as error:
        print(
            f"[Email] Échec de l'envoi : {error}"
        )
        return False
