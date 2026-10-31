# Server

Python 3.9 to 3.14, one process: sessions live in memory. Run it from `backend/`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export SHARED_TOKEN='a token shared by the server and the phone'
export META_API_KEY='your Meta key'
export META_TEXT_MODEL='muse-spark-1.3'
export ELEVENLABS_API_KEY='your Eleven Labs key, without it the phone reads lines in its system voice'
.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8000 --workers 1
```

Pass secrets through the environment: the server does not read `.env`. The phone needs an HTTPS address through a proxy or a tunnel.
`GET /health` needs no token, every other request needs `X-Token`. The protocol is in [../docs/protocol.md](../docs/protocol.md).

The debug page is `/debug/SESSION_ID?token=SHARED_TOKEN` and refreshes once a second. It also accepts `X-Token`. Uvicorn's standard
logs hide the token in the query. In the logs of an outer HTTPS proxy that takes its own setting.

More settings:

| Variable | Default and purpose |
|---|---|
| `APP_NAME` | `OjO`, the only place for the product name, see `app/config.py` |
| `META_API_BASE` | `https://api.meta.ai/v1` |
| `META_TALK_MODEL` | `muse-wonder-1.0`, the model of the call |
| `META_TALK_REASONING` | `medium` |
| `META_SPARE_MODEL` | `muse-spark-1.3`, one try when the call's model gives no answer in 15 seconds |
| `META_TEXT_MODEL` | empty, translation for the keyboard needs one |
| `META_TRANSCRIBE_MODEL` | `muse-voice-transcribe-1.0` |
| `META_SAM_MODEL` | `sam-3.1` |
| `META_REASONING` | `minimal`, for translation and the bench |
| `META_VISION_MODEL` | empty, the bench's default model |
| `ELEVENLABS_MODEL` | `eleven_v4_turbo` |
| `ELEVENLABS_VOICE` | the id of the default voice, George |
| `ELEVENLABS_VOICES` | `id:name` pairs for the voice setting on the phone, separated by commas |
| `CALLS_DIR` | empty, no log is kept. A folder outside the repository for one log per call: what was heard, every answer, and the frame each answer saw. Folders are 0700, payment and password screens are not stored |
| `SESSION_TTL_SECONDS` | `3600`, time without requests before a session is removed |
| `SESSION_CLEANUP_SECONDS` | `30`, how often old sessions are removed |
| `MAX_FRAME_BYTES` / `MAX_FRAME_PIXELS` | `8388608` / `20000000` |
| `MAX_AUDIO_BYTES` | `16777216` |

Recognition of a file takes `audio/wav`: mono, 16 bit PCM, 16 kHz. An empty transcript comes back as `200 {"text":""}`. Recognition
behind an interface is in `app/voice.py`, the stream for a whole call is in `app/ears.py`, and the call loop is in `app/friend.py`.

`app/step.py` keeps the step format of the earlier agent. The bench in `eval/` still uses it, and the call takes only a few helpers from it.

Tests make no real requests to Meta:

```sh
.venv/bin/python -m pip install pytest httpx
.venv/bin/python -m pytest tests -q
```

On Python 3.9 the requirements pick Pillow 11.3, on 3.10 to 3.14 Pillow 12.1.
