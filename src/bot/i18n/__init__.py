from src.bot.i18n.messages import MESSAGES


def normalize_lang(lang: str | None) -> str:
    if not lang:
        return "ar"
    lang = str(lang).lower().strip()
    if lang.startswith("en"):
        return "en"
    return "ar"


def t(lang: str | None, key: str, **kwargs) -> str:
    code = normalize_lang(lang)
    table = MESSAGES.get(code) or MESSAGES["ar"]
    text = table.get(key) or MESSAGES["ar"].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
