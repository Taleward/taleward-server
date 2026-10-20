"""Server 0.4.111: Vorschläge aus Handouts nie mit Geheimem der Bibel, mehr Schreibweisen der Geheim-Markierung,
erstes Passwort nur mit Zustimmung zu den Nutzungsbedingungen."""
import pytest

from tests.test_unterlagen import api  # noqa: F401  (Fixture)

API = "/api/v1"


# ---------------------------------------------------------------- Sicherheit: Vorschläge aus Handouts, Markierungen
def test_handout_vorschlag_mit_geheimem_der_bibel_bleibt_geheim(client, world, dbs, api):  # noqa: F811
    from app.models import Entry
    from tests.dateien import pdf
    from tests.test_step5 import einstellen
    from tests.test_unterlagen import Unterlagenmodell, bibel, hochladen, verarbeiten, vorschlag

    w = world
    bibel(client, w)
    e = dbs.query(Entry).filter_by(name="Rabenfels").one()
    e.gm_notes = "Unter dem alten Leuchtturm liegt der Eingang zur Schmugglerhöhle."
    dbs.commit()
    geheim = vorschlag(entryType="location", title="Alter Leuchtturm", gmNotes="",
                       detail="Unter dem alten Leuchtturm liegt der Eingang zur Schmugglerhöhle.")
    harmlos = vorschlag(title="Borin", detail="Wirt mit rotem Bart.", gmNotes="")
    api["modell"] = Unterlagenmodell([geheim, harmlos])
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234")
    d = hochladen(client, w, "a.pdf", pdf(["Der Wirt heisst Borin."]), "handout").json()
    assert verarbeiten(dbs)
    vs = {v["title"]: v for v in client.get(f"{API}/documents/{d['id']}/proposals", headers=w["gm"]).json()}
    lt = vs["Alter Leuchtturm"]
    assert lt["suggestedVisibility"] == "gm_only" and lt["detail"] == "" and "Schmugglerhöhle" in lt["gmNotes"]
    assert not lt["publicSuggested"]
    assert vs["Borin"]["suggestedVisibility"] == "public"  # Unverfängliches aus Handouts bleibt öffentlich


@pytest.mark.parametrize("text", [
    "SL Info: Borin ist ein Spion.", "GM Info: Borin ist ein Spion.", "SL-Notiz: Borin ist ein Spion.",
    "- [SL] Borin ist ein Spion.", "* Geheim: Borin ist ein Spion.", "**SL-Info:** Borin ist ein Spion.",
    "• GM-Notes: Borin ist ein Spion.", "> secret: Borin ist ein Spion.",
])
def test_weitere_schreibweisen_der_markierung(text):
    from app.unterlagen import geheime_abschnitte

    gefunden = geheime_abschnitte([{"text": "Der Wirt heisst Borin.\n" + text + "\nNoch ein Satz."}])
    assert any("Spion" in g for g in gefunden), text
    assert not any("Der Wirt heisst" in g for g in gefunden)


def test_kein_falscher_treffer_ohne_doppelpunkt():
    from app.unterlagen import geheime_abschnitte

    assert geheime_abschnitte([{"text": "Geheimnisse gibt es viele.\n\nSL ist heute Anna."}]) == []


def test_passwort_neu_anlegen_braucht_die_bedingungen(client, world, dbs):
    from sqlalchemy import delete, select

    from app.models import AuthMethod, User

    u = dbs.scalar(select(User).where(User.username == "ben"))
    dbs.execute(delete(AuthMethod).where(AuthMethod.user_id == u.id))  # wie ein Konto nur mit Google/Discord
    u.terms_accepted_version = None
    dbs.commit()
    r = client.put(f"{API}/me/password", json={"newPassword": "Ein-ganz-neues-Wort-42"}, headers=world["pl"])
    assert r.status_code == 403 and r.json()["code"] == "terms_required"
