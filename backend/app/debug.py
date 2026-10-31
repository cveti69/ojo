"""Self-contained, periodically refreshed debug view."""
from __future__ import annotations

import base64
import html
import json

from . import config
from .sessions import Session


def render(session: Session) -> str:
    def formatted(value: object) -> str:
        return html.escape(json.dumps(value, ensure_ascii=False, indent=2))

    frame = ""
    if session.last_frame:
        frame = base64.b64encode(session.last_frame.jpeg).decode("ascii")
    image = session.last_hint["image_jpeg_base64"] if session.last_hint else ""
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="refresh" content="1"><meta name="referrer" content="no-referrer">
<title>{title} — отладка</title>
<style>
body{{font:16px system-ui,sans-serif;margin:24px;background:#111827;color:#f3f4f6}}
h1{{font-size:24px}}h2{{font-size:20px}}main{{display:flex;flex-wrap:wrap;gap:24px}}
section{{flex:1;min-width:280px}}img{{max-width:100%;max-height:75vh;object-fit:contain}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#1f2937;padding:16px;border-radius:12px}}
</style></head><body>
<h1>{title} — сессия {session_id}</h1><p>Обновляется раз в секунду. Статус: {status}</p>
<main><section><h2>Последний кадр</h2><img alt="Последний кадр" src="data:image/jpeg;base64,{frame}"></section>
<section><h2>Подсказка</h2><img alt="Подсказка" src="data:image/jpeg;base64,{image}"></section></main>
<h2>Ответ модели</h2><pre>{answer}</pre>
<h2>Задержка по этапам, мс</h2><pre>{timings}</pre>
<h2>Рамка</h2><pre>{grounding}</pre>
</body></html>""".format(
        title=html.escape(config.APP_NAME), session_id=html.escape(session.id),
        status=html.escape(session.last_status["text_ru"]), frame=frame, image=image,
        answer=formatted(session.last_answer), timings=formatted(session.timings),
        grounding=formatted(session.grounding),
    )
