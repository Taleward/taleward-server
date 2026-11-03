"""Server 0.4.145: feste Fassung statt mistral-large-latest, Large 4 mit Denkstufe, Antworten als Liste von Teilen;
Schnittstelle 0.4.29 (Klasse und Stufe am Charakter); Hinweis, wenn Einstellungen nicht zur Betriebsart passen."""
import json

import httpx

from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401

CHARAKTER = {"id": "6f1c1f9e-2d1b-4c55-9a51-0d6a1d2f3b4c", "version": 1, "name": "Mira", "status": "active"}


# ---------------------------------------------------------------- Mistral Large 4
def _klient(modell, antwort):
    from app import sprachmodell as sm

    gesendet = []

    def handler(request):
        gesendet.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": antwort}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    k = sm.OpenAIKlient("https://api.mistral.ai/v1", "sk-x", modell,
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    return k, gesendet


def test_denkstufe_und_antwort_in_teilen():
    from app import sprachmodell as sm

    teile = [{"type": "thinking", "thinking": [{"type": "text", "text": "erst überlegen"}]},
             {"type": "text", "text": '{"ok": '}, {"type": "text", "text": "true}"}]
    k, gesendet = _klient("mistral-large-4-0:low", teile)
    a = k.chat("System", "Nutzer")
    assert a.text == '{"ok": true}'
    assert gesendet[0]["model"] == "mistral-large-4-0" and gesendet[0]["reasoning_effort"] == "low"
    assert k.cent_pro_mio == (70, 210)
    k, gesendet = _klient("mistral-large-2512", '{"ok": true}')
    assert k.chat("S", "N").text == '{"ok": true}' and "reasoning_effort" not in gesendet[0]
    assert sm.modell_teilen("mistral-large-4-0:max") == ("mistral-large-4-0:max", None)  # unbekannte Stufe bleibt Name
    assert sm.antwort_text(None) == "" and sm.antwort_text([{"type": "image"}]) == ""


def test_feste_fassung_als_standard_und_alte_werte_umgestellt(client, dbs, admin):  # noqa: F811
    from app.cloudanbieter import ANBIETER
    from app.config import get_settings
    from app.einstellungen import meta_schreiben

    mistral = next(a for a in ANBIETER if a.id == "mistral")
    assert mistral.modell_vorschlaege == "mistral-large-2512" and "mistral-large-latest" not in mistral.modelle
    assert {"mistral-large-4-0", "mistral-large-4-0:low"} <= set(mistral.modelle)
    assert get_settings().llm_api_model == "mistral-large-2512"
    # Hinweis, solange noch der wandernde Name eingestellt ist
    meta_schreiben(dbs, "llm.art", "api")
    meta_schreiben(dbs, "llm.api_modell_vorschlaege", "mistral-large-latest")
    dbs.commit()
    seite = client.get("/verwaltung/zusammenfassung").text
    assert "„mistral-large-latest“ zeigt immer auf die neueste Fassung" in seite
    meta_schreiben(dbs, "llm.api_modell_vorschlaege", "mistral-large-2512")
    dbs.commit()
    assert "zeigt immer auf die neueste Fassung" not in client.get("/verwaltung/zusammenfassung").text


def test_migration_stellt_gespeicherte_werte_um(tmp_path):
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "f4b6d8e0a2c3")
    e = sa.create_engine(url)
    with e.begin() as c:
        for k, v in (("llm.api_modell", "mistral-large-latest"), ("llm.api_modell_vorschlaege", "mistral-large-latest"),
                     ("llm.api_modell_notizen", "mistral-small-latest"), ("anders", "mistral-large-latest")):
            c.execute(sa.text("INSERT INTO server_meta (key, value) VALUES (:k, :v)"), {"k": k, "v": v})
    command.upgrade(cfg, "a5c7e9f1b3d4")
    with e.begin() as c:
        werte = dict(c.execute(sa.text("SELECT key, value FROM server_meta")).all())
    assert werte["llm.api_modell"] == werte["llm.api_modell_vorschlaege"] == "mistral-large-2512"
    assert werte["llm.api_modell_notizen"] == "mistral-small-latest" and werte["anders"] == "mistral-large-latest"


# ---------------------------------------------------------------- 0.4.29: Klasse und Stufe
def _setzen(client, w, h=None, **felder):
    return client.put(f"{API}/campaigns/{w['cid']}/members/me/character", headers=h or w["pl"],
                      json=CHARAKTER | felder)


def _ich(client, w, h):
    return {m["id"]: m for m in client.get(f"{API}/campaigns/{w['cid']}", headers=h).json()["members"]}


def test_klassen_und_stufe_fuer_alle_sichtbar(client, world):
    w = world
    r = _setzen(client, w, classes=[{"name": " Magier" + chr(0x202E), "level": 3}, {"name": "Schurke", "level": 2}],
                level="Erfahren")
    assert r.status_code == 200, r.text
    m = _ich(client, w, w["gm"])[w["pl_member"]]
    assert m["characterClasses"] == [{"name": "Magier", "level": 3}, {"name": "Schurke", "level": 2}]
    assert m["characterLevel"] == "Erfahren"
    assert _ich(client, w, w["pl"])[w["pl_member"]]["characterClasses"][0]["name"] == "Magier"
    # PUT ersetzt den ganzen Stand: ohne Felder keine Klassen, keine Stufe
    assert _setzen(client, w, version=2).status_code == 200
    m = _ich(client, w, w["gm"])[w["pl_member"]]
    assert m["characterClasses"] == [] and m["characterLevel"] is None
    for falsch in ([{"name": "A", "level": 0}], [{"name": "A", "level": 31}], [{"name": " ", "level": 1}],
                   [{"name": "A", "level": 1}] * 5, [{"name": "x" * 41, "level": 1}]):
        r = _setzen(client, w, version=3, classes=falsch)
        assert r.status_code == 400 and r.json()["code"] == "validation_error", falsch
    assert _setzen(client, w, version=3, level="x" * 65).status_code == 400


def test_klassen_nie_an_ein_sprachmodell_und_in_der_auskunft(client, world, dbs):
    from app.models import GameSession
    from app.zusammenfassung import eingabe_bauen, recap_eingabe, vorschlag_eingabe

    w = world
    _setzen(client, w, classes=[{"name": "MARKER-KLASSE", "level": 7}], level="MARKER-STUFE")
    att = [{"memberId": w["gm_member"], "consent": True, "consentSource": "on_site"},
           {"memberId": w["pl_member"], "consent": True, "consentSource": "on_site"}]
    s = client.post(f"{API}/campaigns/{w['cid']}/sessions", headers=w["gm"],
                    json={"playedAt": "2026-11-01T18:00:00Z", "attendees": att}).json()
    basis = eingabe_bauen(dbs, dbs.get(GameSession, s["id"]))
    for ein in (recap_eingabe(basis), vorschlag_eingabe(dbs, dbs.get(GameSession, s["id"]), basis)):
        text = json.dumps(ein, ensure_ascii=False)
        assert "MARKER-KLASSE" not in text and "MARKER-STUFE" not in text
    auskunft = client.get(f"{API}/me/export", headers=w["pl"]).json()
    mitglied = next(m for m in auskunft["memberships"] if m["memberId"] == w["pl_member"])
    assert mitglied["characterClasses"] == [{"name": "MARKER-KLASSE", "level": 7}]
    assert mitglied["characterLevel"] == "MARKER-STUFE"


def test_umzug_nur_mit_zustimmung(client, world, dbs):
    from app import umzug
    from app.models import Campaign

    w = world
    _setzen(client, w, classes=[{"name": "MARKER-KLASSE", "level": 7}])
    dbs.expire_all()
    paket, _d = umzug.packliste(dbs, dbs.get(Campaign, w["cid"]), set())
    assert "MARKER-KLASSE" not in json.dumps(paket, ensure_ascii=False, default=str)
    client.put(f"{API}/campaigns/{w['cid']}/members/me/move-consent", headers=w["pl"], json={"granted": True})
    dbs.expire_all()
    paket, _d = umzug.packliste(dbs, dbs.get(Campaign, w["cid"]), {w["pl_member"]})
    platz = next(p for p in paket["seats"] if p.get("character"))
    assert platz["character"]["classes"] == [{"name": "MARKER-KLASSE", "level": 7}]
    assert umzug._klassen([{"name": "A", "level": 99}, {"name": "", "level": 2}, "kaputt", {"name": "B", "level": 2}]) \
        == json.dumps([{"name": "B", "level": 2}])


# ---------------------------------------------------------------- Betriebsart und Einstellungen
def test_hinweis_wenn_einstellungen_nicht_zur_betriebsart_passen(client, dbs, admin):  # noqa: F811
    from app import einrichtung
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "betrieb.art", "lokal")
    meta_schreiben(dbs, "llm.art", "lokal")
    dbs.commit()
    assert einrichtung.widersprueche(dbs) == []
    assert "passen nicht zur Betriebsart" not in client.get("/verwaltung/").text
    meta_schreiben(dbs, "llm.art", "api")
    dbs.commit()
    seite = client.get("/verwaltung/").text
    assert "Einstellungen passen nicht zur Betriebsart „Lokaler Server“" in seite
    assert "Kapitel entstehen über die Cloud-API" in seite and 'href="/verwaltung/zusammenfassung"' in seite
    meta_schreiben(dbs, "betrieb.art", "beides")
    dbs.commit()
    texte = [x.text for x in einrichtung.widersprueche(dbs)]
    assert texte == ["Die Cloud-Abschrift als Ausweichlösung ist nicht eingerichtet."]
    meta_schreiben(dbs, "extern.anbieter", "mistral")
    meta_schreiben(dbs, "extern.api_key", "sk-test-schluessel-1234")
    meta_schreiben(dbs, "extern.after_hours", "0")
    meta_schreiben(dbs, "betrieb.art", "cloud")
    dbs.commit()
    assert einrichtung.widersprueche(dbs) == []
    meta_schreiben(dbs, "extern.after_hours", "12")
    dbs.commit()
    w = einrichtung.widersprueche(dbs)
    assert len(w) == 1 and w[0].n == 12
    client.get("/verwaltung/", headers={"Accept-Language": "en"})
