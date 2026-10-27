"""Server 0.4.109: Ohne Zustimmung zu den Nutzungsbedingungen geht alles weiter, was Daten zurücknimmt, löscht oder das
Konto absichert. Erteilen und Neues anlegen bleiben gesperrt."""
from sqlalchemy import select

API = "/api/v1"


def _ohne_zustimmung(dbs, username):
    from app.models import User

    u = dbs.scalar(select(User).where(User.username == username))
    u.terms_accepted_version = None  # wie ein Konto von vor dem Update oder nach einer neuen Fassung
    dbs.commit()


def _gesperrt(r):
    return r.status_code == 403 and r.json().get("code") == "terms_required"


def test_widerrufen_geht_erteilen_nicht(client, world, dbs):
    w = world
    assert client.put(f"{API}/campaigns/{w['cid']}/members/me/move-consent", json={"granted": True},
                      headers=w["pl"]).status_code == 200
    assert client.patch(f"{API}/me/voice-profile", json={"learnFromSessions": True}, headers=w["pl"]).status_code == 200
    _ohne_zustimmung(dbs, "ben")
    # Zurücknehmen
    r = client.put(f"{API}/campaigns/{w['cid']}/members/me/move-consent", json={"granted": False}, headers=w["pl"])
    assert r.status_code == 200, r.text
    r = client.patch(f"{API}/me/voice-profile", json={"learnFromSessions": False}, headers=w["pl"])
    assert r.status_code == 200, r.text
    # Erteilen bleibt gesperrt
    assert _gesperrt(client.put(f"{API}/campaigns/{w['cid']}/members/me/move-consent", json={"granted": True},
                                headers=w["pl"]))
    assert _gesperrt(client.patch(f"{API}/me/voice-profile", json={"learnFromSessions": True}, headers=w["pl"]))


def test_cloud_freigabe_ausschalten_geht_einschalten_nicht(client, world, dbs):
    w = world
    an = {"allowCloudSummary": True, "allowExternalTranscription": True}
    from tests.test_server_04132 import cloud_einrichten

    cloud_einrichten(dbs)  # 0.4.25: sonst bietet der Server beides nicht an (locks)
    assert client.patch(f"{API}/campaigns/{w['cid']}", json=an, headers=w["gm"]).status_code == 200
    _ohne_zustimmung(dbs, "anna")
    r = client.patch(f"{API}/campaigns/{w['cid']}", json={"allowCloudSummary": False,
                                                         "allowExternalTranscription": False}, headers=w["gm"])
    assert r.status_code == 200, r.text
    assert r.json()["allowCloudSummary"] is False
    assert _gesperrt(client.patch(f"{API}/campaigns/{w['cid']}", json={"allowCloudSummary": True}, headers=w["gm"]))
    # Ausschalten zusammen mit einer anderen Änderung: gesperrt, und nichts davon wird gespeichert
    r = client.patch(f"{API}/campaigns/{w['cid']}", json={"allowCloudSummary": False, "title": "Anders"},
                     headers=w["gm"])
    assert _gesperrt(r)
    c = client.get(f"{API}/campaigns/{w['cid']}", headers=w["gm"]).json()
    assert c["title"] == "Rabenfels"


def test_verlassen_und_eigenes_loeschen(client, world, dbs, make_user, login):
    w = world
    r = client.patch(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}",
                     json={"characterBackstory": "Aufgewachsen am Fluss."}, headers=w["pl"])
    assert r.status_code == 200, r.text
    _ohne_zustimmung(dbs, "ben")
    # Hintergrund leeren geht, ändern nicht
    assert _gesperrt(client.patch(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}",
                                  json={"characterBackstory": "Neu"}, headers=w["pl"]))
    r = client.patch(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", json={"characterBackstory": None},
                     headers=w["pl"])
    assert r.status_code == 200, r.text
    assert not _gesperrt(client.delete(f"{API}/campaigns/{w['cid']}/members/me/character", headers=w["pl"]))
    assert not _gesperrt(client.delete(f"{API}/me/email", headers=w["pl"]))
    assert not _gesperrt(client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}/portrait",
                                       headers=w["pl"]))
    # Verlassen
    r = client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["pl"])
    assert r.status_code == 204, r.text


def test_sl_entfernt_andere_nur_mit_zustimmung(client, world, dbs):
    w = world
    _ohne_zustimmung(dbs, "anna")
    assert _gesperrt(client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["gm"]))


def test_passwort_aendern_geht(client, world, dbs):
    w = world
    _ohne_zustimmung(dbs, "ben")
    r = client.put(f"{API}/me/password", json={"currentPassword": "geheim123", "newPassword": "Ein-ganz-neues-Wort-42"},
                   headers=w["pl"])
    assert r.status_code == 204, r.text


def test_kommentar_loeschen_geht_schreiben_nicht(client, world, dbs):
    from tests.test_step2a import neue_session

    w = world
    s = neue_session(client, w)
    k = client.post(f"{API}/sessions/{s['id']}/comments", json={"text": "Schöner Abend"}, headers=w["gm"])
    assert k.status_code == 201, k.text
    _ohne_zustimmung(dbs, "anna")
    assert _gesperrt(client.post(f"{API}/sessions/{s['id']}/comments", json={"text": "Noch einer"}, headers=w["gm"]))
    assert client.delete(f"{API}/comments/{k.json()['id']}", headers=w["gm"]).status_code == 204


# ---------------------------------------------------------------- Welt-Vorschlag aus Unterlagen
import pytest  # noqa: E402

from tests.test_unterlagen import Unterlagenmodell, api, bibel, hochladen, verarbeiten, vorschlag  # noqa: E402,F401


@pytest.mark.parametrize("kind", ["handout", "mixed"])
def test_welt_vorschlag_nie_mit_geheimem_der_bibel(client, world, dbs, api, kind):  # noqa: F811
    from app.models import Entry
    from tests.dateien import pdf
    from tests.test_step5 import einstellen

    w = world
    bibel(client, w)
    e = dbs.query(Entry).filter_by(name="Rabenfels").one()
    e.gm_notes = "Unter dem alten Leuchtturm liegt der Eingang zur Schmugglerhöhle."
    dbs.commit()
    api["modell"] = Unterlagenmodell([vorschlag()], welt="Küste. Unter dem alten Leuchtturm liegt der Eingang zur "
                                                          "Schmugglerhöhle.")
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234")
    d = hochladen(client, w, "a.pdf", pdf(["Der Wirt heisst Borin."]), kind).json()
    assert verarbeiten(dbs)
    assert client.get(f"{API}/documents/{d['id']}", headers=w["gm"]).json()["worldInfoSuggestion"] is None


def test_sl_info_klein_geschrieben_mitten_im_absatz():
    from app.unterlagen import geheime_abschnitte

    text = ("Der Wirt heisst Borin.\nsl-info: Borin ist ein Spion der Krone.\n\nGM-INFO: Der Schatz liegt im Brunnen.\n"
            "Ganz normaler Absatz.")
    gefunden = geheime_abschnitte([{"text": text}])
    assert any(g.startswith("sl-info: Borin ist ein Spion") for g in gefunden)
    assert any(g.startswith("GM-INFO: Der Schatz") for g in gefunden)
    assert not any("Der Wirt heisst Borin" in g for g in gefunden)
