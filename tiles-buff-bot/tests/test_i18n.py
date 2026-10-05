import json

import pytest

from core import i18n
from core.timeparse import format_duration

from .test_web import register, site  # noqa: F401  (фикстура)


@pytest.fixture
def catalogs(tmp_path, monkeypatch):
    (tmp_path / "en.json").write_text(json.dumps({
        "Привет, {nick}!": "Hi, {nick}!", "VS сегодня": "VS today", "Стройка": "Construction",
        "Электростанция": "Power Plant",
    }), encoding="utf-8")
    (tmp_path / "es.json").write_text(json.dumps({"Привет, {nick}!": "¡Hola, {nick}!"}), encoding="utf-8")
    monkeypatch.setattr(i18n, "_LOCALES", tmp_path)
    i18n.reload()
    yield
    i18n.reload()


def test_translate_with_fallbacks(catalogs):
    with i18n.using("es"):
        assert i18n.t("Привет, {nick}!", nick="Ana") == "¡Hola, Ana!"
        assert i18n.t("VS сегодня") == "VS today"  # нет в es → английский
        assert i18n.t("Нет перевода") == "Нет перевода"  # нет нигде → русский
    assert i18n.t("VS сегодня") == "VS сегодня"


def test_pick_lang():
    assert i18n.pick_lang("pt", "en-US") == "pt"
    assert i18n.pick_lang(None, "es-ES,es;q=0.9") == "es"
    assert i18n.pick_lang("xx", "de-DE") == "ru"


def test_duration_units_follow_language():
    assert format_duration(90061) == "1д 1ч"
    with i18n.using("en"):
        assert format_duration(90061) == "1d 1h"


def test_language_switch_is_remembered(site, catalogs):  # noqa: F811
    svc, owner, _ = site
    register(owner, "Ana")
    r = owner.get("/lang/en?next=/guides", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/guides"
    home = owner.get("/").text
    assert '<html lang="en">' in home and "Hi, Ana!" in home and "VS today" in home
    assert svc.db.one("SELECT lang FROM players WHERE nick = 'Ana'")["lang"] == "en"
    assert "How it all works" in owner.get("/help").text
    owner.get("/lang/ru")
    assert "Привет, Ana!" in owner.get("/").text
    assert owner.get("/lang/zz", follow_redirects=False).headers["location"] == "/"
