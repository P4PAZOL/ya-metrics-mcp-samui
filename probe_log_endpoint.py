"""
Зонд: доходит ли обёртка до журнала samui-guide.

Запускается вручную, когда записи на панели /cms/metrika/ не появляются, и
отвечает на вопрос, на каком именно шаге всё ломается. Читает те же
переменные из .env, что и сам MCP-сервер.

Ключевое свойство: зонд НАМЕРЕННО шлёт невалидную полезную нагрузку — без
обязательного поля status. Благодаря этому он различает все режимы отказа,
не создавая записи в боевом журнале. Валидная нагрузка была бы записана и
засоряла бы панель мусором при каждой диагностике.

    400 — связь есть, секрет верный, эндпоинт работает. Нужный результат.
    401 — связь есть, секрет НЕ совпадает с прод-.env samui-guide.
    503 — на проде не задан METRIKA_LOG_SECRET, приём выключен.
    429 — сработал лимит частоты.
    сеть/таймаут — эндпоинт недоступен с этой машины.

Запуск:
    cd ~/Projects/opt/ya-metrics-mcp-samui && .venv/bin/python probe_log_endpoint.py

Код возврата: 0 — путь до журнала исправен, 1 — нет.
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

TIMEOUT_SECONDS = 5.0

# Без обязательного поля status — см. докстринг. Не «чинить»: валидная
# нагрузка начнёт создавать записи в боевом журнале при каждом запуске.
PROBE_PAYLOAD = {"tool": "probe_connectivity"}

VERDICTS = {
    400: (
        "СВЯЗЬ И СЕКРЕТ В ПОРЯДКЕ. Эндпоинт принял запрос и отверг его по схеме,\n"
        "как и задумано зондом. Значит дело не в сети и не в секрете — смотреть\n"
        "надо, доходил ли вызов инструмента до обёртки (есть ли @log_call и не\n"
        "сработал ли предохранитель после трёх неудач)."
    ),
    401: (
        "СЕКРЕТ НЕ СОВПАДАЕТ. METRIKA_LOG_SECRET в .env форка отличается от того,\n"
        "что стоит в прод-.env samui-guide. Сверить и выровнять."
    ),
    503: (
        "НА ПРОДЕ ПРИЁМ ВЫКЛЮЧЕН: METRIKA_LOG_SECRET не задан в прод-.env либо\n"
        "gunicorn не перезапускался после его добавления."
    ),
    429: "ЛИМИТ ЧАСТОТЫ. Подождать минуту и повторить.",
}


def describe_config(url: str, secret: str, token: str) -> str:
    """Что видит зонд в окружении. Значения секретов не печатаются — только длины."""
    return (
        f"METRIKA_LOG_ENDPOINT_URL: {url or '(ПУСТО)'}\n"
        f"METRIKA_LOG_SECRET:       {f'задан, длина {len(secret)}' if secret else '(ПУСТО)'}\n"
        f"YANDEX_API_KEY:           {f'задан, длина {len(token)}' if token else '(ПУСТО)'}"
    )


def main() -> int:
    load_dotenv()
    url = os.environ.get("METRIKA_LOG_ENDPOINT_URL", "").strip()
    secret = os.environ.get("METRIKA_LOG_SECRET", "").strip()
    token = os.environ.get("YANDEX_API_KEY", "").strip()

    print("=== конфигурация из .env ===")
    print(describe_config(url, secret, token))
    print()

    if not url or not secret:
        print(
            "ИТОГ: логирование ВЫКЛЮЧЕНО — не заполнена одна из двух переменных.\n"
            "Обёртка в этом случае молча ничего не отправляет, поэтому панель пуста."
        )
        return 1

    print("=== запрос (заведомо невалидный, записи не создаст) ===")
    try:
        response = httpx.post(
            url,
            json=PROBE_PAYLOAD,
            headers={"X-Metrika-Log-Secret": secret},
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        print(f"СЕТЬ: {type(exc).__name__}: {exc}")
        print("\nИТОГ: эндпоинт недоступен с этой машины.")
        return 1

    print(f"HTTP {response.status_code}")
    print(f"тело: {response.text[:300]}\n")

    verdict = VERDICTS.get(
        response.status_code,
        f"НЕОЖИДАННЫЙ КОД {response.status_code} — показать вывод целиком.",
    )
    print("ИТОГ:", verdict)
    return 0 if response.status_code == 400 else 1


if __name__ == "__main__":
    sys.exit(main())
