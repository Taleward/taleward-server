"""Kapitel teilen als verschlüsselte Datei (Schnittstelle 0.4.30).

Für Gruppen ohne Server im Internet: Die SL holt für ein veröffentlichtes Kapitel eine Datei und schickt sie über den
eigenen Gruppenchat. Jedes aktive Mitglied hat je Kampagne einen eigenen Schlüssel (nur für sich, über GET
/campaigns/{id}); damit öffnet die App die Datei. Der Server signiert jede Datei.

Regeln:
- Inhalt nur über die Positivliste `inhalt()`: veröffentlichtes Kapitel ohne Prüfteil, Entwurf und Hinweise,
  Kapitel-Kopf ohne Anwesende, Stimmen und Kommentarzahlen, Einträge nur `allgemein_bekannt` in der Spieler-Sicht.
  Nie Vorschläge, Kommentare, Hintergründe, Bögen, Abschrift, Stimmen, Hörproben, Zähler, Merkstellen, Bilder.
- Empfänger nur aktive Mitglieder mit gültigem Schlüssel (SL eingeschlossen), keine offenen Plätze, keine Ehemaligen.
- Der Mitgliedsschlüssel gehört der Person, nicht dem Platz: gebunden an Konto und Beitrittszeit, gelöscht bei Austritt,
  Entfernen, Platz freigeben, Wiedereintritt, Konto löschen. Nie in Listen, Umzug, Export oder Protokollen.
- Der Signierschlüssel des Servers liegt in server_meta (geheim: unter Windows mit dem Windows-Datenschutz), ist damit
  in der Sicherung und nie im Umzug.
- Der Server speichert die Datei nicht.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

FORMAT = "taleward-kapitel/1"
META_SCHLUESSEL = "teilen.signierschluessel"  # PEM (PKCS#8), geheim
META_ID = "teilen.schluessel_id"
JE_STUNDE = 30  # Aufrufe je Kampagne und Stunde


# ---------------------------------------------------------------- Hilfen
def b64u(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode("ascii")


def b64u_lesen(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _json(obj) -> bytes:
    """Kompakt, ohne Leerzeichen, Schlüssel in der angegebenen Reihenfolge (nicht sortiert)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def der_zu_p1363(der: bytes) -> bytes:
    """Python liefert ECDSA-Signaturen als DER, WebCrypto erwartet r‖s (IEEE P1363, je 32 Byte)."""
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def p1363_zu_der(roh: bytes) -> bytes:
    if len(roh) != 64:
        raise ValueError("Signatur muss 64 Byte haben")
    return encode_dss_signature(int.from_bytes(roh[:32], "big"), int.from_bytes(roh[32:], "big"))


# ---------------------------------------------------------------- Signierschlüssel des Servers
@dataclass
class Signierer:
    id: str
    privat: ec.EllipticCurvePrivateKey

    @property
    def spki(self) -> bytes:
        return self.privat.public_key().public_bytes(serialization.Encoding.DER,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo)

    def signieren(self, daten: bytes) -> bytes:
        return der_zu_p1363(self.privat.sign(daten, ec.ECDSA(hashes.SHA256())))


def _id_aus(spki: bytes) -> str:
    return b64u(hashlib.sha256(spki).digest()[:12])


def signierer(db: Session) -> Signierer:
    """Den Schlüssel holen, beim ersten Mal anlegen. Lässt er sich nicht öffnen (Sicherung von einem anderen
    Windows-PC), entsteht ein neuer – mit neuer Kennung; ältere Dateien prüft die App dann gegen den alten nicht mehr,
    neue Dateien gehen sofort."""
    from app.einstellungen import meta_lesen, meta_schreiben

    pem = meta_lesen(db, META_SCHLUESSEL)
    if pem:
        try:
            privat = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
            if isinstance(privat, ec.EllipticCurvePrivateKey) and privat.curve.name == "secp256r1":
                s = Signierer("", privat)
                s.id = meta_lesen(db, META_ID) or _id_aus(s.spki)
                return s
        except (ValueError, TypeError):
            pass
    privat = ec.generate_private_key(ec.SECP256R1())
    s = Signierer("", privat)
    s.id = _id_aus(s.spki)
    meta_schreiben(db, META_SCHLUESSEL, privat.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii"))
    meta_schreiben(db, META_ID, s.id)
    db.flush()
    return s


def share_keys(db: Session) -> list[dict]:
    """Für ServerInfo.shareKeys. Legt den Schlüssel bei Bedarf an (und speichert ihn)."""
    s = signierer(db)
    db.commit()
    return [{"id": s.id, "spki": b64u(s.spki)}]


# ---------------------------------------------------------------- Mitgliedsschlüssel
def _bindung(m) -> str:
    beitritt = m.joined_at.replace(tzinfo=None, microsecond=0).isoformat() if m.joined_at else ""
    return f"{m.user_id}|{beitritt}"


def _empfaenger_moeglich(m) -> bool:
    return m.aktiv and not m.open_seat and m.deleted_at is None


def gueltiger(db: Session, m):
    """Gespeicherter Schlüssel des Mitglieds, wenn er noch zur selben Person und demselben Beitritt gehört."""
    from app.models import ShareKey

    k = db.get(ShareKey, m.id)
    if k is None:
        return None
    if not _empfaenger_moeglich(m) or k.bindung != _bindung(m):
        db.delete(k)  # gehört zu einer früheren Mitgliedschaft
        db.flush()
        return None
    return k


def fuer_mich(db: Session, me) -> dict | None:
    """Schlüssel für genau dieses Mitglied (GET /campaigns/{id}); legt ihn beim ersten Laden an."""
    from app.models import ShareKey

    if not _empfaenger_moeglich(me):
        return None
    k = gueltiger(db, me)
    if k is None:
        k = ShareKey(member_id=me.id, key_id=secrets.token_urlsafe(12), key=b64u(os.urandom(32)), bindung=_bindung(me))
        db.add(k)
        db.flush()
    return {"id": k.key_id, "key": k.key}


def vergessen(db: Session, member_id: str) -> None:
    """Austritt, Entfernen, Platz freigeben, Wiedereintritt, Konto löschen: alter Schlüssel weg."""
    from app.models import ShareKey

    db.execute(ShareKey.__table__.delete().where(ShareKey.member_id == member_id))


# ---------------------------------------------------------------- Inhalt (Positivliste)
def _zeit(t: datetime | None) -> str | None:
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def inhalt(db: Session, c, s) -> dict:
    """ShareContent: nur, was ein gerade beigetretenes Mitglied sieht."""
    from sqlalchemy.orm import selectinload

    from app.models import Entry, EntryMention, Recap
    from app.routers.pruefung import recap_out
    from app.services import entry_out

    r = db.get(Recap, s.id)
    recap = recap_out(s, r, sl=False).model_dump(mode="json", by_alias=True, exclude_unset=True)
    for feld in ("review", "revision", "gmWarnings"):  # doppelt gesichert
        recap.pop(feld, None)
    sitzung = {"id": s.id, "number": s.number, "title": s.title, "playedAt": _zeit(s.played_at), "state": s.state}
    eintraege = db.scalars(select(Entry).where(Entry.campaign_id == c.id, Entry.visibility == "public")
                           .options(selectinload(Entry.mentions).selectinload(EntryMention.session)))
    liste = []
    for e in sorted(eintraege, key=lambda e: e.name.casefold()):
        if not e.allgemein_bekannt:
            continue
        d = entry_out(e, False, None).model_dump(mode="json", by_alias=True, exclude_unset=True)
        for feld in ("gmNotes", "hiddenFromMemberIds", "formerHolderMemberId", "originCharacterId", "originEntryId",
                     "originVersion"):
            d.pop(feld, None)
        liste.append(d)
    return {"campaignTitle": c.title, "sessions": [{"session": sitzung, "recap": recap}], "entries": liste}


# ---------------------------------------------------------------- Datei
@dataclass
class Zufall:
    """Für den Testvektor austauschbar; im Betrieb immer frisch."""

    def bytes(self, n: int) -> bytes:
        return os.urandom(n)

    def share_id(self) -> str:
        return secrets.token_urlsafe(16)


def kopf(server_id: str, campaign_id: str, share_id: str, created_at: str) -> bytes:
    return _json({"format": FORMAT, "serverId": server_id, "campaignId": campaign_id, "shareId": share_id,
                  "createdAt": created_at})


def bauen(sign: Signierer, server_id: str, campaign_id: str, created_at: str, daten: dict,
          empfaenger: list[tuple[str, bytes]], zufall: Zufall | None = None) -> str:
    """Datei nach ShareFile als Text. empfaenger: (keyId, 32-Byte-Schlüssel)."""
    zufall = zufall or Zufall()
    share_id = zufall.share_id()
    k = kopf(server_id, campaign_id, share_id, created_at)
    inhaltsschluessel = zufall.bytes(32)
    nonce = zufall.bytes(12)
    gepackt = gzip.compress(_json(daten), mtime=0)
    chiffrat = AESGCM(inhaltsschluessel).encrypt(nonce, gepackt, k)
    liste = []
    for key_id, schluessel in empfaenger:
        n = zufall.bytes(12)
        liste.append({"keyId": key_id, "nonce": b64u(n),
                      "wrapped": b64u(AESGCM(schluessel).encrypt(n, inhaltsschluessel, k + key_id.encode("utf-8")))})
    signiert = _json({"serverId": server_id, "campaignId": campaign_id, "shareId": share_id, "createdAt": created_at,
                      "signKeyId": sign.id, "recipients": liste, "nonce": b64u(nonce), "ciphertext": b64u(chiffrat)})
    return json.dumps({"format": FORMAT, "signed": b64u(signiert), "signature": b64u(sign.signieren(signiert))},
                      separators=(",", ":"))


def oeffnen(datei: str, spki: bytes, key_id: str, schluessel: bytes) -> dict:
    """Gegenstück wie in der App (für Tests): Signatur zuerst, dann entschlüsseln. Wirft ValueError."""
    from cryptography.exceptions import InvalidSignature, InvalidTag

    if len(datei.encode("utf-8")) > 5 * 1024 * 1024:
        raise ValueError("zu groß")
    d = json.loads(datei)
    if d.get("format") != FORMAT:
        raise ValueError("format")
    signiert = b64u_lesen(d["signed"])
    oeffentlich = serialization.load_der_public_key(spki)
    try:
        oeffentlich.verify(p1363_zu_der(b64u_lesen(d["signature"])), signiert, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise ValueError("signatur") from None
    s = json.loads(signiert)
    k = kopf(s["serverId"], s["campaignId"], s["shareId"], s["createdAt"])
    eintrag = next((r for r in s["recipients"] if r["keyId"] == key_id), None)
    if eintrag is None:
        raise ValueError("nicht für dich")
    try:
        cek = AESGCM(schluessel).decrypt(b64u_lesen(eintrag["nonce"]), b64u_lesen(eintrag["wrapped"]),
                                         k + key_id.encode("utf-8"))
        gepackt = AESGCM(cek).decrypt(b64u_lesen(s["nonce"]), b64u_lesen(s["ciphertext"]), k)
    except InvalidTag:
        raise ValueError("entschlüsseln") from None
    with gzip.GzipFile(fileobj=__import__("io").BytesIO(gepackt)) as g:
        roh = g.read(20 * 1024 * 1024 + 1)
    if len(roh) > 20 * 1024 * 1024:
        raise ValueError("entpackt zu groß")
    return json.loads(roh)


def teilen(db: Session, c, s) -> dict:
    """POST /campaigns/{id}/shares (Rechte und Zustand prüft der Router)."""
    from app.db import server_id, utcnow
    from app.models import Member

    sign = signierer(db)
    empfaenger, fehlen = [], []
    mitglieder = db.scalars(select(Member).where(Member.campaign_id == c.id))
    for m in sorted(mitglieder, key=lambda m: (m.role != "gm", m.joined_at or utcnow())):
        if not _empfaenger_moeglich(m):
            continue
        k = gueltiger(db, m)
        if k is None:
            fehlen.append(m.id)
        else:
            empfaenger.append((k.key_id, b64u_lesen(k.key)))
    jetzt = _zeit(utcnow().replace(microsecond=0))
    datei = bauen(sign, server_id(), c.id, jetzt, inhalt(db, c, s), empfaenger)
    return {"file": datei, "fileName": f"taleward-kapitel-{s.number}.taleward", "missingKeys": fehlen}
