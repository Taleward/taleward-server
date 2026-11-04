"""0.4.149 (S57): Der frische zweite Faktor gilt nur in der Sitzung, in der er gezeigt wurde – Tests binden ihn an die
Sitzung ihres Test-Clients."""
import jwt


def sitzung(client, bereich: str = "verwaltung") -> str:
    wert = None
    for c in client.cookies.jar:
        if c.name == f"tw_{bereich}":
            wert = c.value
    try:
        return jwt.decode(wert or "", options={"verify_signature": False}).get("jti") or "-"
    except jwt.PyJWTError:
        return "-"


def frisch_merken(client, dbs, user_id: str, bereich: str = "verwaltung") -> None:
    from app.verwaltung import zweiter_faktor

    zweiter_faktor.frisch_merken(dbs, user_id, bereich, sitzung=sitzung(client, bereich))
