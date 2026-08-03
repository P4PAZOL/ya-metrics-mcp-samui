"""
Зонд probe_log_endpoint.

Главное, что здесь закреплено, — зонд НЕ МОЖЕТ создать запись в боевом
журнале. Он шлёт заведомо невалидную нагрузку, и это не небрежность, а
условие его существования: диагностику запускают именно тогда, когда с
журналом что-то не так, и засорять его при каждой проверке нельзя.

Сеть не используется: httpx подменяется.
"""
import importlib.util
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_spec = importlib.util.spec_from_file_location(
    "probe_log_endpoint",
    pathlib.Path(__file__).resolve().parents[2] / "probe_log_endpoint.py",
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

ENV = {
    "METRIKA_LOG_ENDPOINT_URL": "https://samuiguide.ru/api/metrika/log/",
    "METRIKA_LOG_SECRET": "secret",
    "YANDEX_API_KEY": "token",
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **kw: None)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


def fake_post(status_code=400, raises=None):
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        if raises is not None:
            raise raises
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        response = MagicMock()
        response.status_code = status_code
        response.text = "{}"
        return response

    return patch.object(probe.httpx, "post", _post), calls


# --- Ключевое свойство ---


def test_payload_cannot_create_a_record():
    """
    Нагрузка обязана быть невалидной для эндпоинта: без поля status он
    ответит 400 и ничего не запишет. Появится status — зонд начнёт
    засорять боевой журнал при каждом запуске.
    """
    assert "status" not in probe.PROBE_PAYLOAD
    assert set(probe.PROBE_PAYLOAD) == {"tool"}


def test_sends_exactly_the_probe_payload(configured):
    patcher, calls = fake_post()

    with patcher:
        probe.main()

    assert calls[0]["json"] == probe.PROBE_PAYLOAD
    assert calls[0]["headers"] == {"X-Metrika-Log-Secret": "secret"}


# --- Коды возврата ---


def test_400_means_healthy(configured):
    patcher, _ = fake_post(status_code=400)
    with patcher:
        assert probe.main() == 0, "400 — исправный путь до журнала"


@pytest.mark.parametrize("status", [401, 429, 503, 500])
def test_other_codes_mean_broken(configured, status):
    patcher, _ = fake_post(status_code=status)
    with patcher:
        assert probe.main() == 1


def test_network_failure_is_reported(configured, capsys):
    patcher, _ = fake_post(raises=OSError("сеть недоступна"))
    with patcher:
        assert probe.main() == 1
    assert "недоступен" in capsys.readouterr().out


def test_unconfigured_env_reports_disabled_logging(monkeypatch, capsys):
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **kw: None)
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    assert probe.main() == 1
    assert "ВЫКЛЮЧЕНО" in capsys.readouterr().out


# --- Секреты не печатаются ---


def test_config_output_hides_secret_values():
    out = probe.describe_config(
        "https://example/", "СУПЕРСЕКРЕТ", "ТОКЕН-МЕТРИКИ"
    )
    assert "СУПЕРСЕКРЕТ" not in out
    assert "ТОКЕН-МЕТРИКИ" not in out
    assert "длина 11" in out and "длина 13" in out
