"""
Notifications — Slack (webhook) + Email (SMTP)

- Slack : envoyé uniquement si le webhook est configuré.
- Email : peut être envoyé à chaque scan.
- Les paramètres SMTP Gmail ont des valeurs par défaut.
- Une erreur de notification ne fait jamais planter le scanner.
"""

import smtplib
from email.mime.text import MIMEText

import requests


# ============================================================
# SLACK
# ============================================================

def send_slack(webhook_url, message):

    if not webhook_url:

        print(
            "[Slack] SLACK_WEBHOOK_URL non configuré "
            "— notification ignorée."
        )

        return

    try:

        resp = requests.post(
            webhook_url,
            json={"text": message},
            timeout=15
        )

        resp.raise_for_status()

        print("[Slack] Message envoyé.")

    except Exception as e:

        print(
            f"[Slack] Échec de l'envoi: {e}"
        )


# ============================================================
# EMAIL
# ============================================================

def send_email(
    smtp_host,
    smtp_port,
    sender,
    password,
    recipient,
    subject,
    body
):

    # --------------------------------------------------------
    # VALEURS PAR DÉFAUT GMAIL
    # --------------------------------------------------------

    smtp_host = (
        smtp_host.strip()
        if isinstance(smtp_host, str)
        else smtp_host
    )

    if not smtp_host:

        smtp_host = "smtp.gmail.com"


    if not smtp_port:

        smtp_port = 465


    try:

        smtp_port = int(smtp_port)

    except (TypeError, ValueError):

        smtp_port = 465


    # --------------------------------------------------------
    # VÉRIFICATION DES PARAMÈTRES OBLIGATOIRES
    # --------------------------------------------------------

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
            f"paramètre(s) manquant(s) : "
            f"{', '.join(missing)}"
        )

        return


    # --------------------------------------------------------
    # CONSTRUCTION DU MESSAGE
    # --------------------------------------------------------

    msg = MIMEText(
        body,
        "plain",
        "utf-8"
    )

    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient


    # --------------------------------------------------------
    # ENVOI SMTP SSL
    # --------------------------------------------------------

    try:

        print(
            f"[Email] Connexion SMTP à "
            f"{smtp_host}:{smtp_port}..."
        )

        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=30
        ) as server:

            server.login(
                sender,
                password
            )

            server.sendmail(
                sender,
                [recipient],
                msg.as_string()
            )


        print(
            "[Email] Message envoyé."
        )


    except smtplib.SMTPAuthenticationError as e:

        print(
            "[Email] Échec d'authentification SMTP. "
            "Vérifiez EMAIL_SENDER et EMAIL_PASSWORD. "
            "Avec Gmail, utilisez généralement un "
            "mot de passe d'application."
        )

        print(
            f"[Email] Détail technique : {e}"
        )


    except Exception as e:

        print(
            f"[Email] Échec de l'envoi: {e}"
        )
