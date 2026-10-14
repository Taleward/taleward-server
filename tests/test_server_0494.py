"""Server 0.4.94: S32 (Platz freigeben gibt nichts Privates der vorigen Person weiter) und der Rest von M2 (alle Tonspuren
messen, Länge vor jedem Cloud-Aufruf prüfen, Nachberechnung ohne Kappung)."""
import uuid

import pytest
from sqlalchemy import select

from tests.test_abrechnung_0485 import _modus
from tests.test_charakterauswertung_0418 import _charakter

API = "/api/v1"


def _welt_geheim(client, w):
    r = client.post(f"{API}/campaigns/{w['cid']}/members/me/world", headers=w["pl"], json={"entries": [
        {"id": str(uuid.uuid4()), "version": 1, "type": "npc", "name": "Litha", "summary": "Schwester, lebt verborgen",
         "secret": True}]})
    assert r.status_code == 200, r.text
    [v] = [x for x in client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
           if x["title"] == "Litha"]
    assert client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"}).status_code == 200


# ---------------------------------------------------------------- S32
def test_s32_platz_freigeben_gibt_nichts_privates_weiter(client, world, dbs, make_user, login):
    from app.models import CampaignDocument, Comment, Entry, GameSession, Member

    w = world
    # zweite Spielerin, damit die geheime mitgebrachte Welt jemanden hat, vor dem sie geheim ist
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Ilva"}, headers=w["out"])
    _charakter(client, w, w["pl"], backstory="GEHEIMER-HINTERGRUND: Tharvok schuldet der Gilde Geld.")
    _welt_geheim(client, w)
    r = client.post(f"{API}/campaigns/{w['cid']}/documents", headers=w["pl"], data={"kind": "character_sheet"},
                    files={"file": ("bogen.txt", b"BOGEN-VON-BEN", "text/plain")})
    assert r.status_code == 201, r.text
    from tests.test_step2a import neue_session

    s = neue_session(client, w)
    dbs.add(Comment(session_id=s["id"], author_member_id=w["pl_member"], recipient_member_id=w["gm_member"],
                    text="PRIVAT: Ich bin Weihnachten nicht da."))
    dbs.commit()
    # SL gibt Bens Platz frei
    r = client.post(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}/release", headers=w["gm"])
    assert r.status_code == 200, r.text
    make_user("dora")
    dora = login("dora")
    code = client.post(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}/invite", headers=w["gm"]).json()["code"]
    r = client.post(f"{API}/campaigns/join", json={"code": code}, headers=dora)
    assert r.status_code == 200, r.text
    antwort = r.text
    assert "GEHEIMER-HINTERGRUND" not in antwort
    ich = next(m for m in r.json()["members"] if m["id"] == w["pl_member"])
    assert not ich.get("characterBackstory")
    # keine Bögen, keine Auswertungen, keine mitgebrachte Welt der vorigen Person
    docs = client.get(f"{API}/campaigns/{w['cid']}/documents", headers=dora)
    assert docs.status_code in (200, 404) and "bogen" not in docs.text
    assert client.get(f"{API}/campaigns/{w['cid']}/character-analyses", headers=dora).json() == []
    assert client.get(f"{API}/campaigns/{w['cid']}/members/me/world", headers=dora).json()["entries"] == []
    # Geheimes aus der mitgebrachten Welt sieht Dora nicht (sie ist nicht die Urheberin)
    namen = {e["name"] for e in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=dora).json()}
    assert "Litha" not in namen
    # Kommentare und Bogen hängen am „Ehemaligen Mitglied“
    dbs.expire_all()
    k = dbs.scalar(select(Comment).where(Comment.text.like("PRIVAT:%")))
    ehemalig = dbs.get(Member, k.author_member_id)
    assert ehemalig.id != w["pl_member"] and ehemalig.user_id is None and ehemalig.left_at is not None
    assert ehemalig.anzeigename in ("Ehemaliges Mitglied", "Former member")
    bogen = dbs.scalar(select(CampaignDocument).where(CampaignDocument.kind == "character_sheet"))
    assert bogen.uploaded_by_member_id == ehemalig.id
    litha = dbs.scalar(select(Entry).where(Entry.name == "Litha"))
    assert litha.origin_member_id == ehemalig.id and w["pl_member"] in litha.hidden_member_ids
    assert dbs.get(Member, w["pl_member"]).character_backstory is None
    # die SL sieht weiter alles
    namen_sl = {e["name"] for e in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"]).json()}
    assert "Litha" in namen_sl
    assert dbs.get(GameSession, s["id"]) is not None


# ---------------------------------------------------------------- M2-Rest
def test_m2_laengste_tonspur_zaehlt(tmp_path, monkeypatch):
    import subprocess

    from app import audio

    datei = tmp_path / "zwei.mka"
    datei.write_bytes(b"\0" * 1000)
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"0\n1\n", b""))
    spuren = {"0:a:0": 30, "0:a:1": 600}

    def ffmpeg(args, zeit, programm=None):
        spur = args[args.index("-map") + 1]
        return subprocess.CompletedProcess(args, 0, f"out_time_us={spuren[spur] * 1_000_000}\n".encode(), b"")

    monkeypatch.setattr(audio, "ffmpeg", ffmpeg)
    assert audio.dauer_dekodiert(datei) == 600  # Spur 1 = 30 s, Spur 2 = 600 s → 600 s
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, "".join(f"{i}\n" for i in range(audio.MAX_TONSPUREN + 1)).encode(), b""))
    assert audio.dauer_dekodiert(datei) is None  # unübliche Zahl an Spuren: nicht messbar


def test_m2_dekodieren_nimmt_die_erste_spur(tmp_path, monkeypatch):
    import subprocess

    from app import audio

    gesehen = []
    monkeypatch.setattr(audio, "ffmpeg", lambda args, zeit, programm=None: gesehen.append(args)
                        or subprocess.CompletedProcess(args, 0, b"\0\0" * 16, b""))
    (tmp_path / "a.ogg").write_bytes(b"x")
    audio.dekodieren(tmp_path / "a.ogg")
    assert gesehen and gesehen[0][gesehen[0].index("-map") + 1] == "0:a:0"


def test_m2_cloud_prueft_laenge_vor_dem_anbieter(dbs):
    from app import abrechnung, extern
    from app.audio import AudioFehler

    extern._laenge_pruefen(dbs, 10 * 3600)  # ohne Abrechnung: keine Grenze
    _modus(dbs)
    extern._laenge_pruefen(dbs, abrechnung.MAX_AUFNAHME_SEKUNDEN)
    with pytest.raises(AudioFehler) as e:
        extern._laenge_pruefen(dbs, abrechnung.MAX_AUFNAHME_SEKUNDEN + 1)
    assert e.value.code == "recording_too_long" and not e.value.retryable


def test_m2_nachberechnung_ohne_kappung(client, world, dbs):
    from app import abrechnung
    from tests.test_server_0489 import _kostenauftrag

    _modus(dbs)
    _, job, k = _kostenauftrag(dbs, weg="worker")
    abrechnung.menge_nachtragen(dbs, job, 30 * 3600.0)
    assert k.menge == 30 * 3600
