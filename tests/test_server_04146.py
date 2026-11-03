"""Server 0.4.146: Anleitungen für die Anmeldedienste (Microsoft nach Benjamins Durchgang, Google und Discord mit
denselben Stolperstellen) und Namen der Anwesenden als Schreibhilfe für die Abschrift."""
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401


def test_anleitung_microsoft_und_kopierknopf(client, dbs, admin):  # noqa: F811
    from app.verwaltung.anmeldung import ANLEITUNG

    seite = client.get("/verwaltung/anmeldung").text
    assert "portal.azure.com" in seite and "entra.microsoft.com" not in seite
    for satz in ("„Verzeichnis wechseln“", "„Unterstützte Kontotypen“", "Nur „Anwendungs-ID (Client)“",
                 "Aus der Spalte „Wert“ kopieren, nicht „Geheimnis-ID“", "höchstens 24 Monate", "„nicht überprüft“",
                 "„App veröffentlichen“", "„Save Changes“"):
        assert satz in seite, satz
    assert "Wert des geheimen Clientschlüssels" in seite  # Feld so benannt wie beim Anbieter
    assert 'data-kopieren="rueck-microsoft"' in seite and "kopieren.js" in seite
    assert "/nutzungsbedingungen</code>" in seite and "/datenschutz</code>" in seite
    en = client.get("/verwaltung/anmeldung", headers={"Accept-Language": "en"}).text
    assert "Copy only “Application (client) ID”" in en or "Application (client) ID" in en
    assert len(ANLEITUNG["microsoft"][1]) == 8
    js = client.get("/verwaltung/static/kopieren.js")
    assert js.status_code == 200 and ".innerHTML" not in js.text


def test_anwesende_stehen_vorn_in_der_schreibhilfe(client, world, dbs):
    from app.models import Campaign, GameSession, Member
    from app.namenshilfe import anwesende, fuer_kampagne

    w = world
    m = dbs.get(Member, w["pl_member"])
    m.character_name, m.character_nickname = "Orasilas Albio", "Ora"
    dbs.commit()
    att = [{"memberId": w["gm_member"], "consent": True, "consentSource": "on_site"},
           {"memberId": w["pl_member"], "consent": True, "consentSource": "on_site"},
           {"guestName": "Arlekin Dehsch", "consent": True, "consentSource": "on_site"}]
    r = client.post(f"{API}/campaigns/{w['cid']}/sessions", headers=w["gm"],
                    json={"playedAt": "2026-11-01T18:00:00Z", "attendees": att})
    assert r.status_code == 201, r.text
    s = dbs.get(GameSession, r.json()["id"])
    assert anwesende(dbs, s) == ["Orasilas Albio", "Ora", "Arlekin Dehsch"]  # ohne SL, ohne Anzeigenamen
    liste = fuer_kampagne(dbs, dbs.get(Campaign, w["cid"]), s.number, s)
    assert liste[:3] == ["Orasilas Albio", "Ora", "Arlekin Dehsch"]
    assert len([n for n in liste if n.casefold() == "orasilas albio"]) == 1  # nicht doppelt
    assert "Arlekin Dehsch" not in fuer_kampagne(dbs, dbs.get(Campaign, w["cid"]), s.number)  # ohne Runde wie bisher
