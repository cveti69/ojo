"""One agent step: goal, history and a frame go in, the next action comes out as checked JSON."""
from __future__ import annotations

import json

from . import config, meta

ACTIONS = ("tap", "type", "scroll", "wait", "back", "none")
LANGUAGES = {"ru": "Russian", "id": "Indonesian", "en": "English", "zh": "Chinese", "vi": "Vietnamese", "th": "Thai",
             "auto": "the language shown on the screen"}
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
CHARACTERS = {
    "strict": "",
    "warm": "Your manner: warm and lively, like a friend sitting next to them. Cheer small wins naturally, without flattery.",
    "brief": "Your manner: as few words as possible. Only the action, no polite words and no explanations.",
    "patient": "Your manner: a patient teacher for someone who is afraid of phones. Plain words, and say where things are.",
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["screen", "instruction", "action", "target", "insert_texts", "insert_hint_ru",
                 "needs_user_confirm", "done"],
    "properties": {
        "screen": {"type": "string"},
        "instruction": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "target": {
            "type": "object",
            "additionalProperties": False,
            "required": ["label", "object", "box", "mark_id"],
            "properties": {
                "label": {"type": "string"},
                "object": {"type": "string"},
                "box": {"type": "array", "items": {"type": "integer"}},
                "mark_id": {"type": ["integer", "null"]},
            },
        },
        "insert_texts": {"type": "array", "items": {"type": "string"}},
        "insert_hint_ru": {"type": "string"},
        "needs_user_confirm": {"type": "boolean"},
        "done": {"type": "boolean"},
    },
}

PROMPT = """You are {app_name}, a calm guide who helps a person use an unfamiliar phone app.{manner}
You see one screenshot of their iPhone. Their goal: {goal}.
They speak {user_lang}; the app is in {target_lang}.
Steps done so far: {history}.
Decide the ONE next action the person should take on THIS screen.
Rules:
- One step at a time. Instruction in {user_lang}, max 12 words. Speak to the person as a friend: in Russian use "ты", for example "Нажми на поиск".
- Point at exactly one element to tap, or say what to do if no tap is needed (scroll, wait, go back).
- If they must type, give 1-3 ready texts in {target_lang} for that field, short and natural.
- Text in a field counts as entered only if the steps so far include typing it. Any other text there is left over from earlier. If the keyboard is open and nothing was typed yet, the next step is to type: set action to type, point at that field and give a fresh query in insert_texts. Do not tap the search button, search history or suggestions, even if they look related.
- Use none only when the goal is reached or nothing can be done yet.
- Never assume an option is already selected unless the screen clearly marks it, such as a filled radio button or a tick. An open list of options, such as delivery, stays open until the person picks one: do not close it. Point at the option that fits the goal, even if it looks like the current one. If the goal does not say which, point at the cheapest ordinary option and name it in the instruction.
- Never ask for passwords, PINs or card numbers. On payment or order confirmation set needs_user_confirm=true and let the person confirm themselves.
- The floating picture-in-picture window is our own hint: never point at it and do not read it. It can hide part of the screen. If the element you need is not visible and may be behind the window, set action to none, leave the box empty and ask the person to drag the window aside. Do not send them scrolling for it.
- If the screen is loading or unclear, say so and do not guess.
- Set done=true only if this screen itself shows the result of the goal, such as a placed order or a message visible as sent. The steps so far are not proof, and if the action is still offered on the screen, it is not done.
Answer with JSON only, matching the schema.
action is one of: tap, type, scroll, wait, back, none. Use tap for a back arrow too; "back" is only for going back when no arrow is visible.
target.label copies the visible text of the element exactly as shown on the screen, in the app's language. If it has no text, leave it empty.
target.object is a short English noun phrase for what the element looks like, such as "search bar", "green button", "heart icon", "plus icon", "checkbox".
target.box is [x1, y1, x2, y2] around the element, in 0-1000 coordinates from the top-left corner of the image: x is relative to the image width, y to the image height. Use an empty list when nothing should be tapped.
target.mark_id is null unless numbered marks are drawn on the image.
insert_hint_ru says in Russian what the ready text is, for example "запрос для поиска"."""

MARKS_RULE = """
The image has numbered red marks around pieces of text. If the element to tap is one of them, set target.mark_id to its number. If the element has no mark, such as an icon, leave mark_id null and give target.box."""


def manner(character: str, own_text: str = "") -> str:
    """The chosen tone, a line for the top of a prompt. The rules come after it and it cannot touch them."""
    if character == "own" and own_text.strip():
        words = json.dumps(own_text.strip(), ensure_ascii=False)
        return f"\nThe person asked you to behave like this: {words}. It sets only your tone, never the rules below."
    return "\n" + CHARACTERS[character] if CHARACTERS.get(character) else ""


def build_prompt(goal: str, history: list[str], user_lang: str = "ru",
                 target_lang: str = "id", marks: bool = False, character: str = "") -> str:
    text = PROMPT.format(
        app_name=config.APP_NAME,
        manner=character,
        goal=goal,
        user_lang=LANGUAGES.get(user_lang, user_lang),
        target_lang=LANGUAGES.get(target_lang, target_lang),
        history="; ".join(history) if history else "none",
    )
    return text + MARKS_RULE if marks else text


def check(answer: dict) -> list[str]:
    """Returns what is wrong with a parsed answer, empty when it is fine."""
    if not isinstance(answer, dict):
        return ["answer must be an object"]
    problems = []
    for key in SCHEMA["required"]:
        if key not in answer:
            problems.append(f"missing {key}")
    target = answer.get("target")
    if not isinstance(target, dict):
        problems.append("target must be an object")
    else:
        problems += [f"missing target.{key}" for key in SCHEMA["properties"]["target"]["required"] if key not in target]
    if problems:
        return problems
    if answer["action"] not in ACTIONS:
        problems.append("unknown action")
    box = target["box"] or []
    if box:
        if len(box) != 4 or not all(isinstance(value, (int, float)) for value in box):
            problems.append("box must be four numbers")
        elif not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000):
            problems.append("box must be ordered and within 0-1000")
    return problems


def ask(jpeg: bytes, prompt: str, model: str | None = None, effort: str | None = None) -> dict:
    """Calls the model, retries once on a bad answer. Returns {"answer", "ms", "usage", "tries"}."""
    model = model or config.META_VISION_MODEL
    effort = effort or config.META_REASONING
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": meta.data_url(jpeg)}},
    ]
    total_ms = 0
    usage: dict = {}
    problems: list[str] = []
    for tries in (1, 2):
        reply = meta.chat(model, [{"role": "user", "content": content}], effort, SCHEMA)
        total_ms += reply["ms"]
        usage = reply["usage"]
        try:
            answer = json.loads(reply["text"])
            problems = check(answer)
        except json.JSONDecodeError:
            answer, problems = None, ["not JSON"]
        if not problems:
            return {"answer": answer, "ms": total_ms, "usage": usage, "tries": tries}
        reminder = f"Your answer was rejected: {'; '.join(problems)}. Answer with JSON only, matching the schema."
        content = [{"type": "text", "text": f"{prompt}\n\n{reminder}"}, content[-1]]
    return {"answer": None, "ms": total_ms, "usage": usage, "tries": 2, "problems": problems}
