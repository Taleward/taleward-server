"""Server 0.4.104: Das installierte Paket (KI-Paket der Worker-App, „Alles auf diesem PC“) bringt die
Datenbank-Migrationen mit und kann damit eine neue Datenbank anlegen."""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]


def test_migrationen_im_quellordner_und_im_paket(tmp_path):
    from app.db import migrationen_ort

    ordner, ini = migrationen_ort()
    assert ordner == WURZEL / "migrations" and ini == WURZEL / "alembic.ini"
    paket = tmp_path / "site-packages" / "app"
    paket.mkdir(parents=True)
    ordner, ini = migrationen_ort(paket)
    assert ordner == paket / "_migrationen" and ini == paket / "_migrationen" / "alembic.ini"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv fehlt")
def test_rad_bringt_migrationen_mit_und_legt_die_datenbank_an(tmp_path):
    r = subprocess.run(["uv", "build", "--wheel", "-q", "-o", str(tmp_path / "rad"), str(WURZEL)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    rad = next((tmp_path / "rad").glob("taleward_server-*.whl"))
    with zipfile.ZipFile(rad) as z:
        namen = set(z.namelist())
        z.extractall(tmp_path / "site")
    assert "app/_migrationen/env.py" in namen and "app/_migrationen/alembic.ini" in namen
    versionen = {n for n in namen if n.startswith("app/_migrationen/versions/") and n.endswith(".py")}
    assert len(versionen) == len(list((WURZEL / "migrations" / "versions").glob("*.py")))
    assert not any("__pycache__" in n for n in namen if n.startswith("app/_migrationen/"))
    # Wie im KI-Paket: nur das installierte Paket, kein Quellordner daneben – neue Datenbank anlegen
    daten = tmp_path / "daten"
    umgebung = {**os.environ, "PYTHONPATH": str(tmp_path / "site"), "DATA_DIR": str(daten), "PYTHONUTF8": "1"}
    umgebung.pop("DATABASE_URL", None)
    skript = ("import app.db as d, pathlib, sys; o, i = d.migrationen_ort(); "
              "assert str(o).startswith(sys.argv[1]), o; d.run_migrations(); print('ok')")
    r = subprocess.run([sys.executable, "-c", skript, str(tmp_path / "site")], capture_output=True, text=True,
                       env=umgebung, cwd=tmp_path)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), (r.stdout + r.stderr)[-2000:]
    assert (daten / "chronik.db").stat().st_size > 0
