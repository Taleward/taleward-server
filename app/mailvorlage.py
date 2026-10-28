"""Mails an Kontoinhaber und Verwaltung im Hausstil (0.4.135).

Eine Vorlage für alle Mails des Servers: Pergament, das Taleward-Zeichen, das Lesebändchen, ein Knopf, wo ein Link
hingehört. Kein Tracking, keine nachgeladenen Bilder: Das Zeichen wird als kleines PNG (5 KB) in die Mail eingebettet
(Content-ID), alles andere ist Text und Farbe. Die Textfassung bleibt als Alternative erhalten – Mailprogramme ohne
HTML, Vorleser und die Vorschauzeile nutzen sie. Beide Fassungen entstehen aus denselben Inhalten (INHALTE), damit
sie nie auseinanderlaufen.
"""
from __future__ import annotations

from html import escape

# Farben aus taleward-tokens.css (hell: Pergament). Mails können keine CSS-Variablen, darum stehen die Werte hier
# einmal fest; bei Änderungen der Tokens hier nachziehen.
HELL = {
    "paper": "#f2e8d5", "raised": "#fbf6ea", "line": "#d9c9a8", "line_strong": "#8a7654",
    "ink": "#2a2118", "muted": "#5e4b36", "tinte": "#17313b", "on_tinte": "#f3f3ee", "siegel": "#9e2a3a",
}
DUNKEL = {
    "paper": "#121d22", "raised": "#1a2930", "line": "#2e424b", "line_strong": "#6f838b",
    "ink": "#e9ece6", "muted": "#a2aeb2", "tinte": "#2c5361", "on_tinte": "#f3f3ee", "siegel": "#d4596c",
}
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "'Alegreya Sans', 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
ZEICHEN_CID = "taleward-zeichen"  # Content-ID des eingebetteten Zeichens (zeichen-mail.png, 96 × 96)

# 0.4.135: Satz unter jeder Hinweis-Mail zum Guthaben (abbestellen geht auf der Guthaben-Seite)
HINWEIS_ABBESTELLEN = {
    "de": "Diese Hinweise kannst du auf der Guthaben-Seite unter „Hinweise per E-Mail“ abbestellen.",
    "en": "You can turn these notices off on the credit page under “Notices by e-mail”.",
}

# Inhalte je Mail-Art und Sprache. Platzhalter: {name}, {benutzer}, {server}, {betreiber}, {link}, {datum};
# Hinweis-Mails zum Guthaben zusätzlich {guthaben}, {guthaben_gross} und {wo}.
# anrede: "name" → „Hallo {name},“; "ohne" → „Hallo,“. knopf: Beschriftung des Knopfs (Ziel ist {link}) oder None.
INHALTE = {
    "bestaetigen": {
        "de": dict(betreff="Bitte bestätige deine E-Mail-Adresse für Taleward ({server})",
                   ueberschrift="Bitte bestätige deine E-Mail-Adresse.", anrede="name",
                   absaetze=["Du hast diese Adresse für dein Konto „{benutzer}“ auf {server} eingetragen.",
                             "Wir nutzen die Adresse nur für dein Konto, zum Beispiel damit du dein Passwort selbst zurücksetzen "
                             "kannst."],
                   knopf="Adresse bestätigen",
                   hinweis="Der Link gilt 24 Stunden. Warst du das nicht, ignoriere diese Mail einfach."),
        "en": dict(betreff="Please confirm your e-mail address for Taleward ({server})",
                   ueberschrift="Please confirm your e-mail address.", anrede="name",
                   absaetze=["You entered this address for your account “{benutzer}” on {server}.",
                             "We only use the address for your account, for example so that you can reset your password "
                             "yourself."],
                   knopf="Confirm address",
                   hinweis="The link is valid for 24 hours. If this wasn't you, simply ignore this e-mail."),
    },
    "passwort": {
        "de": dict(betreff="Neues Passwort für Taleward ({server})",
                   ueberschrift="Ein neues Passwort wählen.", anrede="name",
                   absaetze=["Jemand – hoffentlich du – möchte das Passwort für dein Konto „{benutzer}“ auf {server} "
                             "zurücksetzen."],
                   knopf="Neues Passwort wählen",
                   hinweis="Der Link gilt eine Stunde und nur einmal. Warst du das nicht, ignoriere diese Mail; dein "
                           "bisheriges Passwort bleibt gültig."),
        "en": dict(betreff="New password for Taleward ({server})",
                   ueberschrift="Choose a new password.", anrede="name",
                   absaetze=["Someone – hopefully you – wants to reset the password for your account “{benutzer}” on "
                             "{server}."],
                   knopf="Choose a new password",
                   hinweis="The link is valid for one hour and only once. If this wasn't you, ignore this e-mail; "
                           "your current password stays valid."),
    },
    "sl_anfrage": {
        "de": dict(betreff="SL-Freigabe angefragt: {name} ({server})",
                   ueberschrift="{name} möchte eigene Kampagnen leiten.", anrede="ohne",
                   absaetze=["{name} (Benutzername „{benutzer}“) bittet auf {server} um die SL-Freigabe, angefragt "
                             "am {datum}.",
                             "Entscheiden kannst du nach der Anmeldung in der Verwaltung."],
                   knopf="Anfrage prüfen",
                   hinweis="Der Link selbst gibt nichts frei."),
        "en": dict(betreff="Game master approval requested: {name} ({server})",
                   ueberschrift="{name} would like to run their own campaigns.", anrede="ohne",
                   absaetze=["{name} (username “{benutzer}”) asks for game master approval on {server}, requested "
                             "on {datum}.",
                             "You can decide after signing in to the administration."],
                   knopf="Review request",
                   hinweis="The link itself does not approve anything."),
    },
    "sl_freigegeben": {
        "de": dict(betreff="Du kannst jetzt eigene Kampagnen leiten ({server})",
                   ueberschrift="Du kannst jetzt eigene Kampagnen leiten.", anrede="name",
                   absaetze=["Die Verwaltung von {server} hat deine SL-Freigabe erteilt. In der App kannst du jetzt "
                             "eigene Kampagnen anlegen."],
                   knopf=None, hinweis=None),
        "en": dict(betreff="You can now run your own campaigns ({server})",
                   ueberschrift="You can now run your own campaigns.", anrede="name",
                   absaetze=["The administrators of {server} have approved you as a game master. You can now create "
                             "your own campaigns in the app."],
                   knopf=None, hinweis=None),
    },
    "sl_abgelehnt": {
        "de": dict(betreff="Deine Anfrage zur SL-Freigabe ({server})",
                   ueberschrift="Deine Anfrage zur SL-Freigabe.", anrede="name",
                   absaetze=["Die Verwaltung von {server} hat deine Anfrage zur SL-Freigabe abgelehnt.",
                             "In Kampagnen, zu denen du eingeladen wirst, spielst du weiter wie bisher. Fragen "
                             "beantworten die Betreiber."],
                   knopf=None, hinweis=None),
        "en": dict(betreff="Your request for game master approval ({server})",
                   ueberschrift="Your request for game master approval.", anrede="name",
                   absaetze=["The administrators of {server} have declined your request for game master approval.",
                             "You keep playing in campaigns you are invited to. The operators can answer any "
                             "questions."],
                   knopf=None, hinweis=None),
    },
    "sl_entzogen": {
        "de": dict(betreff="Deine SL-Freigabe wurde zurückgenommen ({server})",
                   ueberschrift="Deine SL-Freigabe wurde zurückgenommen.", anrede="name",
                   absaetze=["Die Verwaltung von {server} hat deine SL-Freigabe zurückgenommen. Neue Kampagnen kannst "
                             "du nicht mehr anlegen; deine bestehenden Kampagnen bleiben."],
                   knopf=None, hinweis=None),
        "en": dict(betreff="Your game master approval was withdrawn ({server})",
                   ueberschrift="Your game master approval was withdrawn.", anrede="name",
                   absaetze=["The administrators of {server} have withdrawn your game master approval. You can no "
                             "longer create new campaigns; your existing campaigns stay."],
                   knopf=None, hinweis=None),
    },
    # 0.4.135: wie sl_freigegeben, dazu der Weg zum Aufladen, wenn die Abrechnung mit eigenem Guthaben an ist
    "sl_freigegeben_guthaben": {
        "de": dict(betreff="Du kannst jetzt eigene Kampagnen leiten ({server})",
                   ueberschrift="Du kannst jetzt eigene Kampagnen leiten.", anrede="name",
                   absaetze=["Die Verwaltung von {server} hat deine SL-Freigabe erteilt. In der App kannst du jetzt "
                             "eigene Kampagnen anlegen.",
                             "Abschriften und Kapitel bezahlst du auf diesem Server aus deinem Guthaben. Aufladen, "
                             "Gutscheine einlösen und Buchungen ansehen kannst du auf der Guthaben-Seite des Servers, "
                             "angemeldet mit Benutzername und Passwort."],
                   knopf="Zur Guthaben-Seite", hinweis=None),
        "en": dict(betreff="You can now run your own campaigns ({server})",
                   ueberschrift="You can now run your own campaigns.", anrede="name",
                   absaetze=["The administrators of {server} have approved you as a game master. You can now create "
                             "your own campaigns in the app.",
                             "On this server, transcripts and chapters are paid from your credit. You can top up, "
                             "redeem vouchers and see your bookings on the server's credit page, signed in with your "
                             "username and password."],
                   knopf="Go to the credit page", hinweis=None),
    },
    # 0.4.135: Hinweis-Mails zum Guthaben (app/guthaben_mail.py). Ohne Beträge. {guthaben}: „dein Guthaben“ bzw. „das
    # Guthaben der Organisation …“; {wo}: „ in der Kampagne …“ nur für die heutige SL der Kampagne, sonst leer.
    "guthaben_wartet": {
        "de": dict(betreff="Eine Verarbeitung wartet auf Guthaben ({server})",
                   ueberschrift="Eine Verarbeitung wartet auf Guthaben.", anrede="name",
                   absaetze=["Auf {server} wartet etwas{wo} darauf, verarbeitet zu werden. {guthaben_gross} reicht "
                             "dafür gerade nicht.",
                             "Nach dem Aufladen geht es von selbst weiter."],
                   knopf="Guthaben aufladen", hinweis=HINWEIS_ABBESTELLEN["de"]),
        "en": dict(betreff="Something is waiting for credit ({server})",
                   ueberschrift="Something is waiting for credit.", anrede="name",
                   absaetze=["On {server}, something{wo} is waiting to be processed. {guthaben_gross} is not enough "
                             "for it right now.",
                             "Once topped up, it continues on its own."],
                   knopf="Top up credit", hinweis=HINWEIS_ABBESTELLEN["en"]),
    },
    "guthaben_freigabe": {
        "de": dict(betreff="Eine Verarbeitung wartet auf „Verarbeiten“ ({server})",
                   ueberschrift="Eine Verarbeitung wartet auf deine Bestätigung.", anrede="name",
                   absaetze=["Auf {server} wartet etwas{wo}, das voraussichtlich mehr kostet als der Betrag, den du "
                             "ohne Rückfrage freigibst.",
                             "Auf der Guthaben-Seite siehst du die Schätzung. Mit „Verarbeiten“ startet es."],
                   knopf="Guthaben-Seite öffnen", hinweis=HINWEIS_ABBESTELLEN["de"]),
        "en": dict(betreff="Something is waiting for “Process” ({server})",
                   ueberschrift="Something is waiting for your confirmation.", anrede="name",
                   absaetze=["On {server}, something{wo} is waiting that will probably cost more than the amount you "
                             "approve without asking.",
                             "The credit page shows the estimate. “Process” starts it."],
                   knopf="Open the credit page", hinweis=HINWEIS_ABBESTELLEN["en"]),
    },
    "guthaben_knapp": {
        "de": dict(betreff="Das Guthaben wird knapp ({server})",
                   ueberschrift="Das Guthaben wird knapp.", anrede="name",
                   absaetze=["Auf {server} ist {guthaben} fast aufgebraucht.",
                             "Wenn du rechtzeitig auflädst, müssen die nächsten Runden nicht warten."],
                   knopf="Guthaben aufladen", hinweis=HINWEIS_ABBESTELLEN["de"]),
        "en": dict(betreff="Credit is running low ({server})",
                   ueberschrift="Credit is running low.", anrede="name",
                   absaetze=["On {server}, {guthaben} is almost used up.",
                             "If you top up in time, the next sessions won't have to wait."],
                   knopf="Top up credit", hinweis=HINWEIS_ABBESTELLEN["en"]),
    },
    "guthaben_minus": {
        "de": dict(betreff="Das Guthaben ist im Minus ({server})",
                   ueberschrift="Das Guthaben ist im Minus.", anrede="name",
                   absaetze=["Auf {server} ist {guthaben} unter null gerutscht.",
                             "Neue Verarbeitungen warten, bis wieder aufgeladen ist."],
                   knopf="Guthaben aufladen", hinweis=HINWEIS_ABBESTELLEN["de"]),
        "en": dict(betreff="Credit is below zero ({server})",
                   ueberschrift="Credit is below zero.", anrede="name",
                   absaetze=["On {server}, {guthaben} has dropped below zero.",
                             "New processing waits until it has been topped up."],
                   knopf="Top up credit", hinweis=HINWEIS_ABBESTELLEN["en"]),
    },
}

# 0.4.136: Hinweise bei Änderungen am Konto (app/konto_mail.py). Kein Knopf und kein Link (nichts, was etwas auslöst
# oder anmeldet); {wann} mit Uhrzeit, {wie} je Anlass (Anmeldeart und Ort, Dienst bzw. Weg der Änderung).
KONTO_WARNUNG = {
    "de": "Warst du das nicht? Ändere in der App unter Konto dein Passwort. Das meldet dich überall ab. Sag außerdem "
          "der Verwaltung von {server} Bescheid.",
    "en": "Wasn't you? Change your password in the app under Account. That signs you out everywhere. Also let the "
          "administrators of {server} know.",
}
KONTO_HINWEIS = {
    "de": "Diese Mail ist nur ein Hinweis und enthält keinen Link. Du bekommst sie, weil an deinem Konto diese "
          "E-Mail-Adresse hinterlegt ist.",
    "en": "This e-mail is only a notice and contains no link. You get it because this e-mail address is set for "
          "your account.",
}
KONTO_HINWEIS_ALT = {
    "de": "Diese Mail ist nur ein Hinweis und enthält keinen Link. Du bekommst sie, weil diese Adresse bisher an "
          "deinem Konto hinterlegt war.",
    "en": "This e-mail is only a notice and contains no link. You get it because this address was set for your "
          "account until now.",
}
KONTO_ARTEN = {
    # art: (Betreff de, Überschrift de, Absatz de, Betreff en, Überschrift en, Absatz en)
    "konto_anmeldung": (
        "Neue Anmeldung bei deinem Konto ({server})", "Neue Anmeldung bei deinem Konto.",
        "Dein Konto „{benutzer}“ auf {server} wurde gerade angemeldet: {wann}, {wie}.",
        "New sign-in to your account ({server})", "New sign-in to your account.",
        "Your account “{benutzer}” on {server} was just signed in: {wann}, {wie}."),
    "konto_passwort": (
        "Dein Passwort wurde geändert ({server})", "Dein Passwort wurde geändert.",
        "Das Passwort für dein Konto „{benutzer}“ auf {server} wurde neu gesetzt: {wann}, {wie}.",
        "Your password was changed ({server})", "Your password was changed.",
        "The password for your account “{benutzer}” on {server} was set anew: {wann}, {wie}."),
    "konto_dienst_neu": (
        "Neuer Anmeldedienst an deinem Konto ({server})", "Ein Anmeldedienst wurde verknüpft.",
        "Dein Konto „{benutzer}“ auf {server} lässt sich jetzt auch über {wie} anmelden. Verknüpft: {wann}.",
        "New sign-in service on your account ({server})", "A sign-in service was linked.",
        "Your account “{benutzer}” on {server} can now also be signed in via {wie}. Linked: {wann}."),
    "konto_dienst_weg": (
        "Anmeldedienst von deinem Konto getrennt ({server})", "Ein Anmeldedienst wurde getrennt.",
        "Dein Konto „{benutzer}“ auf {server} lässt sich nicht mehr über {wie} anmelden. Getrennt: {wann}.",
        "Sign-in service removed from your account ({server})", "A sign-in service was removed.",
        "Your account “{benutzer}” on {server} can no longer be signed in via {wie}. Removed: {wann}."),
    "konto_email": (
        "Die E-Mail-Adresse deines Kontos wurde geändert ({server})", "Die E-Mail-Adresse wurde geändert.",
        "Für dein Konto „{benutzer}“ auf {server} ist seit {wann} eine andere oder keine E-Mail-Adresse mehr "
        "eingetragen. An diese Adresse kommen keine Mails des Servers mehr.",
        "The e-mail address of your account was changed ({server})", "The e-mail address was changed.",
        "Since {wann}, your account “{benutzer}” on {server} has a different e-mail address or none at all. This "
        "address gets no more e-mails from the server."),
    "konto_faktor_an": (
        "Zweiter Faktor eingeschaltet ({server})", "Der zweite Faktor ist eingeschaltet.",
        "Für dein Verwaltungskonto „{benutzer}“ auf {server} ist seit {wann} der zweite Faktor an. Die "
        "Wiederherstellungscodes sind neu.",
        "Second factor turned on ({server})", "The second factor is turned on.",
        "Since {wann}, the second factor is on for your administration account “{benutzer}” on {server}. The "
        "recovery codes are new."),
    "konto_faktor_aus": (
        "Zweiter Faktor ausgeschaltet ({server})", "Der zweite Faktor ist ausgeschaltet.",
        "Für dein Verwaltungskonto „{benutzer}“ auf {server} ist seit {wann} kein zweiter Faktor mehr an.",
        "Second factor turned off ({server})", "The second factor is turned off.",
        "Since {wann}, there is no second factor on your administration account “{benutzer}” on {server}."),
}
for _art, (_bd, _ud, _ad, _be, _ue, _ae) in KONTO_ARTEN.items():
    _alt = KONTO_HINWEIS_ALT if _art == "konto_email" else KONTO_HINWEIS
    INHALTE[_art] = {
        "de": dict(betreff=_bd, ueberschrift=_ud, anrede="name", absaetze=[_ad, KONTO_WARNUNG["de"]], knopf=None,
                   hinweis=_alt["de"]),
        "en": dict(betreff=_be, ueberschrift=_ue, anrede="name", absaetze=[_ae, KONTO_WARNUNG["en"]], knopf=None,
                   hinweis=_alt["en"]),
    }

FUSS = {
    "de": dict(anrede_name="Hallo {name},", anrede_ohne="Hallo,", betrieben="{server} · betrieben von {betreiber}",
               automatisch="Diese Mail wurde automatisch verschickt.", antwort="Antworten erreichen {kontakt}.",
               link_hinweis="Falls der Knopf nicht geht, öffne diesen Link:"),
    "en": dict(anrede_name="Hello {name},", anrede_ohne="Hello,", betrieben="{server} · run by {betreiber}",
               automatisch="This e-mail was sent automatically.", antwort="Replies reach {kontakt}.",
               link_hinweis="If the button does not work, open this link:"),
}


def _sprache(sprache: str) -> str:
    return "en" if sprache == "en" else "de"


def betreff(art: str, sprache: str, **werte) -> str:
    return INHALTE[art][_sprache(sprache)]["betreff"].format(**werte)


def text(art: str, sprache: str, kontakt: str | None = None, **werte) -> str:
    """Reine Textfassung – gleicher Inhalt wie die HTML-Fassung."""
    s = _sprache(sprache)
    i, f = INHALTE[art][s], FUSS[s]
    teile = [(f["anrede_name"] if i["anrede"] == "name" else f["anrede_ohne"]).format(**werte), ""]
    for n, a in enumerate(i["absaetze"]):
        teile += ([""] if n else []) + [a.format(**werte)]  # Leerzeile zwischen Absätzen wie in den früheren Mails
    if i["knopf"]:
        teile += ["", f"{i['knopf'].format(**werte)}:", werte["link"]]
    if i["hinweis"]:
        teile += ["", i["hinweis"].format(**werte)]
    teile += ["", "-- ", f["betrieben"].format(**werte),
              f["automatisch"] + (" " + f["antwort"].format(kontakt=kontakt) if kontakt else "")]
    return "\n".join(teile)


def html(art: str, sprache: str, kontakt: str | None = None, **werte) -> str:
    """HTML-Fassung: Tabellen und Inline-Stile, damit es in Gmail, Outlook und Apple Mail gleich aussieht."""
    s = _sprache(sprache)
    i, f, c = INHALTE[art][s], FUSS[s], HELL
    e = {k: escape(str(v)) for k, v in werte.items()}
    anrede = (f["anrede_name"] if i["anrede"] == "name" else f["anrede_ohne"]).format(**e)
    absaetze = "".join(
        f'<p class="tw-ink" style="margin:0 0 14px;font-family:{SANS};font-size:16px;line-height:24px;color:{c["ink"]}">'
        f'{a.format(**e)}</p>' for a in i["absaetze"])
    knopf = ""
    if i["knopf"]:
        link = e["link"]
        # Der Knopf ist so breit wie seine Beschriftung (Tabelle ohne width). Beschriftungen kurz halten (bis etwa
        # 22 Zeichen), damit sie auch bei 390 px in eine Zeile passen; bricht sie doch um, bleibt sie zentriert.
        knopf = f'''
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0 10px">
            <tr><td class="tw-knopf" align="center" bgcolor="{c["tinte"]}" style="border-radius:10px;background:{c["tinte"]};text-align:center">
              <a href="{link}" style="display:inline-block;padding:14px 24px;font-family:{SANS};font-size:16px;
                 line-height:20px;font-weight:600;color:{c["on_tinte"]};text-decoration:none;text-align:center;
                 border-radius:10px">{i["knopf"].format(**e)}</a></td></tr>
          </table>
          <p class="tw-muted" style="margin:0 0 14px;font-family:{SANS};font-size:13px;line-height:19px;color:{c["muted"]}">
            {f["link_hinweis"]}<br><a class="tw-muted" href="{link}" style="color:{c["muted"]};word-break:break-all">{link}</a></p>'''
    hinweis = ""
    if i["hinweis"]:
        hinweis = (f'<p class="tw-muted" style="margin:6px 0 0;font-family:{SANS};font-size:14px;line-height:21px;'
                   f'color:{c["muted"]}">{i["hinweis"].format(**e)}</p>')
    # kontakt: Adresse des Betreibers (Reply-To), sonst steht nichts über Antworten – nichts versprechen, was nicht stimmt
    antwort = " " + f["antwort"].format(kontakt=escape(kontakt)) if kontakt else ""
    titel = escape(i["betreff"].format(**werte))
    vorschau = i["absaetze"][0].format(**e)  # Vorschauzeile im Posteingang: der erste Absatz, nicht noch mal der Betreff
    d = DUNKEL
    return f'''<!doctype html>
<html lang="{s}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{titel}</title>
<style>
  @media (prefers-color-scheme: dark) {{
    .tw-grund {{ background-color: {d["paper"]} !important; }}
    .tw-karte {{ background-color: {d["raised"]} !important; border-color: {d["line"]} !important; }}
    .tw-ink, .tw-ink a {{ color: {d["ink"]} !important; }}
    .tw-muted, .tw-muted a {{ color: {d["muted"]} !important; }}
    .tw-linie {{ border-color: {d["line"]} !important; }}
    .tw-knopf {{ background-color: {d["tinte"]} !important; }}
    .tw-band, .tw-kerbe {{ background-color: {d["siegel"]} !important; border-left-color: {d["siegel"]} !important;
                           border-right-color: {d["siegel"]} !important; }}
  }}
</style>
</head>
<body class="tw-grund" style="margin:0;padding:0;background:{c["paper"]};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;color:{c["paper"]}">{vorschau}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="tw-grund" style="background:{c["paper"]}">
  <tr><td align="center" style="padding:28px 16px 40px">
    <!--[if mso]><table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="tw-karte"
           style="max-width:560px;background:{c["raised"]};border:1px solid {c["line"]};border-radius:10px">
      <tr>
        <td style="padding:0 28px">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="padding:22px 0 0" valign="middle">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                  <tr>
                    <td valign="middle" style="padding-right:12px"><img src="cid:{ZEICHEN_CID}" width="48" height="48" alt="" style="display:block;width:48px;height:48px;border:0"></td>
                    <td valign="middle" class="tw-ink" style="font-family:{SERIF};font-size:22px;line-height:26px;font-weight:bold;color:{c["tinte"]};letter-spacing:-0.01em">Taleward</td>
                  </tr>
                </table>
              </td>
              <td align="right" valign="top" width="14" style="padding:0">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                  <tr><td class="tw-band" width="14" height="46" style="background:{c["siegel"]};font-size:0;line-height:0">&nbsp;</td></tr>
                  <tr><td class="tw-kerbe" width="0" height="0" style="border-left:7px solid {c["siegel"]};border-right:7px solid {c["siegel"]};border-bottom:7px solid transparent;font-size:0;line-height:0">&nbsp;</td></tr>
                </table>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <tr>
        <td style="padding:26px 28px 8px">
          <h1 class="tw-ink" style="margin:0 0 18px;font-family:{SERIF};font-size:26px;line-height:32px;font-weight:bold;color:{c["ink"]};letter-spacing:-0.01em">{escape(i["ueberschrift"].format(**werte))}</h1>
          <p class="tw-ink" style="margin:0 0 14px;font-family:{SANS};font-size:16px;line-height:24px;color:{c["ink"]}">{anrede}</p>
          {absaetze}
          {knopf}{hinweis}
        </td>
      </tr>
      <tr>
        <td style="padding:18px 28px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="tw-linie" style="border-top:1px solid {c["line"]};font-size:0;line-height:0">&nbsp;</td></tr></table></td>
      </tr>
      <tr>
        <td class="tw-muted" style="padding:14px 28px 24px;font-family:{SANS};font-size:13px;line-height:19px;color:{c["muted"]}">
          {f["betrieben"].format(**e)}<br>{f["automatisch"]}{antwort}
        </td>
      </tr>
    </table>
    <!--[if mso]></td></tr></table><![endif]-->
  </td></tr>
</table>
</body>
</html>'''
