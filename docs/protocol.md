# Phone and server protocol

The contract between the iPhone client and the server. Change it only together with both sides.

The address comes from the App Group, by default `DEFAULT_SERVER_URL`. Every request from the phone carries an `X-Token` header.
Without it, or with a wrong value, the answer is `401`. Sessions live in the server's memory. An unknown session gets `404`.

## Requests

| Method and path | Caller | Body | Answer |
|---|---|---|---|
| `POST /sessions` | app | `{"user_lang": "es-MX", "target_lang": "id", "text_entry": "clipboard", "effort": "medium", "character": "warm", "own_character": "", "eyes": true, "signals": true}`, `eyes` is always `true` from the eleventh build on, `signals` if the phone sends `/changed` | `201 {"session_id": "s_ab12cd34"}` |
| `POST /transcribe` | app | `audio/wav`: mono, 16 bit, 16 kHz | `200 {"text": "..."}` |
| `POST /sessions/{id}/frames` | broadcast extension | `image/jpeg` | `202 {"accepted": true}` if the frame became a turn, or `202 {"accepted": false, "reason": "same"}`. `reason`: `same` the same screen, `small` a small change the next turn will see, `looking` the phone is sending the screen for a line, `busy` the answer was restarted twice already, `old` a newer frame has come, `not_looking` the person asked the agent not to watch and the frame goes nowhere. `lang`, once the model has named the language of the screen, two letters: the extension reads the next screens with it |
| `POST /sessions/{id}/changed` | broadcast extension, once for a change and once for a move until the next frame is delivered | `{"at": 1793436844526, "moved": true}`: `at` on the clock of `X-Frame-Ts`, `moved` if the person is leaving the screen | `200 {"ok": true}` |
| `POST /sessions/{id}/text` | broadcast extension or app | `{"frame_ts": 1793436844526, "lines": [{"text": "Beli sekarang", "words": [{"text": "Beli", "box": [0.1, 0.8, 0.2, 0.83]}]}]}` | `200 {"ok": true}` |
| `GET /sessions/{id}/suggestions` | keyboard | | `200 {"step_id": "s3", "texts": ["sampo anti ketombe"], "hint_ru": ""}` |
| `GET /sessions/{id}/hint?after=s3` | app, the fallback without a WebSocket, once a second | | `200` with a `hint` event newer than `after`, otherwise `204` |
| `WS /sessions/{id}/listen` | app | `X-Token` in the connection header, binary frames of 16 kHz, 16 bit, mono, 100 ms each, only while the phone hears a voice | No answers: lines arrive as events. The socket can be reopened, recognition lasts the whole call |
| `POST /sessions/{id}/say` | app, the fallback when no `heard` event came | `audio/wav`: mono, 16 bit, 16 kHz, a spoken line | `200 {"heard": true, "speech_ms": 900}` at once, the answer comes as events. `heard` is `false` if there are no words |
| `POST /sessions/{id}/words` | app, a typed line | `{"text": "Что это за кнопка?"}` | `200 {"ok": true}`, then the same as a spoken line |
| `POST /sessions/{id}/look` | app, in reply to a `look` event | `multipart/form-data`: part `frame`, `image/jpeg`, the file name is the capture time in milliseconds, `1793436844526.jpg`. Up to 4 parts, oldest first, the server looks at the last one | `200 {"ok": true}` at once. `409` if the line that needed the screen is out of date |
| `POST /sessions/{id}/played` | app, on the first sound of a line | `{"turn": 3}` | `200 {"ok": true}` |
| `POST /sessions/{id}/spoken` | app, for a line that did not sound to its end | `{"line": 2, "said": "Первая фраза."}`, `said` is empty if no sound of it played | `200 {"ok": true}` |
| `POST /speech` | app | `{"text": "Нажми на поиск", "voice": "JBFqnCBsd6RMkjVDRZzb"}`, `voice` is optional, an unknown voice is replaced with the server's | `200 audio/pcm`: 24 kHz, 16 bit, mono, as it is ready. `502` if the voice is unavailable: the system voice reads the line |
| `GET /voices` | app, settings | | `200 {"voices": [{"id": "JBFqnCBsd6RMkjVDRZzb", "name": "George"}], "default": "JBFqnCBsd6RMkjVDRZzb"}` |
| `POST /translate` | keyboard | `{"text": "...", "from": "ru", "to": "id", "session_id": "s_ab12cd34"}` | `200 {"text": "..."}` |
| `POST /sessions/{id}/broadcast` | broadcast extension | `{"state": "started"}` or `{"state": "finished"}` | `200 {"ok": true}` |
| `POST /sessions/{id}/end` | app | | `200 {"ok": true}` |
| `WS /sessions/{id}/events` | app | `X-Token` in the connection header | a stream of events |
| `GET /debug/{id}?token=...` | developer | | a page that refreshes once a second |
| `GET /health` | anyone | no token | `200 {"ok": true}` |

`user_lang` is the language chosen in settings, or with "Same as the phone" the first language of the phone at the moment of the call, as the phone writes it: letters, digits, `-` and `_`, anything else gets a 422. `ru` leaves the call as it always was.
Any other language adds that language to the rule about the spoken line, and the status words come in `ru`, `en` or `es`, English for
the rest. So despite their names `text_ru` and `instruction_ru` can hold any language.

`target_lang` can be `"auto"`: the model names the app's language from the screen, and until the first screen a translation into it
gets `409`. Once the model has named it, a translation goes into that language, whatever `to` says. `text_entry` takes `"keyboard"` or `"clipboard"`. A frame comes with the headers `X-Frame-Width` and `X-Frame-Height` (the
source screen size in pixels) and `X-Frame-Ts` (the frame time in milliseconds). The extension sends no new frame while the last
request is in flight, and gives up on `/frames`, `/text` and `/look` after 5 seconds without an answer. After a timeout or a lost
network the phone opens a new connection for the next request. `X-Frame-Small: 1` means the change is small and has settled, such as
a check mark or a price digit. It starts no turn, and the frame stays the latest one.

`effort` picks the model's reasoning depth for the whole call: `minimal`, `low`, `medium`, `high`, `xhigh` or `max`. Without it the
server's value applies, `low` for now. `character` picks the agent's manner: `warm`, `strict`, `brief`, `patient`, or `own` with its text in
`own_character`, up to 500 characters. The manner changes the tone only: the prohibitions and the answer format stay on the server.

## The call loop

A call is an ordinary chat with one model. A turn is the person's words or a new settled screen, and every turn gets one answer: what
to say, what to outline, what text to paste, and notes to itself. The whole conversation and the last 4 different screens go to the
model on every turn.

Words come from the `/listen` stream, from `/say` or from `/words`. With `signals` and no change since the last delivered frame, that
frame is the screen as it is now and the answer starts on it at once. Otherwise the server sends `look`, the app asks the extension
for the screen as it is and sends it to `/look`. There is no point waiting for a frame longer than a second: after 2 seconds the server
takes the latest frame it has. With no broadcast or no frames, the model gets a note that the screen is not shown.

`/changed` comes at the first change after a delivered frame, without a frame and without waiting for the screen to settle: 8 pixels of
256×256 are a change, and 40% of them, past the keyboard's 33%, are a move. A frame from `at` on ends the change. A move does this at
once: what is outlined goes, the answer in flight stops, and words with no answer heard wait for the next delivered frame, however
little it differs, or for 6.5 seconds with no frame. A line that has gone out stands until the phone reports it.

Every line and every ready text names its frame in `frame_ts`. The server does not send what it has about a screen the person has
left, and the phone checks it again against the moment of the move before it shows, plays or pastes anything. A line that has not
begun to sound is dropped, one that has ends with its sentence, and `/spoken` leaves the model with what the person heard. A dropped
line takes the rest of its answer with it. If that leaves words with no answer, they go to the frame of the new screen if it is here,
and wait for the next frame if not. An answer that cannot be put off any more, after 2 stops in a row, comes
with `frame_ts: null` and goes with any screen, or a video on the whole screen would never get one.

A frame counts as a new screen if at least 38% of the picture changed since the frame of the last turn: a video, a banner or the
keyboard do not change that much, and a transition does. Such a frame reaches the model with a note that the person is silent, and
the model decides by itself whether to say something.

A new turn cancels the answer in flight. Its turn stays in the chat and goes out with the next one. A screen that does not settle
cancels an answer at most 2 times in a row. With no line in 8 seconds, or a broken answer, the spare model starts beside the main
one and the main one is not cancelled. The first whole line has the say, and the other call changes neither the voice nor the chat.
Silence from the spare model waits for the main one. Nothing by 30 seconds ends the turn.

The answer can carry `look`, and no list of phrases decides it: the model understands. `false` means the person asked the agent not to
watch the screen at all, in any words and any language. The server reads this field in the stream, in any order of fields, and sends
`not_looking` with `on: true` as soon as it is written. The rules list it first, and an answer that writes it first sends no early
line, so the event goes ahead of the line that says so. An answer that writes it after `say` speaks first, and the event follows.
From then on the server takes no frame for a turn and keeps none: `/frames` answers with `reason: not_looking`, `/changed` does
nothing, `/look` answers 409, and the frames of that turn leave the chat and the log, the last frame of the call among them. The spare
model, if it starts after that, gets no picture. The phone stops sending frames and reading text, and the broadcast stays on. Text
read before that can still reach `/text`: it keeps a payment screen out of the log.

The field counts even if a new screen has stopped its answer by then, in the same turn. A later turn ignores it. Whatever took over
from that answer stops, and a look or a wait for a frame is dropped. If no line has gone out, their words go to the model with no
picture and the note that they asked it not to look, with one line in reply. The search does not know where in the answer it stands,
so a `look` nested in `notes` closes too: that mistake falls on the safe side.

While the eyes are open, the agent is looking. Until the field is written a frame can still go to the model: a new screen in that time
restarts the answer with that frame. The server cannot call it back, it only keeps none of the frames of that turn.

With the eyes shut the person's words reach the model with no picture and a note that they asked it not to look, and talk changes
nothing. `look: true` in that state means they asked it to look again. That answer goes unsaid, the server sends `not_looking` with
`on: false`, and their words wait for a fresh frame, with one line in reply. No older frame is left, so the wait is 6.5 seconds, and
with no frame by then the model gets the note that the screen is not shown.

A new call starts with the eyes open, and a lost connection changes nothing. The last `not_looking` is the only record of this state,
and the phone shuts the eyes on this event alone.

The Meta stream hears the line and finds the end of the phrase by itself. The server answers at once, and the app does not start the
voice until the person has finished. If the person goes on before the first sound (`/played`), the new words count as the same line:
the last answer is dropped, and a new one comes with a higher `turn`.

The server finds our window on a frame by its `#FF00AA` border and paints it grey: the model knows it is its own window, the words
under it do not count, and a change of the picture in the window is not a new screen.

`/text` carries the screen text read on the phone: `frame_ts` equals the `X-Frame-Ts` of that frame, and boxes are shares from 0 to 1
from the top left corner. The request is optional and may come after the frame: the server picks the box once the model has answered.
Without it the outline works worse, but it works: SAM and the model's own box remain.

## Events

The server sends JSON strings over the WebSocket.

```json
{"type": "heard", "turn": 3}
{"type": "look", "turn": 3}
{"type": "say", "turn": 3, "line": 2, "frame_ts": 1793436844526, "text_ru": "Жми зелёную кнопку внизу.", "paste_text": null, "timings": {"model_ms": 2710}}
{"type": "quiet", "turn": 3}
{"type": "hint", "step_id": "s3", "instruction_ru": "Жми зелёную кнопку внизу.", "image_jpeg_base64": "...",
 "frame_ts": 1793436844526, "timings": {"model_ms": 2710, "box_ms": 3, "box_source": "text", "window": true}}
{"type": "left"}
{"type": "read", "frame_ts": 1793436844526}
{"type": "not_looking", "on": true}
{"type": "status", "state": "broadcasting", "text_ru": "Демонстрация экрана включена"}
{"type": "done", "text_ru": "Сессия завершена"}
```

`status.state`: `waiting_frames`, `broadcasting`, `broadcast_stopped`. `done` comes only at the end of a call. `timings` are for the log.

On a new connection the server repeats `status`, the last `not_looking` if there was one, the last `hint` and `done`. If no sound of the answer to the last words has played
(`/played`), it also repeats `heard` and `say`, because that line may have gone to a dead socket.

`heard` means the server heard a phrase. On it the phone drops older lines and, with no answer in 1.5 seconds, says a waiting phrase.
After a reconnect the server can repeat `heard` and the `say` that has not sounded: the phone keeps the line it already has.
`read` means a small frame went to the model with no words read on it: the extension reads small frames only then, and the words are
needed to keep a payment screen out of the log. `say` carries the agent's line ahead of the outline: it goes out as soon as the model has written it, without waiting for the end of
the answer. `text_ru` can be empty if there is only `paste_text`: that is how ready text follows a line that has already gone out, as
a second `say` of the same turn. `quiet` means the model chose to stay silent. A screen turn does not change `turn`: a line about a
screen comes with the `turn` of the last words, or `0` if there were none yet.

`hint` carries the picture for the window: what is outlined, and a caption. `image_jpeg_base64` is always 720×960, and `frame_ts` equals
the `X-Frame-Ts` of the frame. If the person moved to another screen while the model was thinking, no outline goes out: the button is
gone. `left` means a new screen came while something on the old one was outlined: the window goes back to its card. A video on the
page does not count as a new screen. The voice does not wait for the outline, so when none goes out, the model is told so on its next
turn.

In a call with `eyes` the window has two states. With a hint it shows the picture with no eyes: a piece of the screen, the outline
and one short line, the first sentence as far as it fits. With no hint it shows the eyes and a word for what is really going on:
Слушаю, Смотрю, Думаю, Говорю, Я рядом, Всё ещё думаю after 12 seconds with no answer, Не смотрю. Смотрю and Думаю come only while
the person waits for an answer: a new screen nobody asked about leaves the window at rest. The phone takes these from real events, not
from the model. The hint event has one more field, `calm`: a payment, card or password screen, the eyes only rest.

A call with `eyes` has two more events. `reading`: a new screen came. `face`, at
the end of every answer: `{"type":"face","turn":3,"face":null,"calm":false}`. `face` is always `null`, and a build before 2 November
reads it as no face. `calm`: a payment, card or password screen. Such an event also comes ahead of the answer if payment words were
found in the text of a frame the server already has.

If `text_entry` is `clipboard`, the app puts `paste_text` on the clipboard as soon as it gets `say`. iOS lets a background app reach the
clipboard only while the window is open, so if the write failed, the app repeats it when the person comes back. With `keyboard`,
`paste_text` is `null`, and the keyboard takes the text from `/suggestions`.

A payment, card, password or code screen is not written to the log and does not go to the model again: the model says so in its
`private` field, or the words on the screen do, or its own words about a card or a payment. A line goes out before the end of the
answer, and the log decision is made only on the whole answer: if the answer breaks off after the line, neither the frame nor the
answer is logged. Screen text that comes after the answer removes both the frame and the answer line from the log. A small frame
whose words have not come by the end of its answer is logged with neither the picture nor the answer.

## App Group

`UserDefaults` keys of the group: `serverURL`, `token`, `sessionId`, `userLang`, `targetLang`, `language`, `windowFrame`, `agentManner`,
`ownCharacter`, `notLooking`. The app writes them, and the broadcast extension and the keyboard read them. `notLooking` is the last
`not_looking` event, and the extension checks it four times a second.
