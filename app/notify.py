```python
"""
V4.2 — Notifications Slack + Email.

Principes :
- Les erreurs de notification n'arrêtent JAMAIS le scanner.
- Slack utilise un timeout et vérifie explicitement la réponse HTTP.
- Email utilise SMTP sécurisé.
- Les paramètres manquants sont détectés proprement.
- Les erreurs d'authentification sont distinguées des autres erreurs.
- Les messages peuvent être longs sans faire planter le scanner.
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.header import Header

import requests


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

SLACK_TIMEOUT = 15
SMTP_TIMEOUT = 30

# Slack limite la taille des messages. On reste volontairement sous une
# limite prudente afin d'éviter les erreurs de payload.
SLACK_MAX_MESSAGE_LENGTH = 39000


# ---------------------------------------------------------------------------
# UTILITAIRES
# ---------------------------------------------------------------------------

def _safe_text(value, default=""):
    """
    Convertit proprement une valeur en texte.
    """
    if value is None:
        return default

    try:
        return str(value)
    except Exception:
        return default


def _truncate_message(message, max_length):
    """
    Tronque proprement un message trop long.
    """
    text = _safe_text(message)

    if len(text) <= max_length:
        return text

    suffix = "\n\n[Message tronqué automatiquement par V4.2]"

    available = max_length - len(suffix)

    if available <= 0:
        return text[:max_length]

    return text[:available] + suffix


# ---------------------------------------------------------------------------
# SLACK
# ---------------------------------------------------------------------------

def send_slack(webhook_url, message):
    """
    Envoie une notification Slack.

    Une erreur Slack ne provoque JAMAIS d'exception vers le scanner.
    """

    webhook_url = _safe_text(
        webhook_url
    ).strip()

    if not webhook_url:

        print(
            "[Slack] "
            "SLACK_WEBHOOK_URL non configuré "
            "— notification ignorée."
        )

        return False

    text = _truncate_message(
        message,
        SLACK_MAX_MESSAGE_LENGTH,
    )

    payload = {
        "text": text
    }

    try:

        response = requests.post(
            webhook_url,
            json=payload,
            timeout=SLACK_TIMEOUT,
        )

        response.raise_for_status()

        print(
            "[Slack] Message envoyé."
        )

        return True

    except requests.exceptions.Timeout:

        print(
            "[Slack] "
            "Timeout lors de l'envoi "
            "— scanner poursuivi."
        )

        return False

    except requests.exceptions.HTTPError as exc:

        response_text = ""

        try:
            response_text = response.text[:500]
        except Exception:
            pass

        print(
            "[Slack] "
            f"Erreur HTTP : {exc}"
        )

        if response_text:
            print(
                f"[Slack] Réponse : "
                f"{response_text}"
            )

        return False

    except requests.exceptions.RequestException as exc:

        print(
            "[Slack] "
            f"Erreur réseau : {exc}"
        )

        return False

    except Exception as exc:

        print(
            "[Slack] "
            f"Échec inattendu : {exc}"
        )

        return False


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def send_email(
    smtp_host,
    smtp_port,
    sender,
    password,
    recipient,
    subject,
    body,
):
    """
    Envoie un email via SMTP SSL.

    Une erreur email ne provoque JAMAIS d'exception vers le scanner.

    Retourne :
        True  -> email envoyé
        False -> échec / configuration incomplète
    """

    # ---------------------------------------------------------------
    # SMTP HOST
    # ---------------------------------------------------------------

    if isinstance(smtp_host, str):

        smtp_host = smtp_host.strip()

    if not smtp_host:

        smtp_host = "smtp.gmail.com"

    # ---------------------------------------------------------------
    # SMTP PORT
    # ---------------------------------------------------------------

    try:

        smtp_port = int(
            smtp_port or 465
        )

    except (TypeError, ValueError):

        smtp_port = 465

    # ---------------------------------------------------------------
    # PARAMÈTRES OBLIGATOIRES
    # ---------------------------------------------------------------

    sender = _safe_text(
        sender
    ).strip()

    password = _safe_text(
        password
    )

    recipient = _safe_text(
        recipient
    ).strip()

    subject = _safe_text(
        subject,
        "V4.2 Trading Scanner",
    ).strip()

    body = _safe_text(
        body
    )

    missing = []

    if not sender:
        missing.append(
            "EMAIL_SENDER"
        )

    if not password:
        missing.append(
            "EMAIL_PASSWORD"
        )

    if not recipient:
        missing.append(
            "EMAIL_RECIPIENT"
        )

    if missing:

        print(
            "[Email] "
            "Paramètres incomplets "
            "— paramètre(s) manquant(s) : "
            + ", ".join(missing)
        )

        return False

    # ---------------------------------------------------------------
    # CONSTRUCTION DU MESSAGE
    # ---------------------------------------------------------------

    try:

        msg = MIMEText(
            body,
            "plain",
            "utf-8",
        )

        msg["Subject"] = str(
            Header(
                subject,
                "utf-8",
            )
        )

        msg["From"] = sender

        msg["To"] = recipient

    except Exception as exc:

        print(
            "[Email] "
            f"Erreur construction message : {exc}"
        )

        return False

    # ---------------------------------------------------------------
    # CONTEXTE SSL
    # ---------------------------------------------------------------

    try:

        ssl_context = ssl.create_default_context()

    except Exception as exc:

        print(
            "[Email] "
            f"Impossible de créer le contexte SSL : {exc}"
        )

        return False

    # ---------------------------------------------------------------
    # CONNEXION SMTP
    # ---------------------------------------------------------------

    try:

        print(
            f"[Email] "
            f"Connexion SMTP à "
            f"{smtp_host}:{smtp_port}..."
        )

        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=SMTP_TIMEOUT,
            context=ssl_context,
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

        print(
            "[Email] Message envoyé."
        )

        return True

    # ---------------------------------------------------------------
    # AUTHENTIFICATION
    # ---------------------------------------------------------------

    except smtplib.SMTPAuthenticationError as exc:

        print(
            "[Email] "
            "Échec d'authentification SMTP."
        )

        print(
            "[Email] "
            "Avec Gmail, utilisez généralement "
            "un mot de passe d'application."
        )

        print(
            f"[Email] "
            f"Détail technique : {exc}"
        )

        return False

    # ---------------------------------------------------------------
    # TIMEOUT
    # ---------------------------------------------------------------

    except (
        TimeoutError,
        smtplib.SMTPServerDisconnected,
    ) as exc:

        print(
            "[Email] "
            f"Connexion SMTP interrompue/timeout : {exc}"
        )

        return False

    # ---------------------------------------------------------------
    # ERREUR SMTP
    # ---------------------------------------------------------------

    except smtplib.SMTPException as exc:

        print(
            "[Email] "
            f"Erreur SMTP : {exc}"
        )

        return False

    # ---------------------------------------------------------------
    # ERREUR GÉNÉRALE
    # ---------------------------------------------------------------

    except Exception as exc:

        print(
            "[Email] "
            f"Échec de l'envoi : {exc}"
        )

        return False


# ---------------------------------------------------------------------------
# NOTIFICATION COMBINÉE
# ---------------------------------------------------------------------------

def send_notifications(
    message,
    slack_webhook_url=None,
    smtp_host=None,
    smtp_port=None,
    sender=None,
    password=None,
    recipient=None,
    subject="V4.2 Trading Scanner",
):
    """
    Envoie simultanément Slack et Email.

    Important :
    l'échec de Slack n'empêche PAS l'email.
    L'échec de l'email n'empêche PAS Slack.

    Retourne un dictionnaire de diagnostic.
    """

    result = {
        "slack": False,
        "email": False,
    }

    # ---------------------------------------------------------------
    # SLACK
    # ---------------------------------------------------------------

    try:

        result["slack"] = send_slack(
            slack_webhook_url,
            message,
        )

    except Exception as exc:

        # Double sécurité : même une erreur inattendue dans
        # send_slack ne doit jamais arrêter le scanner.
        print(
            "[Slack] "
            f"Erreur protégée : {exc}"
        )

        result["slack"] = False

    # ---------------------------------------------------------------
    # EMAIL
    # ---------------------------------------------------------------

    try:

        result["email"] = send_email(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            sender=sender,
            password=password,
            recipient=recipient,
            subject=subject,
            body=message,
        )

    except Exception as exc:

        # Double sécurité.
        print(
            "[Email] "
            f"Erreur protégée : {exc}"
        )

        result["email"] = False

    # ---------------------------------------------------------------
    # RÉSUMÉ
    # ---------------------------------------------------------------

    if result["slack"] or result["email"]:

        print(
            "[Notifications] "
            f"Slack={'OK' if result['slack'] else 'ECHEC'} | "
            f"Email={'OK' if result['email'] else 'ECHEC'}"
        )

    else:

        print(
            "[Notifications] "
            "Aucune notification envoyée."
        )

    return result
```
