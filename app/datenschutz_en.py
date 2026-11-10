"""English template for the privacy notice (0.4.163) – same structure and conditions as `datenschutz.vorlage`.

A TEMPLATE, not legal advice. The operator reads it, adapts it and only then publishes it (at /datenschutz, shown when
the visitor chose English). Parts in “[…]” must be filled in by the operator. Kept as a separate function so the German
template (and with it `datenschutz.vorlage_stand`) stays byte-identical.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

_WIR = {"privat": ("our games", "on a server we run ourselves"),
        "firma": ("our organisation's games", "on a server of our organisation")}
_WIR_STANDARD = ("our club's games", "on a server of our club")


def _tage(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def vorlage(db: Session, hosting: str = "") -> str:
    from app.config import get_settings
    from app.einrichtung import betriebsart
    from app.einstellungen import angaben, extern_konfig, llm_konfig

    from app import betreiber

    a = angaben(db)
    b = betreiber.lesen(db)
    wir, bei_uns = _WIR.get(b.art, _WIR_STANDARD)
    art = betriebsart(db)
    ext = extern_konfig(db)
    k = llm_konfig(db)
    kontakt = a.server_contact or "[E-mail address for privacy questions]"
    mistral_transkription = ext.anbieter == "mistral" or art in ("cloud", "beides")
    mistral_llm = k.art == "api" and k.ist_mistral
    anderer_llm = k.art == "api" and not k.ist_mistral
    tage = get_settings().audio_retention_days
    from app.aufbewahrung import lesen as aufbewahrung

    frist = aufbewahrung(db)
    from app import anmeldedienste, mail

    email = mail.kann_senden(db)
    from app.slfreigabe import gm_approval

    freigabe = gm_approval(db) == "required"
    from app import abrechnung

    guthaben = abrechnung.an(db)
    from app import konto_mail

    konto_hinweise = konto_mail.an(db)
    dienste = anmeldedienste.eingerichtet(db)
    dienst_namen = [anmeldedienste.DIENSTE[d]["name"] for d in dienste]

    t = [f"# Privacy notice for {a.server_name}",
         f"As of: [Date]. This notice applies to the Taleward app in connection with the server “{a.server_name}”, "
         f"operated by {a.server_operator}.",
         "## Controller",
         f"{a.server_operator}\n{b.anschrift or '[Postal address]'}\n{kontakt}",
         "## What the app does",
         "Taleward records tabletop role-playing games (only with the consent of everyone present), turns the "
         "recording into text and writes a summary (“chapter”) and suggestions for the campaign bible from it. The "
         "game master reviews everything before the group sees it.",
         "## Which data we process",
         "- Account: user name, display name, password (stored only as a check value), time of your consent to this "
         "notice and of the age confirmation, where applicable status and time of a request for GM approval, the "
         "app language at your last sign-in (for emails in your language).",
         *(["- Optional: an email address – only so you can reset your password yourself"
            + (" and receive the answer to a request for GM approval" if freigabe else "")
            + (" and get notices when someone signs in to or changes your account" if konto_hinweise else "")
            + ("; as game master also notices when your credit runs low or something is waiting for it (can be "
               "turned off on the credit page)" if guthaben else "")
            + ". You can remove it in the app at any time."] if email else []),
         *([f"- Optional, if you sign in with {', '.join(dienst_namen)}: your identifier with that service, your "
            "name and – if confirmed by the service – your email address. We do not store the service's login "
            "details."] if dienst_namen else []),
         "- Campaign memberships: role, character name, character description and background, character picture, "
         "comments, answers in scheduling polls.",
         "- Recordings of games and the transcripts, summaries and entries made from them.",
         "- Optional: a voice profile (voiceprint) the app uses to recognise voices in recordings. This is a "
         "biometric characteristic; we only process it with explicit consent (Art. 9(2)(a) GDPR), which can be "
         "withdrawn in the app at any time.",
         "- Technically necessary data when the server is accessed (IP address, time), only for operation and to "
         "prevent abuse. The server keeps no access log; to limit sign-in attempts it keeps the IP address in memory "
         "for at most one hour.",
         "## Purpose and legal basis",
         f"We process the data to document {wir} and to provide the summaries to the members (Art. 6(1)(b) GDPR – "
         "use of the service; for recordings and voice profiles Art. 6(1)(a) and Art. 9(2)(a) GDPR – consent). A "
         "recording is only made if everyone present has consented; consent can be withdrawn in the app at any time.",
         "## How long we keep data",
         (f"- Recordings: are turned into text and stay on the server for checking the summary until the game master "
          f"releases the chapter, at most {_tage(frist.tage)}, and are then deleted." if frist.bis_freigabe else
          f"- Recordings: are deleted as soon as they have been turned into text, at the latest after {tage} days.")
         + " Short voice samples per speaker are only kept until the game master has assigned the voices, at most "
         + (f"{_tage(frist.tage)}." if frist.bis_freigabe else f"{tage} days."),
         "- Voice profiles: until withdrawn; deleting also removes everything learned from them.",
         "- Account and content: until the account is deleted (in the app or at /konto-loeschen on this server). "
         "Contributions to shared campaigns (comments, summaries) then remain without a name as “Deleted account”.",
         "- Backups: up to [14] days.",
         "## Who else receives the data"]
    empfaenger = [f"- Running the server: {hosting or '[Hosting provider, registered office]'}, data centre in "
                  "[country in the EU] (processing on our behalf under Art. 28 GDPR). [Take from the contract: "
                  "whether the provider uses sub-processors outside the EU and on what basis, e.g. standard "
                  "contractual clauses.]"]
    if art in ("lokal", "beides"):
        empfaenger.append(f"- Turning recordings into text: {bei_uns} [location], without passing data to third "
                          "parties.")
    if mistral_transkription or mistral_llm:
        zwecke = " and ".join(z for z in ("turning recordings into text" if mistral_transkription else "",
                                          "writing summaries" if mistral_llm else "") if z)
        empfaenger.append(f"- For {zwecke}: Mistral AI SAS, Paris (France, EU), as processor under Art. 28 GDPR. "
                          "[Take from the contract: whether and for how long the provider stores the data and whether "
                          "it uses it for training.]")
    if anderer_llm:
        empfaenger.append("- For writing summaries: [provider, registered office, basis for transfers outside the "
                          "EU].")
    if k.art == "lokal":
        empfaenger.append(f"- Summaries are written {bei_uns}, without passing data to third parties.")
    anbieter = {"google": "Google Ireland Limited, Ireland", "microsoft": "Microsoft Ireland Operations Limited, Ireland",
                "apple": "Apple Distribution International Ltd., Ireland",
                "discord": "[Discord – provider and registered office according to Discord's privacy policy]"}
    for d in dienste:
        empfaenger.append(f"- Sign-in with {anmeldedienste.DIENSTE[d]['name']} (optional): if you choose it, you sign "
                          f"in with {anbieter[d]}; the service learns that you are signing in with us. The service "
                          "itself is responsible for this sign-in; its privacy policy applies. [Check transfers "
                          "outside the EU according to the service.]")
    if email:
        empfaenger.append("- We send emails about your account via [email provider].")
    empfaenger.append("- Within a campaign, members see what the game master releases. Transcripts, suggestions and "
                      "secret notes are only seen by the game master.")
    t.append("\n".join(empfaenger))
    from app import rechtstexte

    t += rechtstexte.datenschutz_abschnitt(db, "en")
    t += ["## Your rights",
          "You have the right of access, rectification, erasure, restriction of processing, data portability and "
          "objection, and the right to withdraw consent you have given. In the app you can export your data yourself "
          "and delete your account (also without the app at /konto-loeschen). For everything else contact "
          f"{kontakt}. You can also lodge a complaint with a data protection supervisory authority, e.g. [competent "
          "state authority].",
          "## Minimum age",
          f"The app is intended for people aged {a.min_age} and over. Younger people need their parents' consent."]
    return "\n\n".join(t)
