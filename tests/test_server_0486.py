"""Server 0.4.86: zweiter Faktor unter Verwaltung → Anmeldung, S30 (Heimnetz-Paket, TOTP-Code nur einmal auch bei
gleichzeitigen Anfragen)."""
import time
from pathlib import Path

from sqlalchemy import select

from tests.test_server_0484 import _code
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


def test_zweiter_faktor_unter_anmeldung(client, dbs, admin):  # noqa: F811
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf

    assert "Dein zweiter Faktor" in client.get("/verwaltung/anmeldung").text
    assert "Dein zweiter Faktor" not in client.get("/verwaltung/konten").text
    r = client.post("/verwaltung/zweiter-faktor/beginnen", data={"csrf": admin, "password": "geheim123"})
    assert r.status_code == 200 and "So melden sich Mitglieder an" in r.text and "data:image/svg+xml" in r.text
    chef = dbs.scalar(select(User).where(User.username == "chef"))
    r = client.post("/verwaltung/zweiter-faktor/bestaetigen", data={"csrf": admin, "code": _code(dbs, chef.id)})
    assert r.status_code == 200 and "Wiederherstellungscodes" in r.text and "So melden sich Mitglieder an" in r.text
    r = client.post("/verwaltung/zweiter-faktor/beginnen", data={"csrf": r.text.split('name="csrf" value="')[1]
                                                                 .split('"')[0], "password": "geheim123"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/anmeldung#zweiter-faktor"
    dbs.expire_all()
    assert zf.aktiv(dbs, chef.id)
    # In der Kontenliste bleibt der Hinweis, wer einen zweiten Faktor hat
    assert "zweiter Faktor" in client.get("/verwaltung/konten").text


def test_s30_2_code_gilt_auch_gleichzeitig_nur_einmal(client, dbs, admin, monkeypatch):  # noqa: F811
    """Zwei Anfragen haben denselben Stand gelesen und prüfen denselben Code – nur eine darf gelten."""
    from app.db import session_factory
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf

    chef = dbs.scalar(select(User).where(User.username == "chef"))
    zf.beginnen(dbs, chef.id)
    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    dbs.commit()
    spaeter = time.time() + 60
    code = _code(dbs, chef.id, spaeter)
    with session_factory()() as a, session_factory()() as b:
        alt_a = zf.meta_lesen(a, f"verwaltung.totp.{chef.id}")  # beide lesen, bevor eine schreibt
        alt_b = zf.meta_lesen(b, f"verwaltung.totp.{chef.id}")
        assert alt_a == alt_b
        assert zf.pruefen(a, chef.id, code, jetzt=spaeter)
        a.commit()
        echt = zf.meta_lesen
        monkeypatch.setattr(zf, "meta_lesen", lambda db, key, standard=None: alt_b if db is b else echt(db, key,
                                                                                                        standard))
        assert not zf.pruefen(b, chef.id, code, jetzt=spaeter)  # Stand hat sich geändert → gilt nicht
        b.commit()
    monkeypatch.undo()
    dbs.expire_all()
    assert zf.stand(dbs, chef.id)["zuletzt"] == int(spaeter // 30)
    # Wiederherstellungscode genauso
    zf.ausschalten(dbs, chef.id)
    zf.beginnen(dbs, chef.id)
    codes = zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    dbs.commit()
    with session_factory()() as a, session_factory()() as b:
        alt_b = zf.meta_lesen(b, f"verwaltung.totp.{chef.id}")
        assert zf.pruefen(a, chef.id, codes[0])
        a.commit()
        echt = zf.meta_lesen
        monkeypatch.setattr(zf, "meta_lesen", lambda db, key, standard=None: alt_b if db is b else echt(db, key,
                                                                                                        standard))
        assert not zf.pruefen(b, chef.id, codes[0])
    monkeypatch.undo()
    dbs.expire_all()
    assert zf.restliche_codes(dbs, chef.id) == zf.ANZAHL_CODES - 1


def test_s30_1_heimnetz_paket_ohne_netzfilter():
    heim = Path("deploy/docker-compose.heimnetz.yml").read_text(encoding="utf-8")
    assert "VERWALTUNG_NUR_AUS:" not in heim
    assert "VERWALTUNG_NUR_AUS: ${VERWALTUNG_NUR_AUS:-}" in Path("deploy/docker-compose.yml").read_text(encoding="utf-8")
    assert "nicht im Heimnetz-Paket" in Path("INSTALLATION.md").read_text(encoding="utf-8")
