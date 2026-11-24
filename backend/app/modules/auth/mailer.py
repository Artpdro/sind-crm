import smtplib
from email.message import EmailMessage

from app.config import get_settings

OUTBOX: list[dict] = []  # usado apenas em testes (APP_ENV=test)


def send_reset_email(to: str, link: str) -> None:
    s = get_settings()
    if s.app_env == "test":
        OUTBOX.append({"to": to, "link": link})
        return
    if not s.smtp_host:
        # Sem SMTP configurado: NÃO registra o link (é um segredo). O admin usa scripts/admin.py.
        return
    msg = EmailMessage()
    msg["Subject"] = "Redefinição de senha"
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg.set_content(f"Para redefinir sua senha, acesse (válido por {s.reset_token_minutes} min):\n{link}\n\n"
                    "Se você não solicitou, ignore esta mensagem.")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
