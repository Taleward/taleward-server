"""Server 0.4.90: Nachtrag zur Schnittstelle 0.4.18 (409 bei Unterlagen, feste Meldung bei Auswertungen) und die
Begriffe „Kapitel“, „Abschrift“ und „Runde“ in allen deutschen Texten für Nutzer."""
import ast
import re
from pathlib import Path

import yaml

from tests.test_charakterauswertung_0418 import _charakter, _schalten

API = "/api/v1"
WURZEL = Path(__file__).resolve().parent.parent
# Alte Wörter in deutschen Texten für Nutzer. Ausnahme: der Name des Schalters in der Worker-App, bis die ihn umbenennt.
ALT = re.compile(r"\b(Recaps?|Transkripte?s?n?|Sessions?|Upload)\b")
AUSNAHMEN = ("„Recaps auch auf diesem PC schreiben“",)


def _ohne_ausnahmen(text: str) -> str:
    for a in AUSNAHMEN:
        text = text.replace(a, "")
    return text


def test_yaml_nennt_409_bei_unterlagen():
    spec = yaml.safe_load((WURZEL / "contract" / "session-chronik-api.yaml").read_text(encoding="utf-8"))
    assert spec["info"]["version"] == "0.4.26"
    assert "409" in spec["paths"]["/campaigns/{campaignId}/documents"]["post"]["responses"]
    from tests.contract import BEKANNTE_LUECKEN
    assert not BEKANNTE_LUECKEN


def test_auswertung_meldet_nur_feste_saetze(client, world, dbs):
    from app.models import CharacterAnalysis
    from app.queue import status_message

    w = world
    _schalten(client, w)
    _charakter(client, w, w["pl"])
    a = client.post(f"{API}/campaigns/{w['cid']}/members/me/backstory-analysis", headers=w["pl"]).json()
    x = dbs.get(CharacterAnalysis, a["id"])

    def meldungen():
        dbs.commit()
        return [client.get(f"{API}/campaigns/{w['cid']}/character-analyses", headers=h).json()[0]["message"]
                for h in (w["pl"], w["gm"])]

    x.state, x.message = "failed", status_message("doc.no_text")  # fester Grund ohne Platzhalter: beide sehen ihn
    assert all("keinen auslesbaren Text" in m for m in meldungen())
    for roh in (status_message("doc.failed", detail="Modell sagt: Lirael ist die Tochter des Königs"),
                "Freitext ohne Schlüssel", "", None, '{"key": "not_found.recap"}'):
        x.message = roh
        spieler, sl = meldungen()
        assert "Lirael" not in spieler + sl and "Freitext" not in spieler + sl
        assert "noch einmal abgeben" in spieler and "Die Spielerin kann" in sl


def test_deutsche_texte_ohne_alte_begriffe():
    from app import errors
    from app.verwaltung import i18n

    alt = [de for de, _en in errors.MESSAGES.values() if ALT.search(_ohne_ausnahmen(de))]
    alt += [de for de in i18n.EN if ALT.search(_ohne_ausnahmen(de))]
    vorlagen = WURZEL / "app" / "verwaltung" / "templates"
    for datei in vorlagen.glob("*.html"):
        for text in re.findall(r"""_\((["'])(.*?)\1""", datei.read_text(encoding="utf-8")):
            if ALT.search(_ohne_ausnahmen(text[1])):
                alt.append(f"{datei.name}: {text[1]}")
    assert not alt, alt


def test_datenschutz_vorlage_und_testmodus_mit_neuen_begriffen(dbs):
    from app.datenschutz import vorlage

    text = vorlage(dbs)
    assert "„Kapitel“" in text and "Abschriften" in text and "Recap" not in text and "Transkripte" not in text
    quelle = (WURZEL / "app" / "zusammenfassung.py").read_text(encoding="utf-8")
    deutsch = [k.value for k in ast.walk(ast.parse(quelle)) if isinstance(k, ast.Constant) and isinstance(k.value, str)
               and k.value.startswith("Platzhalter")]
    assert deutsch and not [d for d in deutsch if ALT.search(d)]
