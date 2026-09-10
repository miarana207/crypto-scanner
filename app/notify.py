"""
V4.2 — Notifications Slack + email.

Fonctions :
- Envoi d'alertes Slack via webhook.
- Envoi d'emails via SMTP.
- Support SMTP SSL (port 465).
- Support SMTP STARTTLS (port 587).
- Gestion robuste des erreurs.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from typing import Optional

import requests


def send_slack(message: str) -> bool:
    """
    Envoie un message vers Slack via webhook.

    Retourne :
        True  = envoi réussi
        False = webhook absent ou erreur
    """

    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()

    if not url:
        print("[Slack] SLACK_WEBHOOK_URL absent : notification ignorée.")
        return False

    if not message:
        print("[Slack] message vide : notification ignorée.")
        return False

    try:
        response = requests.post(
            url,
            json={"text": message},
            timeout=15,
        )

        response.raise_for_status()

        print("[Slack] notification envoyée.")
        return True

    except requests.RequestException as exc:
        print(f"[Slack] erreur : {exc}")
        return False

    except Exception as exc:
        print(f"[Slack] erreur inattendue : {exc}")
        return False


def send_email(
    subject: str,
    body: str,
) -> bool:
    """
    Envoie un email via SMTP.

    Variables d'environnement utilisées :
        EMAIL_SENDER
        EMAIL_PASSWORD
        EMAIL_RECIPIENT
        SMTP_HOST
        SMTP_PORT

    Ports supportés :
        465 = SMTP SSL
        587 = SMTP STARTTLS
        autre = SMTP standard

    Retourne :
        True  = email envoyé
        False = erreur ou configuration incomplète
    """

    sender = os.getenv("EMAIL_SENDER", "").strip()
    password = os.getenv("EMAIL_PASSWORD", "")
    recipient = os.getenv("EMAIL_RECIPIENT", "").strip()

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port_raw = os.getenv("SMTP_PORT", "465").strip()

    # ------------------------------------------------------------------
    # Validation de la configuration
    # ------------------------------------------------------------------

    if not sender:
        print("[Email] EMAIL_SENDER absent.")
        return False

    if not password:
        print("[Email] EMAIL_PASSWORD absent.")
        return False

    if not recipient:
        print("[Email] EMAIL_RECIPIENT absent.")
        return False

    if not smtp_host:
        print("[Email] SMTP_HOST absent.")
        return False

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        print(f"[Email] SMTP_PORT invalide : {smtp_port_raw}")
        return False

    # ------------------------------------------------------------------
    # Construction du message
    # ------------------------------------------------------------------

    msg = MIMEText(
        body or "",
        "plain",
        "utf-8",
    )

    msg["Subject"] = subject or "V4.2 Trading Scanner"
    msg["From"] = sender
    msg["To"] = recipient

    # ------------------------------------------------------------------
    # Envoi SMTP
    # ------------------------------------------------------------------

    try:

        # --------------------------------------------------------------
        # Port 465 : SMTP SSL
        # --------------------------------------------------------------

        if smtp_port == 465:

            with smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                timeout=20,
            ) as smtp:

                smtp.login(
                    sender,
                    password,
                )

                smtp.sendmail(
                    sender,
                    [recipient],
                    msg.as_string(),
                )

        # --------------------------------------------------------------
        # Port 587 : SMTP + STARTTLS
        # --------------------------------------------------------------

        elif smtp_port == 587:

            with smtplib.SMTP(
                smtp_host,
                smtp_port,
                timeout=20,
            ) as smtp:

                smtp.ehlo()

                smtp.starttls()

                smtp.ehlo()

                smtp.login(
                    sender,
                    password,
                )

                smtp.sendmail(
                    sender,
                    [recipient],
                    msg.as_string(),
                )

        # --------------------------------------------------------------
        # Autres ports : SMTP standard
        # --------------------------------------------------------------

        else:

            with smtplib.SMTP(
                smtp_host,
                smtp_port,
                timeout=20,
            ) as smtp:

                smtp.ehlo()

                smtp.login(
                    sender,
                    password,
                )

                smtp.sendmail(
                    sender,
                    [recipient],
                    msg.as_string(),
                )

        print("[Email] notification envoyée.")
        return True

    except smtplib.SMTPAuthenticationError as exc:
        print(f"[Email] authentification SMTP échouée : {exc}")
        return False

    except smtplib.SMTPException as exc:
        print(f"[Email] erreur SMTP : {exc}")
        return False

    except OSError as exc:
        print(f"[Email] erreur réseau/connexion : {exc}")
        return False

    except Exception as exc:
        print(f"[Email] erreur inattendue : {exc}")
        return False


__all__ = [
    "send_slack",
    "send_email",
]
```
