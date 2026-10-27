"""Schnittstelle 0.4.22: Nutzungsbedingungen und Impressum je Server – ServerInfo.termsUrl/termsVersion/legalNoticeUrl,
Registrierung mit acceptTerms/termsVersion, User.termsAcceptedVersion, PUT /me/terms, 403 terms_required mit den
Ausnahmen, Vorlage unter /nutzungsbedingungen, neue Fassung in der Verwaltung."""
from tests.test_konto import daten, einladung
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

API = "/api/v1"


def test_info_und_oeffentliche_seite(client, world, dbs, admin):  # noqa: F811
    info = client.get(f"{API}/info").json()
    assert info["termsUrl"] == "http://testserver/nutzungsbedingungen" and info["termsVersion"] == 1
    assert info["legalNoticeUrl"] is None and info["apiVersion"] == "0.4.24"
    seite = client.get("/nutzungsbedingungen")
    assert seite.status_code == 200 and "<h1>Nutzungsbedingungen</h1>" in seite.text and "Fassung 1" in seite.text
    assert "jede für sich selbst" in seite.text and "nicht Vertragspartner" in seite.text
    en = client.get("/nutzungsbedingungen", headers={"Accept-Language": "en"}).text
    assert "<h1>Terms of use</h1>" in en and "each person for themselves" in en
    # Impressum veröffentlicht → legalNoticeUrl zeigt auf die Seite dieses Servers; Link → der Link
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "impressum.text", "# Impressum\n\nNebelfeld-Runde, Lindenweg 3, 12345 Nebelfeld, kontakt@nebelfeld.example")
    dbs.commit()
    assert client.get(f"{API}/info").json()["legalNoticeUrl"] == "http://testserver/impressum"
    meta_schreiben(dbs, "impressum.url", "https://nebelfeld.example/impressum")
    dbs.commit()
    assert client.get(f"{API}/info").json()["legalNoticeUrl"] == "https://nebelfeld.example/impressum"
    # Eigener Link für die Bedingungen
    r = client.post("/verwaltung/assistent/nutzungsbedingungen", data={"csrf": admin, "aktion": "link",
                                                                        "eigener_link": "http://unsicher.example"})
    assert r.status_code == 400
    r = client.post("/verwaltung/assistent/nutzungsbedingungen", follow_redirects=False,
                    data={"csrf": admin, "aktion": "link", "eigener_link": "https://nebelfeld.example/agb"})
    assert r.headers["location"] == "/verwaltung/assistent/sicherung"
    assert client.get(f"{API}/info").json()["termsUrl"] == "https://nebelfeld.example/agb"
    r = client.get("/nutzungsbedingungen", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://nebelfeld.example/agb"
    # Eigener Text: Platzhalter müssen weg, zu kurz geht nicht
    r = client.post("/verwaltung/assistent/nutzungsbedingungen", data={"csrf": admin, "aktion": "speichern",
                                                                        "text": "# Regeln\n\nKurz."})
    assert r.status_code == 400 and "zu kurz" in r.text
    text = "# Regeln\n\n" + "Sei nett zu den anderen am Tisch und räum danach die Würfel weg. " * 3
    r = client.post("/verwaltung/assistent/nutzungsbedingungen", data={"csrf": admin, "aktion": "speichern",
                                                                        "text": text + "[offen]"})
    assert r.status_code == 400 and "eckigen Klammern" in r.text
    r = client.post("/verwaltung/assistent/nutzungsbedingungen", follow_redirects=False,
                    data={"csrf": admin, "aktion": "speichern", "text": text})
    assert r.status_code == 303
    assert "räum danach die Würfel weg" in client.get("/nutzungsbedingungen").text
    assert client.get(f"{API}/info").json()["termsUrl"] == "http://testserver/nutzungsbedingungen"


def test_registrierung_braucht_zustimmung(client, world):
    w = world
    code = einladung(client, w)
    r = client.post(f"{API}/auth/register", json=daten(inviteCode=code, acceptTerms=False))
    assert r.status_code == 400 and r.json()["code"] == "consent_required"
    r = client.post(f"{API}/auth/register", json={k: v for k, v in daten(inviteCode=code).items() if k != "termsVersion"})
    assert r.status_code == 400 and r.json()["code"] == "consent_required"
    r = client.post(f"{API}/auth/register", json=daten(inviteCode=code, termsVersion=2))
    assert r.status_code == 409 and r.json()["code"] == "terms_changed"
    r = client.post(f"{API}/auth/register", json=daten(inviteCode=code))
    assert r.status_code == 201, r.text
    u = r.json()["user"]
    assert u["termsAcceptedVersion"] == 1 and u["termsAcceptedAt"]
    h = {"Authorization": f"Bearer {r.json()['accessToken']}"}
    export = client.get(f"{API}/me/export", headers=h).json()
    assert export["account"]["termsAcceptedVersion"] == 1 and export["account"]["termsAcceptedAt"]


def test_ohne_zustimmung_nur_lesen(client, world, make_user, login, dbs):
    w = world
    make_user("ohne", terms=False)
    h = login("ohne")
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    # Beitreten ist eine ändernde Anfrage → 403 mit lesbarer Meldung
    r = client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Kuno"}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "terms_required" and "Nutzungsbedingungen" in r.json()["message"]
    assert client.post(f"{API}/campaigns", json={"title": "Neu"}, headers=h).status_code == 403
    # Lesen geht, das Profil zeigt null
    me = client.get(f"{API}/me", headers=h).json()
    assert me["termsAcceptedVersion"] is None and client.get(f"{API}/campaigns", headers=h).status_code == 200
    # Ausnahmen: Daten herunterladen, Stimmprofil löschen, alles unter /auth, Konto löschen
    assert client.get(f"{API}/me/export", headers=h).status_code == 200
    assert client.delete(f"{API}/me/voice-profile", headers=h).status_code == 204
    assert client.post(f"{API}/auth/login", json={"username": "ohne", "password": "geheim123"}).status_code == 200
    # Zustimmen: falsche Fassung 409, dann 200 und die ändernde Anfrage geht
    r = client.put(f"{API}/me/terms", json={"accept": True, "version": 7}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "terms_changed"
    r = client.put(f"{API}/me/terms", json={"accept": True, "version": 1}, headers=h)
    assert r.status_code == 200 and r.json()["termsAcceptedVersion"] == 1
    r = client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Kuno"}, headers=h)
    assert r.status_code == 200, r.text
    # Zustimmung zu Aufnahmen: geben braucht die Bedingungen, widerrufen nicht; begonnene Uploads laufen weiter
    assert client.put(f"{API}/campaigns/{w['cid']}/recording-consent", json={"granted": True}, headers=h).status_code == 200
    make_user("spaeter", terms=False)
    h2 = login("spaeter")
    from sqlalchemy import select

    from app.models import User

    u = dbs.scalar(select(User).where(User.username == "ohne"))
    u.terms_accepted_version = None  # wie ein Konto von vor dem Update
    dbs.commit()
    assert client.put(f"{API}/campaigns/{w['cid']}/recording-consent", json={"granted": True}, headers=h).status_code == 403
    assert client.put(f"{API}/campaigns/{w['cid']}/recording-consent", json={"granted": False}, headers=h).status_code == 200
    assert client.post(f"{API}/campaigns", json={"title": "Neu"}, headers=h2).status_code == 403
    assert client.request("DELETE", f"{API}/me", json={"password": "geheim123"}, headers=h2).status_code == 204


def test_uploads_ohne_zustimmung_fertig(client, world, dbs, tmp_path):
    """Eine schon begonnene Aufnahme darf fertig hochgeladen werden, auch wenn inzwischen eine neue Fassung gilt."""
    from sqlalchemy import select

    from app.models import User
    from tests.test_step2a import audio_abschnitte, hochladen, neue_session

    w = world
    s = neue_session(client, w)
    dateien = audio_abschnitte(tmp_path, (20,))
    files = [{"fileName": d.name, "sizeBytes": d.stat().st_size, "mimeType": "audio/mp4"} for d in dateien]
    up = client.post(f"{API}/sessions/{s['id']}/uploads", json={"source": "table", "files": files}, headers=w["gm"]).json()
    u = dbs.scalar(select(User).where(User.username == "anna"))
    u.terms_accepted_version = None
    dbs.commit()
    assert client.post(f"{API}/campaigns/{w['cid']}/sessions", headers=w["gm"], json={
        "playedAt": "2026-09-21T18:00:00Z", "attendees": []}).status_code == 403
    f = up["files"][0]
    r = client.put(f"{API}/uploads/{up['uploadId']}/files/{f['fileId']}/chunks/0", content=dateien[0].read_bytes(),
                   headers=w["gm"])
    assert r.status_code == 204
    assert client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=w["gm"]).status_code == 202
    del hochladen


def test_neue_fassung_in_der_verwaltung(client, world, dbs, admin):  # noqa: F811
    w = world
    r = client.post("/verwaltung/nutzungsbedingungen/neue-fassung", data={"csrf": admin, "bestaetigung": "falsch"},
                    follow_redirects=False)
    assert r.status_code == 303 and "nb=falsch" in r.headers["location"]
    assert client.get(f"{API}/info").json()["termsVersion"] == 1
    name = client.get(f"{API}/info").json()["name"]
    r = client.post("/verwaltung/nutzungsbedingungen/neue-fassung", data={"csrf": admin, "bestaetigung": name},
                    follow_redirects=False)
    assert "nb=neu" in r.headers["location"]
    seite = client.get("/verwaltung/einstellungen?nb=neu").text
    assert "Neue Fassung 2." in seite and "Fassung</span>" not in seite
    assert client.get(f"{API}/info").json()["termsVersion"] == 2
    # Alle Konten müssen erneut zustimmen – lesen geht, ändern nicht
    assert client.get(f"{API}/campaigns", headers=w["gm"]).status_code == 200
    r = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"])
    assert r.status_code == 403 and r.json()["code"] == "terms_required"
    assert client.put(f"{API}/me/terms", json={"accept": True, "version": 1}, headers=w["gm"]).status_code == 409
    assert client.put(f"{API}/me/terms", json={"accept": True, "version": 2}, headers=w["gm"]).status_code == 200
    assert client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).status_code == 201
