"""
Отправка факта вызова в журнал samui-guide (call_log).

Главное здесь — не «лог доехал», а то, что он НЕ МОЖЕТ помешать инструменту.
Обёртка стоит на пути каждого вызова, и её отказ не должен превращаться в
отказ инструмента: агент обязан получить ответ Метрики даже когда Django
лежит, отвечает 500 или отвергает секрет.

Сеть не используется: httpx.AsyncClient подменяется.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ya_metrics_mcp import call_log
from ya_metrics_mcp.call_log import log_call, reset_failure_state

ENV = {
    "METRIKA_LOG_ENDPOINT_URL": "https://samuiguide.ru/api/metrika/log/",
    "METRIKA_LOG_SECRET": "log-secret",
}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Предохранитель — модульное состояние, между тестами его надо сбрасывать."""
    reset_failure_state()
    for name in ("METRIKA_LOG_ENDPOINT_URL", "METRIKA_LOG_SECRET", "METRIKA_LOG_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    yield
    reset_failure_state()


def configure(monkeypatch, **extra):
    for name, value in {**ENV, **extra}.items():
        monkeypatch.setenv(name, value)


def fake_http(status_code=201, raises=None):
    """Подменяет httpx.AsyncClient; возвращает (patcher, список отправленных запросов)."""
    sent = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            if raises is not None:
                raise raises
            sent.append({"url": url, "json": json, "headers": headers,
                         "timeout": self.timeout})
            response = MagicMock()
            response.status_code = status_code
            response.text = "ответ"
            return response

    return patch.object(call_log.httpx, "AsyncClient", FakeClient), sent


# --- Инструмент-заглушка ---


@log_call
async def sample_tool(ctx=None, counter_id="123", period="7d"):
    """Заглушка инструмента."""
    return "x" * 42


@log_call
async def failing_tool(ctx=None, counter_id="123"):
    raise RuntimeError("Метрика вернула 500")


# --- Успешная отправка ---


def test_sends_fact_of_call(monkeypatch):
    configure(monkeypatch)
    patcher, sent = fake_http()

    with patcher:
        result = asyncio.run(sample_tool(counter_id="999", period="30d"))

    assert result == "x" * 42
    assert len(sent) == 1
    payload = sent[0]["json"]
    assert payload["tool"] == "sample_tool"
    assert payload["status"] == "ok"
    assert payload["source"] == "mcp"
    assert payload["params"] == {"counter_id": "999", "period": "30d"}
    assert payload["duration_ms"] >= 0


def test_sends_secret_in_header(monkeypatch):
    configure(monkeypatch)
    patcher, sent = fake_http()

    with patcher:
        asyncio.run(sample_tool())

    assert sent[0]["headers"]["X-Metrika-Log-Secret"] == "log-secret"


def test_does_not_send_response_body(monkeypatch):
    """
    Содержимое ответа Метрики в журнал не уходит — ни целиком, ни фрагментом.
    Проверяем по всему телу запроса, а не только по result_summary.
    """
    configure(monkeypatch)
    patcher, sent = fake_http()

    secret_data = "ВИЗИТОВ_1284_ПОИСК_61"

    @log_call
    async def tool_with_data(ctx=None):
        return secret_data

    with patcher:
        asyncio.run(tool_with_data())

    assert secret_data not in json.dumps(sent[0]["json"], ensure_ascii=False)
    assert "символов" in sent[0]["json"]["result_summary"]


def test_only_explicitly_passed_arguments_are_logged(monkeypatch):
    """
    Значения по умолчанию в журнал не идут: он показывает, что агент
    ЗАПРОСИЛ, а не какими параметрами вызов в итоге исполнился. FastMCP
    передаёт аргументы клиента как kwargs, поэтому картина совпадает с тем,
    что реально прислал агент.
    """
    configure(monkeypatch)
    patcher, sent = fake_http()

    with patcher:
        asyncio.run(sample_tool(counter_id="42"))

    assert sent[0]["json"]["params"] == {"counter_id": "42"}


def test_ctx_and_none_params_are_not_sent(monkeypatch):
    configure(monkeypatch)
    patcher, sent = fake_http()

    with patcher:
        asyncio.run(sample_tool(ctx=object(), counter_id="1", period=None))

    assert sent[0]["json"]["params"] == {"counter_id": "1"}


def test_secret_like_params_are_dropped(monkeypatch):
    """Эндпоинт отверг бы такое с 400 — не отправляем вовсе."""
    configure(monkeypatch)
    patcher, sent = fake_http()

    @log_call
    async def tool_with_token(ctx=None, api_key="секрет", counter_id="1"):
        return "ok"

    with patcher:
        asyncio.run(tool_with_token(api_key="секрет", counter_id="1"))

    assert sent[0]["json"]["params"] == {"counter_id": "1"}


def test_timeout_is_short_by_default(monkeypatch):
    configure(monkeypatch)
    patcher, sent = fake_http()

    with patcher:
        asyncio.run(sample_tool())

    assert sent[0]["timeout"] == 3.0


def test_timeout_is_configurable(monkeypatch):
    configure(monkeypatch, METRIKA_LOG_TIMEOUT="1.5")
    patcher, sent = fake_http()

    with patcher:
        asyncio.run(sample_tool())

    assert sent[0]["timeout"] == 1.5


def test_broken_timeout_value_falls_back(monkeypatch):
    configure(monkeypatch, METRIKA_LOG_TIMEOUT="быстро")
    patcher, sent = fake_http()

    with patcher:
        asyncio.run(sample_tool())

    assert sent[0]["timeout"] == 3.0


# --- Отказ логирования не ломает инструмент ---


def test_network_failure_does_not_break_tool(monkeypatch):
    configure(monkeypatch)
    patcher, _ = fake_http(raises=OSError("сеть недоступна"))

    with patcher:
        result = asyncio.run(sample_tool())

    assert result == "x" * 42


def test_timeout_does_not_break_tool(monkeypatch):
    configure(monkeypatch)
    patcher, _ = fake_http(raises=TimeoutError("истёк таймаут"))

    with patcher:
        assert asyncio.run(sample_tool()) == "x" * 42


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_endpoint_rejection_does_not_break_tool(monkeypatch, status):
    """Неверный секрет, битая схема, лимит, отказ сервера — инструмент работает."""
    configure(monkeypatch)
    patcher, _ = fake_http(status_code=status)

    with patcher:
        assert asyncio.run(sample_tool()) == "x" * 42


def test_unset_config_disables_logging_silently(monkeypatch):
    """Без URL и секрета модуль молчит — локальная разработка не требует Django."""
    patcher, sent = fake_http()

    with patcher:
        assert asyncio.run(sample_tool()) == "x" * 42

    assert sent == []


def test_missing_secret_alone_disables_logging(monkeypatch):
    monkeypatch.setenv("METRIKA_LOG_ENDPOINT_URL", ENV["METRIKA_LOG_ENDPOINT_URL"])
    patcher, sent = fake_http()

    with patcher:
        asyncio.run(sample_tool())

    assert sent == [], "без секрета запрос уйти не должен"


# --- Ошибка инструмента ---


def test_tool_error_is_logged_and_reraised(monkeypatch):
    configure(monkeypatch)
    patcher, sent = fake_http()

    with patcher, pytest.raises(RuntimeError, match="Метрика вернула 500"):
        asyncio.run(failing_tool())

    assert sent[0]["json"]["status"] == "error"
    assert "RuntimeError" in sent[0]["json"]["error"]


def test_logging_failure_during_tool_error_still_reraises(monkeypatch):
    """Двойной отказ: и инструмент упал, и лог не ушёл. Агент видит ошибку инструмента."""
    configure(monkeypatch)
    patcher, _ = fake_http(raises=OSError("сеть недоступна"))

    with patcher, pytest.raises(RuntimeError, match="Метрика вернула 500"):
        asyncio.run(failing_tool())


# --- Предохранитель ---


def test_circuit_breaker_stops_trying(monkeypatch):
    """
    При лежащем Django без предохранителя каждый вызов оплачивал бы таймаут.
    После FAILURE_LIMIT неудач попытки прекращаются.
    """
    configure(monkeypatch)
    attempts = []

    class CountingClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            attempts.append(1)
            raise OSError("сеть недоступна")

    with patch.object(call_log.httpx, "AsyncClient", CountingClient):
        for _ in range(10):
            assert asyncio.run(sample_tool()) == "x" * 42

    assert len(attempts) == call_log.FAILURE_LIMIT, (
        f"после {call_log.FAILURE_LIMIT} неудач попытки должны прекратиться"
    )


def test_success_resets_failure_counter(monkeypatch):
    configure(monkeypatch)

    failing, _ = fake_http(raises=OSError("сеть"))
    with failing:
        asyncio.run(sample_tool())
        asyncio.run(sample_tool())

    ok, sent = fake_http()
    with ok:
        asyncio.run(sample_tool())
    assert len(sent) == 1

    failing2, _ = fake_http(raises=OSError("сеть"))
    with failing2:
        for _ in range(2):
            asyncio.run(sample_tool())

    ok2, sent2 = fake_http()
    with ok2:
        asyncio.run(sample_tool())
    assert len(sent2) == 1, "счётчик неудач должен сбрасываться после успеха"


# --- Декоратор не портит инструменты ---


def test_decorator_preserves_signature_and_docstring():
    """
    FastMCP строит схему параметров по сигнатуре и аннотациям. Потеряй их
    обёртка — инструменты остались бы без описания аргументов.
    """
    import inspect

    assert sample_tool.__name__ == "sample_tool"
    assert sample_tool.__doc__ == "Заглушка инструмента."
    params = inspect.signature(sample_tool).parameters
    assert set(params) == {"ctx", "counter_id", "period"}


def test_registered_tools_keep_their_schemas():
    """Сквозная проверка: у зарегистрированных инструментов параметры на месте."""
    from ya_metrics_mcp.servers.main import mcp
    import ya_metrics_mcp.servers.tools  # noqa: F401

    tools = asyncio.run(mcp.get_tools())
    schema = tools["get_goals_conversion"].parameters

    assert "counter_id" in schema["properties"], "параметры инструмента потерялись"
    assert tools["get_goals_conversion"].description, "описание инструмента потерялось"
