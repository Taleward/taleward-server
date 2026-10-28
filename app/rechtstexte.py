"""Bedingungen für Guthaben und Widerrufsbelehrung (0.4.138) – unter /guthaben-bedingungen und /widerruf.

Das sind VORLAGEN und keine Rechtsberatung. Sie werden aus den Angaben des Betreibers und den Einstellungen der
Abrechnung befüllt; was nur der Betreiber wissen kann, steht in [eckigen Klammern]. Er liest, passt an und
veröffentlicht in der Verwaltung (Abrechnung → Bedingungen und Widerruf), immer Deutsch und Englisch zusammen.
Veröffentlichen geht erst, wenn keine eckige Klammer mehr offen ist.

Die Widerrufsbelehrung folgt dem gesetzlichen Muster für Dienstleistungen und siezt deshalb.

Gespeichert in den Server-Einstellungen: `rechtstext.<art>.de`, `rechtstext.<art>.en` und `rechtstext.<art>.stand`
(Datum der letzten inhaltlichen Änderung, JJJJ-MM-TT). Beim Aufladen stimmt die Spielleitung dem sofortigen Beginn
zu; am Bezahlvorgang stehen Zeitpunkt und Fassung (`fassung()`, die beiden Stand-Daten).

Format wie beim Datenschutzhinweis: Absätze durch Leerzeilen, Überschriften mit „## “, Aufzählungen mit „- “.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.einstellungen import meta_lesen, meta_schreiben

ARTEN = ("bedingungen", "widerruf")
SPRACHEN = ("de", "en")
PFADE = {"bedingungen": "/guthaben-bedingungen", "widerruf": "/widerruf"}
MAX_TEXT = 40000
MIN_TEXT = 200


def _schluessel(art: str, teil: str) -> str:
    if art not in ARTEN:
        raise KeyError(art)
    return f"rechtstext.{art}.{teil}"


def text(db: Session, art: str, sprache: str) -> str:
    """Veröffentlichter Text (leer, wenn nicht veröffentlicht)."""
    return (meta_lesen(db, _schluessel(art, sprache if sprache in SPRACHEN else "de")) or "").strip()


def stand(db: Session, art: str) -> str | None:
    """Datum der veröffentlichten Fassung (JJJJ-MM-TT) oder None."""
    return (meta_lesen(db, _schluessel(art, "stand")) or "").strip() or None


def veroeffentlicht(db: Session, art: str | None = None) -> bool:
    """Ist die Art (bzw. sind beide) in beiden Sprachen veröffentlicht?"""
    arten = (art,) if art else ARTEN
    return all(stand(db, a) and all(text(db, a, s) for s in SPRACHEN) for a in arten)


def fassung(db: Session) -> str | None:
    """Fassung für die Zustimmung beim Aufladen: die Stand-Daten beider Texte, sonst None."""
    if not veroeffentlicht(db):
        return None
    return f"bedingungen {stand(db, 'bedingungen')}, widerruf {stand(db, 'widerruf')}"


def offene_stellen(inhalt: str) -> int:
    return inhalt.count("[")


def speichern(db: Session, art: str, de: str, en: str) -> bool:
    """Beide Sprachen veröffentlichen. Das Stand-Datum ändert sich nur, wenn sich der Inhalt ändert. Gibt zurück, ob
    sich etwas geändert hat. Prüfen (Länge, Klammern) macht der Aufrufer."""
    from app.db import utcnow

    neu = {"de": _bereinigen(de)[:MAX_TEXT], "en": _bereinigen(en)[:MAX_TEXT]}
    if all(text(db, art, s) == neu[s] for s in SPRACHEN) and stand(db, art):
        return False
    for s in SPRACHEN:
        meta_schreiben(db, _schluessel(art, s), neu[s])
    meta_schreiben(db, _schluessel(art, "stand"), utcnow().date().isoformat())
    return True


def zurueckziehen(db: Session, art: str) -> None:
    for teil in (*SPRACHEN, "stand"):
        meta_schreiben(db, _schluessel(art, teil), "")


def _bereinigen(t: str) -> str:
    return "\n".join(z.rstrip() for z in (t or "").replace("\r\n", "\n").strip().split("\n"))


def stand_anzeige(datum: str | None, sprache: str) -> str:
    if not datum:
        return ""
    j, m, t = datum.split("-")
    return f"Last updated: {datum}" if sprache == "en" else f"Stand: {t}.{m}.{j}"


# ---------------------------------------------------------------- Vorlagen
def _angaben(db: Session, en: bool) -> dict:
    from app import abrechnung, betreiber, zahlung

    b = betreiber.lesen(db)
    p = abrechnung.preise(db)
    wege = zahlung.bereit(db) or list(zahlung.ANBIETER)
    kleinster = min(zahlung.betraege(db))
    weg = wege[0]
    anschrift = ", ".join(z for z in (b.anschrift or "").split("\n") if z)
    if en:
        zahl = {"stripe": "Stripe (card, Apple Pay, Google Pay, depending on the country also SEPA direct debit)",
                "paypal": "PayPal"}
        euro = _euro_en
    else:
        zahl = {"stripe": "Stripe (Karte, Apple Pay, Google Pay, je nach Land auch SEPA-Lastschrift)", "paypal": "PayPal"}
        euro = abrechnung.euro
    return {
        "name": b.name or ("[Name of the operator]" if en else "[Name des Betreibers]"),
        "anschrift": anschrift or ("[Postal address]" if en else "[Anschrift]"),
        "kontakt": b.kontakt or ("[E-mail address]" if en else "[E-Mail-Adresse]"),
        "zahlwege": (" or " if en else " oder ").join(zahl[w] for w in wege),
        "kleinster": euro(kleinster),
        "beispiel_brutto": euro(zahlung.brutto(db, weg, kleinster)),
        "freigabe": euro(p.freigabe_standard),
        "minus": euro(-p.deckel),
    }


def _euro_en(cent: int) -> str:
    vorzeichen = "−" if cent < 0 else ""
    cent = abs(int(cent))
    return f"{vorzeichen}{cent // 100}.{cent % 100:02d} €"


def vorlage(db: Session, art: str, sprache: str = "de") -> str:
    en = sprache == "en"
    a = _angaben(db, en)
    if art == "bedingungen":
        return _bedingungen_en(a) if en else _bedingungen_de(a)
    if art == "widerruf":
        return _widerruf_en(a) if en else _widerruf_de(a)
    raise KeyError(art)


def text_oder_vorlage(db: Session, art: str, sprache: str) -> str:
    return text(db, art, sprache) or vorlage(db, art, sprache)


def _bedingungen_de(a: dict) -> str:
    return "\n\n".join([
        "# Bedingungen für Guthaben",
        "## Wer bietet das an",
        f"Diesen Server betreibt {a['name']}, {a['anschrift']} (siehe Impressum). Kontakt für Fragen zu Guthaben und "
        f"Zahlungen: {a['kontakt']}.",
        "## Wofür das Guthaben ist",
        "Auf diesem Server kostet die Verarbeitung von Runden und Unterlagen Geld (Abschrift, Kapitel, Auswertung). "
        "Diese Kosten trägt die Spielleitung, die eine Kampagne bezahlt – nie die Spielerinnen und Spieler. Dafür lädst "
        "du als Spielleitung vorab Guthaben auf. Es ist kein Abo und verlängert sich nicht.",
        "## Was eine Verarbeitung kostet",
        "Jede Verarbeitung kostet das, was sie tatsächlich verursacht (Cloud-Anbieter bzw. Rechenarbeit), plus einen "
        "Aufschlag für den Betrieb des Servers. Aufschlag und Preise legt der Betreiber in der Verwaltung fest; die "
        "Guthaben-Seite zeigt sie dir. Vor jeder Verarbeitung siehst du eine Schätzung. Liegt sie über deinem "
        f"Freigabe-Betrag (voreingestellt {a['freigabe']}, von dir änderbar), wartet die Verarbeitung, bis du sie "
        "freigibst. Abgerechnet wird nach Ende der Verarbeitung mit den tatsächlichen Kosten; sie können von der "
        "Schätzung abweichen.",
        "Alle Beträge sind Endbeträge in Euro. [Umsatzsteuer – einen der beiden Sätze stehen lassen: „Gemäß § 19 UStG "
        "wird keine Umsatzsteuer berechnet.“ oder „Alle Beträge enthalten die gesetzliche Umsatzsteuer.“]",
        "## Aufladen",
        f"Du lädst auf der Guthaben-Seite auf, mit festen Beträgen (mindestens {a['kleinster']}). Bezahlt wird über "
        f"{a['zahlwege']} auf deren Seiten; Kartendaten sieht dieser Server nie. Die Gebühr des Zahlungsanbieters wird "
        f"aufgeschlagen und vorher angezeigt („Du zahlst {a['beispiel_brutto']}, gutgeschrieben werden "
        f"{a['kleinster']}“). Das Guthaben steht bereit, sobald der Anbieter die Zahlung bestätigt.",
        "## Verbrauch",
        "- Geschenktes Guthaben (Gutschrift, Gutschein) wird zuerst verbraucht, danach bezahltes.\n"
        "- Eine Verarbeitung startet nur bei positivem Guthaben. Eine begonnene Verarbeitung läuft zu Ende, auch wenn "
        f"das Guthaben dabei ins Minus geht (höchstens bis {a['minus']}). Ein Minus wird beim nächsten Aufladen "
        "verrechnet; bis dahin startet keine neue Verarbeitung.\n"
        "- Guthaben gehört zu deinem Konto auf diesem Server. Es geht nicht mit, wenn du eine Kampagne abgibst oder auf "
        "einen anderen Server umziehst.\n"
        "- Guthaben verfällt nicht.",
        "## Gutschriften und Gutscheine",
        "Gutschriften und Gutscheine sind Geschenke. Sie werden nicht ausgezahlt und nicht erstattet.",
        "## Erstattung von Restguthaben",
        "Bezahltes Guthaben, das du noch nicht verbraucht hast, bekommst du auf Anfrage voll zurück, ohne Abzug. Schreib "
        f"dazu an {a['kontakt']}. Wir erstatten auf die ursprünglichen Zahlungen, die neueste zuerst. Geht das beim "
        "Anbieter nicht mehr, fragen wir per Mail nach einer Bankverbindung und überweisen. Mit der Erstattung ist das "
        "Guthaben entsprechend weg.",
        "## Konto löschen",
        "Du kannst dein Konto jederzeit löschen. Bleibt bezahltes Guthaben übrig, erstatten wir es wie oben. Dafür "
        "behalten wir nur den Betrag, die Zahlungsbezüge und deine E-Mail-Adresse, bis erstattet ist, höchstens [90] "
        "Tage. Ein Minus verfällt beim Löschen.",
        "## Widerruf",
        "Du hast ein Widerrufsrecht, siehe Widerrufsbelehrung (/widerruf auf diesem Server). Beim Aufladen bestätigst "
        "du, dass das Guthaben sofort nutzbar sein soll. Widerrufst du innerhalb von 14 Tagen, bekommst du den nicht "
        "verbrauchten Teil der Aufladung zurück.",
        "## Rückbuchung und Streitfall",
        "Wird eine Zahlung beim Anbieter zurückgebucht oder angefochten, buchen wir den Betrag zurück und halten neue "
        "Verarbeitungen an, bis die Sache geklärt ist. Schreib uns gern vorher – meist lässt sich das einfacher lösen.",
        "## Änderungen",
        "Preise und Aufschlag können sich ändern. Sie gelten nur für Verarbeitungen, die danach beginnen; bereits "
        "aufgeladenes Guthaben behält seinen Euro-Wert. Änderungen dieser Bedingungen kündigen wir auf der "
        "Guthaben-Seite und per Mail an Spielleitungen mit hinterlegter Adresse mindestens [4 Wochen] vorher an.",
        "## Streitbeilegung",
        "Wir sind nicht verpflichtet und nicht bereit, an einem Streitbeilegungsverfahren vor einer "
        "Verbraucherschlichtungsstelle teilzunehmen. Schreib uns einfach.",
    ])


def _bedingungen_en(a: dict) -> str:
    return "\n\n".join([
        "# Credit terms",
        "## Who offers this",
        f"This server is run by {a['name']}, {a['anschrift']} (see legal notice). Contact for questions about credit "
        f"and payments: {a['kontakt']}.",
        "## What the credit is for",
        "On this server, processing sessions and documents costs money (transcript, chapter, analysis). The game master "
        "who pays for a campaign covers these costs – never the players. As a game master you top up credit in "
        "advance. It is not a subscription and does not renew.",
        "## What processing costs",
        "Each processing step costs what it actually causes (cloud provider or computing work), plus a surcharge for "
        "running the server. The operator sets surcharge and prices in the server administration; the credit page "
        "shows them to you. Before each processing step you see an estimate. If it is above your approval amount "
        f"({a['freigabe']} by default, you can change it), processing waits until you approve it. You are charged the "
        "actual costs once processing has finished; they may differ from the estimate.",
        "All amounts are final amounts in euros. [VAT – keep one of the two sentences: “No VAT is charged under "
        "section 19 of the German VAT Act (small business scheme).” or “All amounts include statutory VAT.”]",
        "## Topping up",
        f"You top up on the credit page in fixed amounts (at least {a['kleinster']}). Payment is handled by "
        f"{a['zahlwege']} on their own pages; this server never sees card details. The payment provider's fee is added "
        f"and shown beforehand (“You pay {a['beispiel_brutto']}, {a['kleinster']} will be credited”). The credit is "
        "available as soon as the provider confirms the payment.",
        "## Usage",
        "- Gifted credit (manual credit, voucher) is used first, then paid credit.\n"
        "- Processing only starts with a positive balance. Processing that has started runs to the end, even if the "
        f"balance goes negative (down to {a['minus']} at most). A negative balance is settled with your next top-up; "
        "until then no new processing starts.\n"
        "- Credit belongs to your account on this server. It does not move with a campaign you hand over or move to "
        "another server.\n"
        "- Credit does not expire.",
        "## Manual credits and vouchers",
        "Manual credits and vouchers are gifts. They are not paid out or refunded.",
        "## Refund of remaining credit",
        "Paid credit you have not used yet is refunded in full on request, without deductions. Write to "
        f"{a['kontakt']}. We refund to the original payments, newest first. If the provider no longer allows that, we "
        "ask for bank details by email and transfer the amount. The refunded amount is removed from your credit.",
        "## Deleting your account",
        "You can delete your account at any time. If paid credit remains, we refund it as above. For that we keep only "
        "the amount, the payment references and your email address until the refund is done, for [90] days at most. A "
        "negative balance is waived when you delete your account.",
        "## Withdrawal",
        "You have a right of withdrawal, see the withdrawal notice (/widerruf on this server). When topping up you "
        "confirm that the credit should be usable right away. If you withdraw within 14 days, you get back the unused "
        "part of the top-up.",
        "## Chargebacks and disputes",
        "If a payment is reversed or disputed with the provider, we reverse the amount and pause new processing until "
        "the matter is settled. Feel free to write to us first – it is usually easier to sort out.",
        "## Changes",
        "Prices and surcharge may change. They only apply to processing that starts afterwards; credit already topped "
        "up keeps its euro value. We announce changes to these terms on the credit page and by email to game masters "
        "with a stored address at least [4 weeks] in advance.",
        "## Dispute resolution",
        "We are neither obliged nor willing to take part in dispute resolution proceedings before a consumer "
        "arbitration board. Just write to us.",
    ])


def _widerruf_de(a: dict) -> str:
    wer = f"{a['name']}, {a['anschrift']}, {a['kontakt']}"
    return "\n\n".join([
        "# Widerrufsbelehrung",
        "## Widerrufsrecht",
        "Sie haben das Recht, binnen vierzehn Tagen ohne Angabe von Gründen diesen Vertrag zu widerrufen.",
        "Die Widerrufsfrist beträgt vierzehn Tage ab dem Tag des Vertragsabschlusses.",
        f"Um Ihr Widerrufsrecht auszuüben, müssen Sie uns ({wer}) mittels einer eindeutigen Erklärung (z. B. ein mit "
        "der Post versandter Brief oder E-Mail) über Ihren Entschluss, diesen Vertrag zu widerrufen, informieren. Sie "
        "können dafür das beigefügte Muster-Widerrufsformular verwenden, das jedoch nicht vorgeschrieben ist.",
        "Zur Wahrung der Widerrufsfrist reicht es aus, dass Sie die Mitteilung über die Ausübung des Widerrufsrechts vor "
        "Ablauf der Widerrufsfrist absenden.",
        "## Folgen des Widerrufs",
        "Wenn Sie diesen Vertrag widerrufen, haben wir Ihnen alle Zahlungen, die wir von Ihnen erhalten haben, "
        "einschließlich der Lieferkosten (mit Ausnahme der zusätzlichen Kosten, die sich daraus ergeben, dass Sie eine "
        "andere Art der Lieferung als die von uns angebotene, günstigste Standardlieferung gewählt haben), unverzüglich "
        "und spätestens binnen vierzehn Tagen ab dem Tag zurückzuzahlen, an dem die Mitteilung über Ihren Widerruf "
        "dieses Vertrags bei uns eingegangen ist. Für diese Rückzahlung verwenden wir dasselbe Zahlungsmittel, das Sie "
        "bei der ursprünglichen Transaktion eingesetzt haben, es sei denn, mit Ihnen wurde ausdrücklich etwas anderes "
        "vereinbart; in keinem Fall werden Ihnen wegen dieser Rückzahlung Entgelte berechnet.",
        "Haben Sie verlangt, dass die Dienstleistungen während der Widerrufsfrist beginnen sollen, so haben Sie uns "
        "einen angemessenen Betrag zu zahlen, der dem Anteil der bis zu dem Zeitpunkt, zu dem Sie uns von der Ausübung "
        "des Widerrufsrechts hinsichtlich dieses Vertrags unterrichten, bereits erbrachten Dienstleistungen im "
        "Vergleich zum Gesamtumfang der im Vertrag vorgesehenen Dienstleistungen entspricht.",
        "Erläuterung, nicht Teil des Musters: Praktisch heißt das, du bekommst den Teil der Aufladung zurück, den du "
        "noch nicht verbraucht hast. Die Gebühr des Zahlungsanbieters erstatten wir mit.",
        "## Muster-Widerrufsformular",
        "(Wenn Sie den Vertrag widerrufen wollen, dann füllen Sie bitte dieses Formular aus und senden Sie es zurück.)",
        f"- An {wer}:\n"
        "- Hiermit widerrufe(n) ich/wir (*) den von mir/uns (*) abgeschlossenen Vertrag über den Kauf der folgenden "
        "Waren (*)/die Erbringung der folgenden Dienstleistung (*)\n"
        "- Bestellt am (*)/erhalten am (*)\n"
        "- Name des/der Verbraucher(s)\n"
        "- Anschrift des/der Verbraucher(s)\n"
        "- Unterschrift des/der Verbraucher(s) (nur bei Mitteilung auf Papier)\n"
        "- Datum",
        "(*) Unzutreffendes streichen.",
    ])


def _widerruf_en(a: dict) -> str:
    wer = f"{a['name']}, {a['anschrift']}, {a['kontakt']}"
    return "\n\n".join([
        "# Withdrawal notice",
        "## Right of withdrawal",
        "You have the right to withdraw from this contract within 14 days without giving any reason.",
        "The withdrawal period will expire after 14 days from the day of the conclusion of the contract.",
        f"To exercise the right of withdrawal, you must inform us ({wer}) of your decision to withdraw from this "
        "contract by an unequivocal statement (e.g. a letter sent by post or e-mail). You may use the attached model "
        "withdrawal form, but it is not obligatory.",
        "To meet the withdrawal deadline, it is sufficient for you to send your communication concerning your exercise "
        "of the right of withdrawal before the withdrawal period has expired.",
        "## Effects of withdrawal",
        "If you withdraw from this contract, we shall reimburse to you all payments received from you, including the "
        "costs of delivery (with the exception of the supplementary costs resulting from your choice of a type of "
        "delivery other than the least expensive type of standard delivery offered by us), without undue delay and in "
        "any event not later than 14 days from the day on which we are informed about your decision to withdraw from "
        "this contract. We will carry out such reimbursement using the same means of payment as you used for the "
        "initial transaction, unless you have expressly agreed otherwise; in any event, you will not incur any fees as "
        "a result of such reimbursement.",
        "If you requested to begin the performance of services during the withdrawal period, you shall pay us an "
        "amount which is in proportion to what has been provided until you have communicated us your withdrawal from "
        "this contract, in comparison with the full coverage of the contract.",
        "Explanation, not part of the model text: In practice, you get back the part of the top-up you have not used "
        "yet. We also refund the payment provider's fee.",
        "## Model withdrawal form",
        "(Complete and return this form only if you wish to withdraw from the contract.)",
        f"- To {wer}:\n"
        "- I/We (*) hereby give notice that I/We (*) withdraw from my/our (*) contract of sale of the following goods "
        "(*)/for the provision of the following service (*),\n"
        "- Ordered on (*)/received on (*),\n"
        "- Name of consumer(s),\n"
        "- Address of consumer(s),\n"
        "- Signature of consumer(s) (only if this form is notified on paper),\n"
        "- Date",
        "(*) Delete as appropriate.",
    ])


# ---------------------------------------------------------------- Zusatz zum Datenschutzhinweis
ANBIETER_ANSCHRIFT = {
    "stripe": "Stripe Payments Europe, Limited, 1 Grand Canal Street Lower, Grand Canal Dock, Dublin, D02 H210, Irland "
              "[Anschrift vor dem Veröffentlichen auf stripe.com prüfen]",
    "paypal": "PayPal (Europe) S.à r.l. et Cie, S.C.A., 22–24 Boulevard Royal, L-2449 Luxemburg "
              "[Anschrift vor dem Veröffentlichen auf paypal.com prüfen]",
}
ANBIETER_HINWEISE = {"stripe": "stripe.com/de/privacy", "paypal": "paypal.com/de/legalhub/privacy-full"}


def datenschutz_abschnitt(db: Session) -> list[str]:
    """Abschnitt „Guthaben und Zahlungen“ für die Datenschutz-Vorlage (nur bei eingeschalteter Abrechnung)."""
    from app import abrechnung, zahlung

    if not abrechnung.an(db):
        return []
    wege = zahlung.bereit(db) or list(zahlung.ANBIETER)
    teile = ["## Guthaben und Zahlungen",
             "Wenn du als Spielleitung Guthaben nutzt, speichert der Server dazu: Buchungen (Betrag, Zeitpunkt, Art, "
             "zugehörige Verarbeitung), Bezahlvorgänge (Betrag, Gebühr, Zahlungsanbieter, dessen Kennung des Vorgangs, "
             "Zustand, Zeitpunkt und Fassung deiner Zustimmung zum sofortigen Beginn) und deine Einstellungen zum "
             "Guthaben. Kartendaten, Bankverbindungen und Zugangsdaten zum Zahlungsanbieter sieht und speichert der "
             "Server nicht. Rechtsgrundlage ist die Erfüllung des Vertrags (Art. 6 Abs. 1 lit. b DSGVO) und die "
             "gesetzliche Aufbewahrungspflicht für Buchungsbelege (Art. 6 Abs. 1 lit. c DSGVO mit § 147 AO).",
             "Bezahlt wird auf den Seiten von:\n" + "\n".join(f"- {ANBIETER_ANSCHRIFT[w]}" for w in wege),
             "An den Anbieter gibt der Server nur den Betrag, die Währung und eine interne Kennung des Vorgangs weiter – "
             "keine Namen, keine Kampagnen- oder Runden-Inhalte. Was du dort selbst eingibst (z. B. Name, "
             "E-Mail-Adresse, Zahlungsdaten), erhebt der Anbieter in eigener Verantwortung, auch zur Betrugsabwehr; es "
             "gelten dessen Datenschutzhinweise (" + ", ".join(ANBIETER_HINWEISE[w] for w in wege) + "). "
             + ("Beide Anbieter können" if len(wege) > 1 else "Der Anbieter kann")
             + " Daten auch in die USA übermitteln [Grundlage laut Anbieter prüfen, z. B. EU-US Data Privacy Framework "
             "oder Standardvertragsklauseln].",
             "Buchungen und Bezahlvorgänge bewahren wir [8] Jahre auf (Aufbewahrungsfrist für Buchungsbelege), "
             "gerechnet ab Ende des Kalenderjahres der Buchung. Löschst du dein Konto, bleiben sie bis dahin ohne Bezug "
             "zu deiner Person. Ist noch Guthaben zu erstatten, behalten wir dafür deine E-Mail-Adresse, bis erstattet "
             "ist, höchstens [90] Tage. Eine Bankverbindung, nach der wir für eine Erstattung per Mail fragen, bleibt "
             "nur im Postfach und wird nach der Überweisung gelöscht."]
    return teile
