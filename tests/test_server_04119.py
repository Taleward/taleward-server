"""Server 0.4.119: Anthropic mit aktuellen Modellen (Sonnet/Opus/Haiku 5.5) als Voreinstellung und mit Preisen, damit
Verbrauchsanzeige und Kostenlimit auch bei Claude stimmen."""


def test_anthropic_aktuell():
    from app import cloudanbieter
    from app.sprachmodell import PREISE

    a = next(x for x in cloudanbieter.ANBIETER if x.id == "anthropic")
    assert a.modell == "claude-sonnet-5-5" and a.modell in a.modelle
    for m in a.modelle:
        ein, aus = PREISE[m]
        assert 0 < ein < aus


def test_s46_nur_drei_bildformate():
    """Pillow startet nur die Decoder für JPEG, PNG und WEBP – eine TIFF- oder GIF-Datei mit falschem Typ wird
    abgelehnt, ohne dass ihr Decoder läuft."""
    import io

    import pytest
    from PIL import GifImagePlugin, Image, TiffImagePlugin

    from app import bilder, errors

    for fmt in ("TIFF", "GIF"):
        roh = io.BytesIO()
        Image.new("RGB", (4, 4)).save(roh, fmt)
        gesehen = []
        alt_tiff, alt_gif = TiffImagePlugin.TiffImageFile._open, GifImagePlugin.GifImageFile._open
        TiffImagePlugin.TiffImageFile._open = lambda self: gesehen.append("tiff") or alt_tiff(self)
        GifImagePlugin.GifImageFile._open = lambda self: gesehen.append("gif") or alt_gif(self)
        try:
            with pytest.raises(errors.ApiError) as e:
                bilder.laden(roh.getvalue(), "image/png", 10 ** 7)
        finally:
            TiffImagePlugin.TiffImageFile._open, GifImagePlugin.GifImageFile._open = alt_tiff, alt_gif
        assert e.value.code == "unsupported_image" and gesehen == []


def test_s47_messwerte_riesige_zahl_und_steuerzeichen():
    from app import messwerte

    m = messwerte.bereinigen({"rechenzeitMin": 10 ** 400, "einlesenTokS": 12.5, "modell": "gemma" + chr(10) + "4" + chr(0x202E),
                              "stufen": {"erkennung": 10 ** 400, "einlesen": 3}})
    assert "rechenzeitMin" not in m and m["einlesenTokS"] == 12.5
    assert chr(10) not in m["modell"] and chr(0x202E) not in m["modell"]
    assert m["stufen"] == {"einlesen": 3.0}


def test_s47_hoerproben_altdaten_werden_geloescht(client, world, dbs, tmp_path):
    from datetime import timedelta

    from sqlalchemy import update

    from app import aufbewahrung, storage
    from app.db import utcnow
    from app.models import GameSession, Upload
    from tests.test_step2a import neue_session

    s = neue_session(client, world)
    alt = storage.sample_path(s["id"], "sprecher", 2)  # Altdatei ohne sample_path in der Datenbank
    storage.write_atomic(alt, b"OggS")
    dbs.execute(update(Upload).where(Upload.session_id == s["id"]).values(completed_at=None))
    dbs.get(GameSession, s["id"]).created_at = utcnow() - timedelta(days=30)
    dbs.commit()
    aufbewahrung.hoerproben_loeschen(dbs, utcnow() - timedelta(days=7))
    assert not alt.exists()
    verwaist = storage.sample_path("gibt-es-nicht", "x")  # Runde gelöscht, Ordner geblieben
    storage.write_atomic(verwaist, b"OggS")
    aufbewahrung.hoerproben_loeschen(dbs, utcnow() - timedelta(days=7))
    assert not verwaist.exists()


def test_s41_6_datenschutz_link_nur_geprueft(client, dbs):
    from app import links
    from app.einstellungen import meta_schreiben

    assert not links.adresse_ok("https://example.org/" + chr(0x202E) + "x")
    assert not links.adresse_ok("https://example.org/" + chr(0x85))
    assert links.datenschutz_link("javascript:alert(1)") == ""
    assert links.datenschutz_link("http://192.168.1.2:8000/datenschutz") == "http://192.168.1.2:8000/datenschutz"
    meta_schreiben(dbs, "angabe.privacy_policy_url", "javascript:alert(1)")
    dbs.commit()
    seite = client.get("/konto-loeschen").text
    assert "javascript:" not in seite


def test_s41_11a_umzug_bereinigt_namen():
    from app import aliasse

    assert aliasse.zeichen_bereinigen("Ra" + chr(0x202E) + "be" + chr(10)) == "Ra be"
    quelle = open("app/umzug.py", encoding="utf-8").read()
    assert "name=aliasse.zeichen_bereinigen(e.name)[:300]" in quelle
