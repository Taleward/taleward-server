"""Server 0.4.153: Erstattung von Restguthaben (Weg zum ersten echten Geld, Punkte 3 und 4) und die Liste der
Meldungen, die beim Zahlungsanbieter abonniert sein müssen."""
import json
from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from tests.test_abrechnung_0485 import _modus
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


def _konto(dbs, name="erna", email=None, bestaetigt=True):
    from app.models import User

    u = User(username=name, display_name=name.title(), email=email,
             email_verified_at=utcnow() if email and bestaetigt else None)
    dbs.add(u)
    dbs.flush()
    return u


def _bezahlt(dbs, g, netto, brutto, anbieter="stripe", tage=0, ref="ch_1"):
    from app import abrechnung
    from app.models import Bezahlvorgang

    am = utcnow() - timedelta(days=tage)
    v = Bezahlvorgang(guthaben_id=g.id, von=g.user_id or "x", anbieter=anbieter, netto_cent=netto, brutto_cent=brutto,
                      zustand="bezahlt", zahlung_ref=ref, anbieter_ref="cs_" + ref, angelegt=am, abgeschlossen=am)
    dbs.add(v)
    b = abrechnung.buchen(dbs, g, "zahlung", netto)
    v.buchung_id = b.id
    dbs.flush()
    return v


def test_erstattbar_zieht_zuerst_geschenktes(dbs):
    from app import abrechnung, erstattung

    _modus(dbs)
    u = _konto(dbs)
    g = abrechnung.guthaben_konto(dbs, u.id)
    assert erstattung.erstattbar(dbs, g) == 0
    _bezahlt(dbs, g, 1000, 1060)
    abrechnung.buchen(dbs, g, "gutschrift", 500)
    assert erstattung.erstattbar(dbs, g) == 1000
    abrechnung.buchen(dbs, g, "verbrauch", -400)  # aus dem Geschenkten
    assert erstattung.erstattbar(dbs, g) == 1000
    abrechnung.buchen(dbs, g, "verbrauch", -300)  # 100 Geschenk übrig? nein: 200 aus Bezahltem
    assert abrechnung.saldo(dbs, g.id) == 800
    assert erstattung.erstattbar(dbs, g) == 800
    abrechnung.buchen(dbs, g, "verbrauch", -900)
    assert erstattung.erstattbar(dbs, g) == 0  # im Minus: nichts
    assert erstattung.erstattbar(dbs, None) == 0


def test_aufteilen_neueste_zuerst_mit_gebuehr(dbs):
    from app import abrechnung, erstattung

    _modus(dbs)
    u = _konto(dbs)
    g = abrechnung.guthaben_konto(dbs, u.id)
    _bezahlt(dbs, g, 1000, 1060, tage=200, ref="alt")
    _bezahlt(dbs, g, 2000, 2090, anbieter="paypal", tage=3, ref="neu")
    abrechnung.buchen(dbs, g, "verbrauch", -500)
    a = erstattung.aufteilen(dbs, g)
    assert a.betrag_cent == 2500
    assert [t.zahlung_ref for t in a.teile] == ["neu", "alt"]
    assert [t.credit_cent for t in a.teile] == [2000, 500]
    assert a.teile[0].beim_anbieter_cent == 2090  # die ganze neue Zahlung samt Gebühr
    assert a.teile[1].beim_anbieter_cent == 530  # anteilige Gebühr, aufgerundet
    assert not a.teile[0].frist_vorbei and a.teile[1].frist_vorbei
    assert a.ueberweisung_noetig and a.von_hand_cent == 0
    # schon teilweise erstattete Zahlung zählt nur mit dem Rest
    v = a.teile[0].vorgang_id
    from app.models import Bezahlvorgang

    dbs.get(Bezahlvorgang, v).erstattet_cent = 1045
    b = erstattung.aufteilen(dbs, g, 1500)
    assert [t.credit_cent for t in b.teile] == [1000, 500]


def test_ohne_passende_zahlung_von_hand(dbs):
    from app import abrechnung, erstattung

    _modus(dbs)
    u = _konto(dbs)
    g = abrechnung.guthaben_konto(dbs, u.id)
    abrechnung.buchen(dbs, g, "zahlung", 700)  # Zahlung ohne Vorgang (Altbestand)
    a = erstattung.aufteilen(dbs, g)
    assert a.betrag_cent == 700 and a.teile == [] and a.von_hand_cent == 700 and a.ueberweisung_noetig


def test_kontoloeschung_legt_offene_erstattung_an(dbs):
    from app import abrechnung, erstattung
    from app.konto import entfernen
    from app.models import AbrechnungProtokoll, OffeneErstattung

    _modus(dbs)
    u = _konto(dbs, "erna", email="erna@example.org")
    g = abrechnung.guthaben_konto(dbs, u.id)
    _bezahlt(dbs, g, 1000, 1060)
    abrechnung.buchen(dbs, g, "gutschrift", 300)
    entfernen(dbs, u)
    dbs.commit()
    z = dbs.scalar(select(OffeneErstattung))
    assert z.betrag_cent == 1000 and z.email == "erna@example.org" and z.guthaben_id == g.id
    teile = json.loads(z.zahlungen)
    assert teile == [{"anbieter": "stripe", "ref": "ch_1", "cent": 1000, "beim_anbieter_cent": 1060}]
    assert "erna" not in z.zahlungen.replace("erna@", "")  # kein Name
    assert dbs.scalar(select(AbrechnungProtokoll).where(AbrechnungProtokoll.was == "erstattung_offen"))
    # unbestätigte Adresse wird nicht übernommen, ohne Erstattbares keine Zeile
    v = _konto(dbs, "veit", email="veit@example.org", bestaetigt=False)
    _bezahlt(dbs, abrechnung.guthaben_konto(dbs, v.id), 500, 540, ref="ch_2")
    entfernen(dbs, v)
    w = _konto(dbs, "wim")
    abrechnung.buchen(dbs, abrechnung.guthaben_konto(dbs, w.id), "gutschrift", 900)
    entfernen(dbs, w)
    dbs.commit()
    zeilen = erstattung.offene(dbs)
    assert len(zeilen) == 2 and zeilen[1].email is None and zeilen[1].betrag_cent == 500


def test_wartung_schliesst_und_loescht_adresse(dbs):
    from app import abrechnung, erstattung
    from app.konto import entfernen

    _modus(dbs)
    a = _konto(dbs, "anke", email="anke@example.org")
    ga = abrechnung.guthaben_konto(dbs, a.id)
    _bezahlt(dbs, ga, 1000, 1060, ref="ch_a")
    b = _konto(dbs, "bert", email="bert@example.org")
    gb = abrechnung.guthaben_konto(dbs, b.id)
    _bezahlt(dbs, gb, 800, 850, ref="ch_b")
    entfernen(dbs, a)
    entfernen(dbs, b)
    dbs.commit()
    za, zb = erstattung.offene(dbs)
    zb.angelegt = utcnow() - timedelta(days=erstattung.AUFBEWAHRUNG_TAGE + 1)
    abrechnung.buchen(dbs, ga, "erstattung", -1000)  # Meldung des Anbieters
    assert erstattung.wartung(dbs) == 2
    assert za.erledigt_at is not None and za.erledigt_wie == "erstattet" and za.email is None
    assert zb.erledigt_at is None and zb.email is None and zb.betrag_cent == 800
    assert erstattung.wartung(dbs) == 0


def test_paypal_merkt_erstattete_betraege(dbs):
    from app import abrechnung, zahlung

    _modus(dbs)
    u = _konto(dbs)
    g = abrechnung.guthaben_konto(dbs, u.id)
    v = _bezahlt(dbs, g, 1000, 1060, anbieter="paypal", ref="CAP9")
    assert zahlung._gegenbuchen(dbs, v, "erstattung", 530, "ev1")
    assert zahlung._gegenbuchen(dbs, v, "erstattung", 530, "ev2")
    assert v.erstattet_cent == 1060
    assert abrechnung.saldo(dbs, g.id) == 0


def test_verwaltung_zeigt_aufteilung_und_meldungen(client, dbs, admin):  # noqa: F811
    from app import abrechnung

    _modus(dbs)
    u = _konto(dbs, "erna")
    _bezahlt(dbs, abrechnung.guthaben_konto(dbs, u.id), 1000, 1060, ref="ch_sichtbar")
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "angabe.public_url", "https://chronik.example.org")
    dbs.commit()
    r = client.get("/verwaltung/abrechnung")
    assert r.status_code == 200
    for e in ("checkout.session.completed", "charge.refunded", "charge.dispute.created",
              "PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED", "CUSTOMER.DISPUTE.CREATED"):
        assert e in r.text
    assert "davon erstattbar" in r.text and 'id="erstattungen"' in r.text
    r = client.get("/verwaltung/abrechnung", params={"erstattung": "Erna"})
    assert "ch_sichtbar" in r.text and "10,60" in r.text
    r = client.get("/verwaltung/abrechnung", params={"erstattung": "niemand"})
    assert "Kein Konto mit diesem Benutzernamen." in r.text


def test_von_hand_erledigt_braucht_faktor_und_haken(client, dbs, admin):  # noqa: F811
    from app import abrechnung, erstattung
    from app.konto import entfernen

    _modus(dbs)
    u = _konto(dbs, "erna", email="erna@example.org")
    _bezahlt(dbs, abrechnung.guthaben_konto(dbs, u.id), 1000, 1060)
    entfernen(dbs, u)
    dbs.commit()
    z = erstattung.offene(dbs)[0]
    pfad = f"/verwaltung/abrechnung/erstattung/{z.id}/erledigt"
    r = client.post(pfad, data={"csrf": admin, "bestaetigt": "1"}, follow_redirects=False)
    assert r.status_code in (303, 403) and not (r.status_code == 303 and "gespeichert" in r.headers["location"])
    dbs.refresh(z)
    assert z.erledigt_at is None and z.email
    _faktor_an(client, dbs, _chef(dbs).id)
    r = client.post(pfad, data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 400
    r = client.post(pfad, data={"csrf": admin, "bestaetigt": "1"}, follow_redirects=False)
    assert r.status_code == 303
    dbs.refresh(z)
    assert z.erledigt_wie == "von_hand" and z.email is None
    assert client.post(pfad, data={"csrf": admin, "bestaetigt": "1"}).status_code == 404


def test_guthabenseite_und_kontoloeschung_nennen_erstattung(client, dbs):
    _modus(dbs)
    r = client.get("/konto-loeschen")
    assert "erstattet der Betreiber" in r.text
    _modus(dbs, "aus")
    assert "erstattet der Betreiber" not in client.get("/konto-loeschen").text
