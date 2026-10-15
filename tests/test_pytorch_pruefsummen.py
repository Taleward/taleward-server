"""Prüfsummen der PyTorch-Dateien für die Freigabe (deploy/pytorch_pruefsummen.py)."""
import importlib.util
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]


def _modul():
    spec = importlib.util.spec_from_file_location("pytorch_pruefsummen", WURZEL / "deploy" / "pytorch_pruefsummen.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _seite(paket, fassung, variante, systeme=("win_amd64", "manylinux_2_28_x86_64"), extra=""):
    zeilen = []
    for i, s in enumerate(systeme):
        h = f"{abs(hash((paket, variante, s))) % 10**60:064d}"
        name = f"{paket}-{fassung}%2B{variante}-cp311-cp311-{s}.whl"
        zeilen.append(f'<a href="/whl/{variante}/{name}#sha256={h}">{name}</a><br/>')
    zeilen.append(f'<a href="/whl/{variante}/{paket}-{fassung}%2B{variante}-cp312-cp312-win_amd64.whl#sha256={"1" * 64}">x</a>')
    zeilen.append(f'<a href="/whl/{variante}/{paket}-9.9.9%2B{variante}-cp311-cp311-win_amd64.whl#sha256={"2" * 64}">x</a>')
    return "<html><body>" + "".join(zeilen) + extra + "</body></html>"


def test_versionen_aus_der_echten_uv_lock():
    m = _modul()
    f = m.fassungen((WURZEL / "uv.lock").read_text(encoding="utf-8"))
    assert set(f) == {"torch", "torchaudio", "torchvision"} and all(f.values())


def test_liste_je_variante_nur_python_311(monkeypatch):
    m = _modul()
    lock = (WURZEL / "uv.lock").read_text(encoding="utf-8")
    f = m.fassungen(lock)
    seiten = []

    def laden(adresse):
        seiten.append(adresse)
        variante, paket = adresse.rstrip("/").split("/")[-2:]
        return _seite(paket, f[paket], variante)
    text = m.erzeugen(lock, laden)
    zeilen = [z for z in text.splitlines() if not z.startswith("#")]
    assert len(zeilen) == 6 and len(seiten) == 6
    assert all(s.startswith("https://download.pytorch.org/whl/") for s in seiten)
    torch_cu = next(z for z in zeilen if z.startswith("cu128 torch=="))
    assert torch_cu.startswith(f"cu128 torch=={f['torch']}+cu128 --hash=sha256:") and torch_cu.count("--hash=") == 2
    assert "1" * 64 not in text and "2" * 64 not in text  # andere Python-Fassung, andere torch-Fassung


def test_ohne_windows_datei_keine_liste():
    m = _modul()
    lock = (WURZEL / "uv.lock").read_text(encoding="utf-8")
    f = m.fassungen(lock)

    def laden(adresse):
        variante, paket = adresse.rstrip("/").split("/")[-2:]
        return _seite(paket, f[paket], variante, systeme=("manylinux_2_28_x86_64",))
    with pytest.raises(m.Fehler):
        m.erzeugen(lock, laden)


def test_kaputte_pruefsumme_bricht_ab():
    m = _modul()
    html = '<a href="/whl/cpu/torch-2.8.0%2Bcpu-cp311-cp311-win_amd64.whl#sha256=xyz">x</a>'
    with pytest.raises(m.Fehler):
        m.dateien(html, "torch", "2.8.0")


def test_aufruf_ohne_netz_meldet_fehler(tmp_path, monkeypatch, capsys):
    m = _modul()

    def kein_netz(adresse):
        raise OSError("kein Netz")
    monkeypatch.setattr(m, "seite_laden", kein_netz)
    ziel = tmp_path / "liste.txt"
    assert m.main(["x", str(WURZEL / "uv.lock"), str(ziel)]) == 1 and not ziel.exists()


def test_umleitung_nur_https_und_gleicher_host():
    import urllib.request

    m = _modul()
    h = m._NurGleicherHost()
    anfrage = urllib.request.Request("https://download.pytorch.org/whl/cpu/torch/")
    for ziel in ("http://download.pytorch.org/whl/cpu/torch/", "https://boese.example/whl/cpu/torch/"):
        with pytest.raises(m.Fehler):
            h.redirect_request(anfrage, None, 302, "Found", {}, ziel)
    assert h.redirect_request(anfrage, None, 302, "Found", {}, "https://download.pytorch.org/whl/cpu/torch/x") is not None
    with pytest.raises(m.Fehler):
        m.seite_laden("http://download.pytorch.org/whl/cpu/torch/")


def test_linux_nur_mit_linux_und_x86_64():
    m = _modul()
    lock = (WURZEL / "uv.lock").read_text(encoding="utf-8")
    f = m.fassungen(lock)

    def laden(adresse):  # „x86_64“ gibt es nur für macOS – das zählt nicht als Linux
        variante, paket = adresse.rstrip("/").split("/")[-2:]
        return _seite(paket, f[paket], variante, systeme=("win_amd64", "macosx_14_0_x86_64"))
    with pytest.raises(m.Fehler):
        m.erzeugen(lock, laden)


def test_pflicht_rad_ohne_pruefsumme_ist_fehler():
    m = _modul()
    ohne = '<a href="/whl/cpu/torch-2.8.0%2Bcpu-cp311-cp311-win_amd64.whl">x</a>'
    with pytest.raises(m.Fehler):
        m.dateien(ohne, "torch", "2.8.0")
    anderes = '<a href="/whl/cpu/torch-2.8.0%2Bcpu-cp311-cp311-macosx_14_0_arm64.whl">x</a>'
    assert m.dateien(anderes, "torch", "2.8.0") == {}
