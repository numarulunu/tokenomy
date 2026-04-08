"""Currency config + conversion tests."""
from __future__ import annotations

import json

import pytest

from tuner import currency


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    p = tmp_path / "currency.json"
    monkeypatch.setattr(currency, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(currency, "CONFIG_PATH", str(p))
    yield


def test_load_defaults_to_usd_when_missing():
    cfg = currency.load_currency()
    assert cfg["code"] == "USD"
    assert cfg["symbol"] == "$"
    assert cfg["rate_to_usd"] == 1.0


def test_save_known_currency_uses_default_rate_and_symbol():
    cfg = currency.save_currency("EUR")
    assert cfg["code"] == "EUR"
    assert cfg["symbol"] == "\u20ac"
    assert cfg["rate_to_usd"] == pytest.approx(0.92)
    loaded = currency.load_currency()
    assert loaded == cfg


def test_save_custom_rate_and_symbol():
    cfg = currency.save_currency("XYZ", rate=2.5, symbol="X$")
    assert cfg == {"code": "XYZ", "symbol": "X$", "rate_to_usd": 2.5}


def test_save_unknown_without_rate_errors():
    with pytest.raises(SystemExit):
        currency.save_currency("XYZ")


def test_convert_applies_rate():
    currency.save_currency("EUR")
    converted, symbol = currency.convert(10.0)
    assert symbol == "\u20ac"
    assert converted == pytest.approx(9.2)


def test_reset_reverts_to_usd():
    currency.save_currency("EUR")
    currency.reset_currency()
    assert currency.load_currency()["code"] == "USD"


def test_corrupt_file_falls_back_to_usd():
    with open(currency.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert currency.load_currency()["code"] == "USD"


def test_negative_rate_falls_back_to_usd():
    with open(currency.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"code": "EUR", "symbol": "\u20ac", "rate_to_usd": -1}, f)
    assert currency.load_currency()["code"] == "USD"


def test_cli_set_and_show(capsys):
    assert currency.main(["set", "EUR"]) == 0
    out = capsys.readouterr().out
    assert "EUR" in out and "\u20ac" in out

    assert currency.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "EUR" in out


def test_cli_reset(capsys):
    currency.save_currency("EUR")
    assert currency.main(["reset"]) == 0
    assert currency.load_currency()["code"] == "USD"
