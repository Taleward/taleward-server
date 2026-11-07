"""Verwaltung → Anmeldung: Anmeldedienste (Google, Discord, Apple, Microsoft) und E-Mail für „Passwort vergessen“.

Jeder Server meldet sich selbst bei den Diensten an. Schlüssel sind nur schreibbar (Anzeige: „hinterlegt“).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import anmeldedienste, mail
from app.db import get_db
from app.einstellungen import meta_schreiben, oeffentliche_adresse
from app.models import User
from app.verwaltung.router import _seite, _sitzungs_bindung, _zurueck, csrf_pruefen, faktor_frisch, tr, verwalter

router = APIRouter(prefix="/verwaltung", include_in_schema=False)

# 0.4.146: Schritte nach Benjamins Durchgang (28.10.) – je Schritt genau das Feld bzw. die Spalte, die gemeint ist, und
# was freiwillig ist. Stand der Beschriftungen: November 2026.
ANLEITUNG = {
    "google": ("https://console.cloud.google.com/auth/clients", [
        "In der Google Cloud Console oben ein Projekt wählen oder anlegen (z. B. „Taleward Verein“).",
        "Links „Google Auth Platform“ → „Branding“: App-Name und Support-E-Mail eintragen; Startseite, Datenschutz und Nutzungsbedingungen mit den Adressen unten (freiwillig).",
        "„Zielgruppe“: Nutzertyp „Extern“. Danach „App veröffentlichen“ drücken – solange dort „Testen“ steht, können sich nur eingetragene Testnutzer anmelden. Eine Prüfung durch Google ist für Name und E-Mail nicht nötig.",
        "„Clients“ → „Client erstellen“ → Anwendungstyp „Webanwendung“ → bei „Autorisierte Weiterleitungs-URIs“ die Rückleitungsadresse unten eintragen → „Erstellen“.",
        "Im Fenster danach „Client-ID“ und „Clientschlüssel“ kopieren und hier eintragen. Den Clientschlüssel zeigt Google nur jetzt; verpasst → beim Client einen neuen Schlüssel hinzufügen und den alten löschen.",
    ]),
    "discord": ("https://discord.com/developers/applications", [
        "Im Discord Developer Portal „New Application“ → Name (z. B. „Taleward Verein“) → anlegen.",
        "Links „OAuth2“ → bei „Redirects“ auf „Add Redirect“ → Rückleitungsadresse unten einfügen → unten in der Leiste „Save Changes“ (sonst ist die Adresse nicht gespeichert).",
        "Auf derselben Seite „Client ID“ kopieren und hier eintragen.",
        "„Client Secret“ → „Reset Secret“ → bestätigen → den angezeigten Schlüssel kopieren und hier eintragen. Er wird nur einmal angezeigt.",
        "Bot, Intents und Bereiche (Scopes) braucht es dafür nicht – die wählt der Server selbst.",
    ]),
    "microsoft": ("https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade", [
        "Mit einem privaten Microsoft-Konto (Outlook, Hotmail) braucht es ein eigenes Verzeichnis. Ob es eins gibt, zeigt das Azure-Portal oben rechts: Profilbild → „Verzeichnis wechseln“ (meist „Standardverzeichnis“). Gibt es keins, ein kostenloses Azure-Konto anlegen (die Kreditkarte dient nur zur Prüfung). Der Link unten führt direkt zu den App-Registrierungen.",
        "„Neue Registrierung“ → Name (z. B. „Taleward Verein“). Das Feld „Unterstützte Kontotypen“ steht auf „Nur ein Mandant“ – umstellen auf „Konten in allen Organisationsverzeichnissen … und persönliche Microsoft-Konten“. In der Übersicht steht danach „Alle Microsoft-Kontobenutzer“.",
        "Bei „Umleitungs-URI“ die Plattform „Web“ wählen und die Rückleitungsadresse unten eintragen (so, ohne /api/v1) → „Registrieren“.",
        "In der Übersicht stehen drei Kennungen untereinander. Nur „Anwendungs-ID (Client)“ kopieren und hier als Client-ID eintragen – nicht Objekt-ID, nicht Verzeichnis-ID.",
        "„Zertifikate & Geheimnisse“ → „Neuer geheimer Clientschlüssel“ → Ablauf wählen (höchstens 24 Monate) → „Hinzufügen“. Aus der Spalte „Wert“ kopieren, nicht „Geheimnis-ID“. Der Wert wird nur jetzt angezeigt; verpasst → neuen Schlüssel anlegen, alten löschen.",
        "Freiwillig: „Branding und Eigenschaften“ → Startseite, Nutzungsbedingungen und Datenschutz mit den Adressen unten; ein Logo braucht es nicht.",
        "Steht dort orange „nicht überprüft“ (Herausgeber): Private Konten können sich trotzdem anmelden, nur manche Firmen- oder Schulkonten sind gesperrt. Die Herausgeberprüfung ist freiwillig.",
        "Der Schlüssel läuft nach höchstens 24 Monaten ab, danach geht die Anmeldung mit Microsoft still nicht mehr. Den Tag unten bei „Schlüssel läuft ab am“ eintragen, dann kommt rechtzeitig eine Erinnerung.",
    ]),
    "apple": ("https://developer.apple.com/account/resources/identifiers/list/serviceId", [
        "Braucht ein Apple-Entwicklerkonto (kostenpflichtig). Nötig erst mit einer iPhone-Fassung der App.",
        "Unter „Identifiers“ eine Services ID anlegen, „Sign in with Apple“ aktivieren, Domain und Rückleitungsadresse unten eintragen.",
        "Unter „Keys“ einen Schlüssel mit „Sign in with Apple“ erzeugen und die .p8-Datei laden.",
        "Services ID (als Client-ID), Team-ID, Key-ID und den Inhalt der .p8-Datei hier eintragen.",
    ]),
}
# Feldname für den Schlüssel, so wie ihn der Anbieter nennt
SCHLUESSEL_NAME = {"google": "Clientschlüssel", "discord": "Client Secret",
                   "microsoft": "Wert des geheimen Clientschlüssels"}


def _tage(ablauf) -> int | None:
    from app.db import utcnow

    return None if ablauf is None else (ablauf - utcnow().date()).days


def _dienste(db: Session, basis: str) -> list[dict]:
    return [{"id": d, "name": anmeldedienste.DIENSTE[d]["name"], "k": anmeldedienste.konfig(db, d),
             "rueck": anmeldedienste.rueckleitung(basis, d), "link": ANLEITUNG[d][0], "schritte": ANLEITUNG[d][1],
             "schluessel_name": SCHLUESSEL_NAME.get(d, "Clientschlüssel (Secret)"),
             "ablauf": anmeldedienste.ablauf(db, d), "mit_ablauf": d in anmeldedienste.ABLAUF_DIENSTE,
             "tage": _tage(anmeldedienste.ablauf(db, d))}
            for d in anmeldedienste.REIHENFOLGE]


@router.get("/anmeldung", response_class=HTMLResponse)
def anmeldung(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db), fehler: str = ""):
    return seite(request, user, db, fehler=fehler)


def seite(request: Request, user: User, db: Session, fehler: str = "", faktor_neu: dict | None = None,
          faktor_codes: list[str] | None = None) -> HTMLResponse:
    """Seite „Anmeldung“: oben der eigene zweite Faktor (seit 0.4.86 hier statt unter Konten), darunter die
    Anmeldedienste für alle Mitglieder."""
    from app.verwaltung import zweiter_faktor

    basis = oeffentliche_adresse(db, request)
    from app import passkeys, vertraut

    faktor = {"aktiv": zweiter_faktor.aktiv(db, user.id), "codes_uebrig": zweiter_faktor.restliche_codes(db, user.id),
              "neu": faktor_neu, "codes": faktor_codes,
              # 0.4.143: Passkeys und vertraute Geräte (S44)
              "passkeys": passkeys.anzeige(db, user.id), "passkeys_moeglich": passkeys.verfuegbar(db),
              "frisch": zweiter_faktor.frisch(db, user.id, sitzung=_sitzungs_bindung(request)),
              "vertraute": vertraut.liste(db, user.id)}
    return _seite(request, "anmeldung.html", user, db, dienste=_dienste(db, basis), basis=basis,
                  https=basis.startswith("https://"), mail_bereit=mail.kann_senden(db), fehler=fehler, faktor=faktor)


@router.post("/anmeldung/{dienst}", dependencies=[Depends(csrf_pruefen)])
async def anmeldung_speichern(dienst: str, request: Request, user: User = Depends(faktor_frisch),  # 0.4.143 (S44)
                              db: Session = Depends(get_db)):
    _ = tr(request)
    if dienst not in anmeldedienste.DIENSTE:
        return _zurueck("/anmeldung")
    form = await request.form()
    wert = {k: str(form.get(k, "")).strip() for k in ("client_id", "client_secret", "team_id", "key_id",
                                                        "private_key", "entfernen", "ablauf")}
    if wert["entfernen"]:
        for k in ("client_id", "client_secret", "team_id", "key_id", "private_key", "ablauf"):
            meta_schreiben(db, f"oidc.{dienst}.{k}", "")
        db.commit()
        return _zurueck(f"/anmeldung#{dienst}", "gespeichert")
    if dienst == "apple" and wert["private_key"] and "PRIVATE KEY" not in wert["private_key"]:
        antwort = anmeldung(request, user, db, fehler=_("Bitte den ganzen Inhalt der .p8-Datei einfügen (mit BEGIN/END PRIVATE KEY)."))
        antwort.status_code = 400
        return antwort
    if dienst in anmeldedienste.ABLAUF_DIENSTE and "ablauf" in form:  # 0.4.157: leer = kein Datum
        from datetime import date

        try:
            tag = date.fromisoformat(wert["ablauf"]) if wert["ablauf"] else None
        except ValueError:
            antwort = anmeldung(request, user, db, fehler=_("Bitte ein gültiges Datum eintragen."))
            antwort.status_code = 400
            return antwort
        meta_schreiben(db, f"oidc.{dienst}.ablauf", tag.isoformat() if tag else "")
        meta_schreiben(db, f"melden.anmeldeschluessel.{dienst}", "")
    for k in ("client_id", "team_id", "key_id"):
        if wert[k] or k == "client_id":
            meta_schreiben(db, f"oidc.{dienst}.{k}", wert[k][:300])
    for k in ("client_secret", "private_key"):  # nur schreibbar: leer = behalten
        if wert[k]:
            meta_schreiben(db, f"oidc.{dienst}.{k}", wert[k][:5000])
    db.commit()
    return _zurueck(f"/anmeldung#{dienst}", "gespeichert")
