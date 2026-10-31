# Протокол телефона и сервера

Договор между iOS и сервером, уточняет раздел ТЗ «Протокол обмена». Менять только вместе с обеими сторонами.

Адрес берётся из App Group, по умолчанию `DEFAULT_SERVER_URL`. Каждый запрос с телефона несёт заголовок `X-Token`.
Без него или с неверным значением ответ `401`. Сессии живут в памяти сервера. Неизвестная сессия: `404`.

## Запросы

| Метод и путь | Кто вызывает | Тело | Ответ |
|---|---|---|---|
| `POST /sessions` | приложение | `{"goal": "", "user_lang": "ru", "target_lang": "id", "text_entry": "keyboard"}` | `201 {"session_id": "s_ab12cd34"}` |
| `POST /sessions/{id}/goal` | приложение | `{"goal": "Заказать шампунь от перхоти"}` | `200 {"reply_ru": "Хорошо. Включи демонстрацию экрана."}`. Если трансляция уже идёт, сервер сразу разбирает последний кадр |
| `POST /transcribe` | приложение | `audio/wav`: моно, 16 бит, 16 кГц | `200 {"text": "..."}` |
| `POST /sessions/{id}/frames` | расширение записи | `image/jpeg` | `202 {"accepted": true}` или `202 {"accepted": false, "reason": "busy"}` |
| `POST /sessions/{id}/text` | расширение записи или приложение | `{"frame_ts": 1793436844526, "lines": [{"text": "Beli sekarang", "words": [{"text": "Beli", "box": [0.1, 0.8, 0.2, 0.83]}]}]}` | `200 {"ok": true}` |
| `GET /sessions/{id}/suggestions` | клавиатура | | `200 {"step_id": "s3", "texts": ["sampo anti ketombe"], "hint_ru": "запрос для поиска"}` |
| `GET /sessions/{id}/hint?after=s3` | приложение, запасной путь без WebSocket, раз в секунду | | `200` событие `hint` новее `after`, иначе `204` |
| `POST /translate` | клавиатура | `{"text": "...", "from": "ru", "to": "id", "session_id": "s_ab12cd34"}` | `200 {"text": "..."}` |
| `POST /sessions/{id}/broadcast` | расширение записи | `{"state": "started"}` или `{"state": "finished"}` | `200 {"ok": true}` |
| `POST /sessions/{id}/end` | приложение | | `200 {"ok": true}` |
| `WS /sessions/{id}/events` | приложение | `X-Token` в заголовке подключения | поток событий |
| `GET /debug/{id}?token=...` | разработчик | | страница, обновляется раз в секунду |
| `GET /health` | все | без токена | `200 {"ok": true}` |

`target_lang` может быть `"auto"`: тогда язык приложения определяет модель по экрану. `text_entry` принимает `"keyboard"`
или `"clipboard"`. Кадр сопровождают заголовки `X-Frame-Width` и `X-Frame-Height` (исходный размер экрана в пикселях) и
`X-Frame-Ts` (время кадра в миллисекундах). Расширение не шлёт новый кадр, пока прошлый запрос в полёте.

`/text` несёт текст экрана, распознанный на телефоне: `frame_ts` равен `X-Frame-Ts` того же кадра, рамки в долях 0–1 от левого
верхнего угла. Запрос необязателен и может прийти после кадра: рамку сервер выбирает, когда модель уже ответила, то есть через
2–3 секунды. Без него указка работает хуже, но работает: остаются SAM и рамка модели.

## События

Сервер шлёт JSON-строки по WebSocket.

```json
{"type": "hint", "step_id": "s3", "instruction_ru": "Нажми на строку поиска вверху", "speak": true,
 "image_jpeg_base64": "...", "insert_texts": ["sampo anti ketombe"], "insert_hint_ru": "запрос для поиска",
 "paste_text": null, "needs_user_confirm": false, "frame_ts": 1793436844526}
{"type": "status", "state": "analyzing", "text_ru": "Агент думает"}
{"type": "done", "text_ru": "Готово, цель достигнута"}
```

`status.state`: `waiting_frames`, `broadcasting`, `analyzing`, `broadcast_stopped`, `error`.

Если `text_entry` равен `clipboard`, в подсказке есть `paste_text`: приложение кладёт его в буфер обмена сразу при получении
события, а `instruction_ru` просит зажать поле и выбрать «Вставить». При `keyboard` поле `paste_text` равно `null`, а тексты
берёт клавиатура из `/suggestions`.

`image_jpeg_base64` всегда 720×960. Голос подсказки на телефоне: `instruction_ru`, если `speak` равен `true`. `frame_ts` равен
`X-Frame-Ts` кадра, по которому сделана подсказка: так телефон меряет полное время от кадра до подсказки.

## App Group

Ключи `UserDefaults` группы: `serverURL`, `token`, `sessionId`, `userLang`, `targetLang`. Их пишет приложение, читают расширение
записи и клавиатура.
