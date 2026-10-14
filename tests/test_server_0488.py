"""Server 0.4.88: Kleinigkeiten neben der Schnittstelle 0.4.18 (S21.4)."""
import pytest

from tests.test_pruefung_046 import _zur_pruefung
from tests.test_step2a import API
from tests.test_step2b import _kleine_teile, zusammenfassen  # noqa: F401 (Fixture)

pytestmark = pytest.mark.usefixtures("_kleine_teile")


def test_s21_4_chronik_ehemaliger_ohne_spaetere_aenderung(client, world, dbs, tmp_path):
    w = world
    s = _zur_pruefung(client, w, dbs, tmp_path)
    client.put(f"{API}/sessions/{s['id']}/recap", headers=w["gm"], json={"text": "Wie es war.\n\nAlles gut."})
    assert client.post(f"{API}/sessions/{s['id']}/publish", headers=w["gm"]).status_code == 200
    url = f"{API}/campaigns/{w['cid']}/members/me/chronicle"
    assert client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["pl"]).status_code == 204
    [k] = client.get(url, headers=w["pl"]).json()["sessions"]
    assert k["attended"] and k["recap"]["text"].startswith("Wie es war.")
    # Ändert sich das Kapitel nach dem Austritt (heute über die Schnittstelle nicht möglich – veröffentlichte Kapitel
    # sind fest; Schutz für später), sehen Ehemalige das nicht
    from app.db import utcnow
    from app.models import Recap

    r = dbs.get(Recap, s["id"])
    r.text, r.edited_at = "SPAETER-ERGAENZT.\n\nNeu.", utcnow()
    dbs.commit()
    [k] = client.get(url, headers=w["pl"]).json()["sessions"]
    assert k["attended"] and k["recap"] is None
