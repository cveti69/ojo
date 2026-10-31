# Сервер

Python 3.9–3.14, один процесс: сессии хранятся в памяти. Запускать из `backend/`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export SHARED_TOKEN='задай общий токен сервера и телефона'
export META_API_KEY='задай ключ Meta'
export META_VISION_MODEL='доступная модель с изображениями'
export META_TEXT_MODEL='доступная текстовая модель'
export ELEVENLABS_API_KEY='задай ключ Eleven Labs, без него фразы читает системный голос телефона'
.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8000 --workers 1
```

Секреты передавай через окружение; сервер не читает `.env`. Для телефона нужен адрес HTTPS через прокси или туннель.
`GET /health` доступен без токена, остальные запросы требуют `X-Token`. Протокол: [../docs/protocol.md](../docs/protocol.md).
Отладка: `/debug/SESSION_ID?token=SHARED_TOKEN`, обновление раз в секунду. Страница также принимает `X-Token`.
Токен из query скрывается в стандартных логах uvicorn; в логах внешнего HTTPS-прокси скрытие query настраивается отдельно.

Обязательны четыре переменные из примера. Дополнительно:

| Переменная | По умолчанию / назначение |
|---|---|
| `APP_NAME` | Имя продукта, единственное место настройки — `app/config.py` |
| `META_API_BASE` | `https://api.meta.ai/v1` |
| `META_TRANSCRIBE_MODEL` | `muse-voice-transcribe-1.0` |
| `META_SAM_MODEL` | `sam-3.1` |
| `META_REASONING` | `minimal` |
| `GROUNDING_MODE` | `direct`; реализацию определяет `app/grounding.py` |
| `SAVE_FRAMES` | `0`; только `1` разрешает запись кадров |
| `FRAMES_DIR` | `screen-agent-frames` в системной временной папке; обязательно вне репозитория |
| `SESSION_TTL_SECONDS` | `3600`, время без обращений до удаления сессии |
| `SESSION_CLEANUP_SECONDS` | `30`, период удаления устаревших сессий |
| `MAX_FRAME_BYTES` / `MAX_FRAME_PIXELS` | `8388608` / `20000000` |
| `MAX_AUDIO_BYTES` | `16777216` |

Распознавание принимает тело `audio/wav`: моно, PCM 16 бит, 16 кГц. Пустой `transcript` возвращается как `200 {"text":""}`.
Интерфейс распознавания изолирован в `app/voice.py`. Перевод принимает `from`, `to` и необязательный `session_id`.
Для `to: "auto"` нужен известный язык сессии; до определения языка по экрану ответ `409`.

Занятый анализ возвращает `202 {"accepted":false,"reason":"busy"}` и запоминает только последний кадр.
Другие причины пропуска: `unchanged`, `user_confirm`, `done`. На оплате анализ останавливается до новой цели или завершения сессии.
Завершённая через `/end` сессия отклоняет новые кадры с `409`. Время каждого этапа видно в `/debug` в миллисекундах;
`send_ms` измеряет публикацию события и, при подключении WebSocket, отправку сокету.

Проверка без настоящих запросов к Meta:

```sh
.venv/bin/python -m pip install pytest httpx
.venv/bin/python -m pytest tests -q
```

Для Python 3.9 requirements выбирает Pillow 11.3, для 3.10–3.14 — 12.1. На телефоне и настоящих моделях нужен отдельный сквозной прогон.
