"""0.4.149 (S57): Der frische zweite Faktor gilt nur in der Sitzung, in der er gezeigt wurde – Tests binden ihn an die
Sitzung ihres Test-Clients."""
import jwt


def sitzung(client, bereich: str = "verwaltung") -> str:
    wert = None
    for c in client.cookies.jar:  # 0.4.165: über https heißt es „__Host-tw_…“ und hat Vorrang
        if c.name == f"tw_{bereich}" and wert is None or c.name == f"__Host-tw_{bereich}":
            wert = c.value
    try:
        return jwt.decode(wert or "", options={"verify_signature": False}).get("jti") or "-"
    except jwt.PyJWTError:
        return "-"


def frisch_merken(client, dbs, user_id: str, bereich: str = "verwaltung") -> None:
    from app.verwaltung import zweiter_faktor

    zweiter_faktor.frisch_merken(dbs, user_id, bereich, sitzung=sitzung(client, bereich))


def https_sitzung(client) -> None:
    """0.4.165 (S68): Tests melden sich teils über http an und rufen danach https-Adressen auf. Im Betrieb gibt es diese
    Mischung nicht; hier wird die Sitzung dafür unter dem https-Namen „__Host-…“ mitgegeben."""
    for c in list(client.cookies.jar):
        if c.name in ("tw_verwaltung", "tw_guthaben"):
            client.cookies.set("__Host-" + c.name, c.value)
