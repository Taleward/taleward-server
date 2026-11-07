"""Server 0.4.154: Schnittstelle 0.4.31 – Namen einmal berichtigen (S62, Prüfkriterien des Sicherheits-Chats)."""
import json
from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from tests.test_step2a import API, neue_session


def _kapitel(client, w, dbs, text, title="Am Hungerstein", published=True):
    from app.models import GameSession, Recap

    s = neue_session(client, w)
    g = dbs.get(GameSession, s["id"])
    if published:
        g.state, g.published_at = "published", utcnow()
    else:
        g.state = "awaiting_review"
    dbs.add(Recap(session_id=s["id"], title=title, text=text, open_threads=json.dumps(["Wo ist Orasilas?"])))
    dbs.commit()
    return s


def _eintrag(dbs, w, name, summary="", visibility="public", gm_notes=None, aliases=None):
    from app.models import Entry

    e = Entry(campaign_id=w["cid"], type="npc", name=name, summary=summary, visibility=visibility, gm_notes=gm_notes,
              aliases_json=json.dumps(aliases) if aliases else None)
    dbs.add(e)
    dbs.commit()
    return e


def _vorschau(client, w, von="Orasilas", nach="Horasilas", h=None):
    return client.post(f"{API}/campaigns/{w['cid']}/name-fixes/preview", json={"from": von, "to": nach},
                       headers=h or w["gm"])


def _berichtigen(client, w, v, **mehr):
    return client.post(f"{API}/campaigns/{w['cid']}/name-fixes",
                       json={"from": v["from"], "to": v["to"], "previewVersion": v["previewVersion"], **mehr},
                       headers=w["gm"])


def test_vorschau_berichtigen_und_was_unberuehrt_bleibt(client, world, dbs):
    from app.models import (Bookmark, Comment, CommentRecipient, Entry, Member, PlayerProposal, Proposal, Recap,
                            TranscriptSegment)

    w = world
    s = _kapitel(client, w, dbs, "Dann trat Orasilas aus dem Schilf. Orasilas’ Schwur galt. Orasilasse nicht.")
    e = _eintrag(dbs, w, "Orasilas", "Der Elf Orasilas.", gm_notes="Orasilas lügt.", aliases=["Orasilas der Alte"])
    dbs.add(TranscriptSegment(session_id=s["id"], position=0, start=1.5, end=3.0, text="ich bin Orasilas"))
    p = Proposal(campaign_id=w["cid"], session_id=s["id"], entry_type="npc", action="create", title="Orasilas",
                 detail="Orasilas kam")
    dbs.add(p)
    pp = PlayerProposal(campaign_id=w["cid"], author_member_id=w["pl_member"], entry_type="npc", title="Orasilas",
                        detail="mag Orasilas")
    dbs.add(pp)
    m = dbs.get(Member, w["pl_member"])
    m.character_backstory = "Ich kenne Orasilas."
    dbs.add(Comment(session_id=s["id"], author_member_id=w["pl_member"], text="Orasilas!"))  # öffentlich
    privat = Comment(session_id=s["id"], author_member_id=w["pl_member"], recipient_member_id=w["pl_member"],
                     text="Orasilas geheim")  # an sich selbst: die SL sieht ihn nicht
    dbs.add(privat)
    dbs.flush()
    dbs.add(CommentRecipient(comment_id=privat.id, member_id=w["pl_member"]))
    dbs.add(Bookmark(session_id=s["id"], member_id=w["gm_member"], at=utcnow(), note="Orasilas"))
    dbs.commit()
    r = _vorschau(client, w)
    assert r.status_code == 200, r.text
    v = r.json()
    orte = {h["place"] for h in v["hits"]}
    assert orte == {"chapter", "entry_name", "entry_alias", "entry_summary", "entry_gm_notes", "session_proposal",
                    "transcript"}
    kap = next(h for h in v["hits"] if h["place"] == "chapter")
    assert kap["count"] == 3 and kap["readByPlayers"] and kap["preselected"]  # Text 2 + offener Faden 1
    assert "[[Orasilas]]" in kap["excerpts"][0]
    assert v["untouched"] == {"characters": [{"memberId": w["pl_member"], "field": "background"}], "comments": 1,
                              "playerProposals": 1}
    assert v["secretWarning"] is False and v["existingRule"] is None
    r = _berichtigen(client, w, v)
    assert r.status_code == 201, r.text
    fix = r.json()
    assert fix["rule"] is True and fix["changed"] == v["total"] and fix["undoUntil"]
    dbs.expire_all()
    rc = dbs.get(Recap, s["id"])
    assert rc.text == "Dann trat Horasilas aus dem Schilf. Horasilas’ Schwur galt. Orasilasse nicht."
    assert json.loads(rc.open_threads) == ["Wo ist Horasilas?"] and rc.edited_at is not None
    e = dbs.get(Entry, e.id)
    assert (e.name, e.summary, e.gm_notes, e.aliases) == ("Horasilas", "Der Elf Horasilas.", "Horasilas lügt.",
                                                          ["Horasilas der Alte"])
    assert e.visibility == "public"
    seg = dbs.scalar(select(TranscriptSegment).where(TranscriptSegment.session_id == s["id"]))
    assert seg.text == "ich bin Horasilas" and seg.start == 1.5
    assert dbs.get(Proposal, p.id).detail == "Horasilas kam"
    assert dbs.get(PlayerProposal, pp.id).detail == "mag Orasilas"  # Worte der Spielenden bleiben
    assert dbs.get(Member, w["pl_member"]).character_backstory == "Ich kenne Orasilas."
    assert dbs.scalar(select(Bookmark)).note == "Orasilas"
    # Liste nur für die SL, Spielende überall 404
    assert client.get(f"{API}/campaigns/{w['cid']}/name-fixes", headers=w["gm"]).json()["fixes"][0]["id"] == fix["id"]
    for antwort in (_vorschau(client, w, h=w["pl"]),
                    client.get(f"{API}/campaigns/{w['cid']}/name-fixes", headers=w["pl"]),
                    client.post(f"{API}/name-fixes/{fix['id']}/undo", headers=w["pl"]),
                    client.delete(f"{API}/name-fixes/{fix['id']}", headers=w["pl"]),
                    _vorschau(client, w, h=w["out"])):
        assert antwort.status_code == 404


def test_eingabe_und_sonderzeichen(client, world, dbs):
    w = world
    _kapitel(client, w, dbs, "Ein a.*+ steht hier, aber nicht abc.")
    assert _vorschau(client, w, "er", "sie").status_code == 400
    assert _vorschau(client, w, "Orasilas", "Orasilas").status_code == 400
    assert _vorschau(client, w, "Ora\nsilas", "Horasilas").status_code == 400
    assert _vorschau(client, w, "x" * 65, "Horasilas").status_code == 400
    v = _vorschau(client, w, "a.*+", "bcd").json()
    assert v["total"] == 1
    assert _vorschau(client, w, "Nichtda", "Sonstwas").json()["hits"] == []


def test_geheimer_name_warnt_und_waehlt_ab(client, world, dbs):
    w = world
    _kapitel(client, w, dbs, "Orasilas sprach.")
    _eintrag(dbs, w, "Horasilas", "Der wahre Name", visibility="gm_only")
    v = _vorschau(client, w).json()
    assert v["secretWarning"] is True
    assert [h["preselected"] for h in v["hits"]] == [False]


def test_veraltete_vorschau_und_unbekannte_treffer(client, world, dbs):
    from app.models import Recap

    w = world
    s = _kapitel(client, w, dbs, "Orasilas sprach.")
    v = _vorschau(client, w).json()
    assert _berichtigen(client, w, v, skipHitIds=["unbekannt"]).status_code == 400
    dbs.get(Recap, s["id"]).text = "Orasilas sprach lange."
    dbs.commit()
    r = _berichtigen(client, w, v)
    assert r.status_code == 409 and r.json()["code"] == "preview_stale"
    assert dbs.get(Recap, s["id"]).text == "Orasilas sprach lange."


def test_abgewaehlte_treffer_bleiben(client, world, dbs):
    from app.models import Entry, Recap

    w = world
    s = _kapitel(client, w, dbs, "Orasilas sprach.")
    e = _eintrag(dbs, w, "Orasilas")
    v = _vorschau(client, w).json()
    weg = [h["id"] for h in v["hits"] if h["place"] == "entry_name"]
    assert _berichtigen(client, w, v, skipHitIds=weg, rule=False).status_code == 201
    dbs.expire_all()
    assert dbs.get(Entry, e.id).name == "Orasilas" and dbs.get(Recap, s["id"]).text == "Horasilas sprach."


def test_rueckgaengig_ueberspringt_bearbeitetes(client, world, dbs):
    from app.models import Entry, NameFix, NameFixUndo, Recap

    w = world
    s = _kapitel(client, w, dbs, "Orasilas sprach.")
    e = _eintrag(dbs, w, "Orasilas")
    v = _vorschau(client, w).json()
    fix = _berichtigen(client, w, v).json()
    dbs.expire_all()
    dbs.get(Recap, s["id"]).text = "Horasilas sprach laut."
    dbs.commit()
    r = client.post(f"{API}/name-fixes/{fix['id']}/undo", headers=w["gm"])
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 1 and [x["place"] for x in r.json()["skipped"]] == ["chapter"]
    dbs.expire_all()
    assert dbs.get(Entry, e.id).name == "Orasilas" and dbs.get(Recap, s["id"]).text == "Horasilas sprach laut."
    assert dbs.get(NameFix, fix["id"]).rule is False and dbs.scalar(select(NameFixUndo)) is None
    r = client.post(f"{API}/name-fixes/{fix['id']}/undo", headers=w["gm"])
    assert r.status_code == 409 and r.json()["code"] == "already_undone"


def test_nach_24_stunden_keine_alten_texte(client, world, dbs):
    from app import namen_berichtigen as nb
    from app.models import NameFix, NameFixUndo

    w = world
    _kapitel(client, w, dbs, "Orasilas sprach.")
    fix = _berichtigen(client, w, _vorschau(client, w).json()).json()
    f = dbs.get(NameFix, fix["id"])
    f.created_at = utcnow() - timedelta(hours=25)
    f.undo_until = utcnow() - timedelta(hours=1)
    dbs.commit()
    assert nb.wartung(dbs) == 1
    dbs.commit()
    assert dbs.scalar(select(NameFixUndo)) is None
    r = client.post(f"{API}/name-fixes/{fix['id']}/undo", headers=w["gm"])
    assert r.status_code == 409 and r.json()["code"] == "undo_expired"
    assert client.get(f"{API}/campaigns/{w['cid']}/name-fixes", headers=w["gm"]).json()["fixes"][0]["undoUntil"] is None


def test_regel_schlaegt_vor_und_laesst_sich_loeschen(client, world, dbs):
    from app import namen_berichtigen as nb, unsicher
    from app.models import Campaign, GameSession, TranscriptSegment

    w = world
    _kapitel(client, w, dbs, "Orasilas sprach.")
    fix = _berichtigen(client, w, _vorschau(client, w).json()).json()
    c = dbs.get(Campaign, w["cid"])
    assert "Horasilas" in __import__("app.namenshilfe", fromlist=["x"]).anzeige(dbs, c)
    s2 = neue_session(client, w)
    g = dbs.get(GameSession, s2["id"])
    g.state = "awaiting_review"
    dbs.add(TranscriptSegment(session_id=g.id, position=0, start=0, end=1, text="da kommt Orasilas",
                              unsicher=json.dumps([{"word": "Orasilas", "score": 0.3}])))
    dbs.commit()
    t = [x for x in unsicher.begriffe(dbs, g) if x["heard"] == "Orasilas"]
    assert t and t[0]["suggestedCorrect"] == "Horasilas" and t[0]["suggestionSource"] == "rule"
    assert t[0]["ruleId"] == fix["id"]
    seg = dbs.scalar(select(TranscriptSegment).where(TranscriptSegment.session_id == g.id))
    assert seg.text == "da kommt Orasilas"  # nie still ersetzt
    assert client.delete(f"{API}/name-fixes/{fix['id']}", headers=w["gm"]).status_code == 204
    assert nb.regeln(dbs, c) == {}
    assert client.get(f"{API}/campaigns/{w['cid']}/name-fixes", headers=w["gm"]).json()["fixes"][0]["rule"] is False


def test_umzug_nur_regeln(client, world, dbs):
    from app import namen_berichtigen as nb
    from app.models import Campaign

    w = world
    _kapitel(client, w, dbs, "Orasilas sprach.")
    _berichtigen(client, w, _vorschau(client, w).json())
    c = dbs.get(Campaign, w["cid"])
    assert nb.fuer_umzug(dbs, c) == [{"from": "Orasilas", "to": "Horasilas"}]
    from app import umzug

    daten, _dateien = umzug.packliste(dbs, c, set())
    assert daten["campaign"]["nameRules"] == [{"from": "Orasilas", "to": "Horasilas"}]
    assert "Orasilas sprach" not in json.dumps(daten, ensure_ascii=False).split("sessions")[0]


def test_zu_viele_stellen(client, world, dbs, monkeypatch):
    from app import namen_berichtigen as nb

    w = world
    _kapitel(client, w, dbs, "Orasilas " * 5)
    monkeypatch.setattr(nb, "MAX_STELLEN", 4)
    r = _vorschau(client, w)
    assert r.status_code == 409 and r.json()["code"] == "too_many_hits"
