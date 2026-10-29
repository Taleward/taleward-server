"""Server 0.4.123: Mails an Konten mit Antwortadresse des Betreibers (Reply-To) und als automatisch gekennzeichnet."""


class _Postausgang:
    gesendet: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, benutzer, passwort):
        pass

    def send_message(self, nachricht):
        _Postausgang.gesendet.append(nachricht)


def _einrichten(dbs, monkeypatch, kontakt):
    import smtplib

    from app.einstellungen import meta_schreiben

    monkeypatch.setattr(smtplib, "SMTP_SSL", _Postausgang)
    _Postausgang.gesendet = []
    for k, v in {"melden.smtp_host": "smtp.example.org", "melden.smtp_port": "465", "melden.smtp_tls": "ssl",
                 "melden.smtp_user": "konto@example.org", "melden.email_von": "noreply@example.org",
                 "angabe.server_contact": kontakt}.items():
        meta_schreiben(dbs, k, v)
    dbs.commit()


def test_konto_mail_antwortet_an_den_betreiber(dbs, monkeypatch):
    from app import mail

    _einrichten(dbs, monkeypatch, "info@example.org")
    mail.senden(dbs, "spielerin@example.net", "Betreff", "Text")
    n = _Postausgang.gesendet[0]
    from email.utils import parseaddr

    assert parseaddr(n["From"])[1] == "noreply@example.org" and n["To"] == "spielerin@example.net"
    assert n["Reply-To"] == "info@example.org" and n["Auto-Submitted"] == "auto-generated"


def test_ohne_mail_kontakt_keine_antwortadresse(dbs, monkeypatch):
    from app import mail

    _einrichten(dbs, monkeypatch, "Verein Beispiel, Telefon 0123")
    mail.senden(dbs, "spielerin@example.net", "Betreff", "Text")
    assert _Postausgang.gesendet[0]["Reply-To"] is None
