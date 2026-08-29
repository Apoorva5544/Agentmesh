import tiktoken


def encoding_for_model(model: str | None = None) -> tiktoken.Encoding:
    if model is None:
        return tiktoken.get_encoding("cl100k_base")
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str | None = None) -> int:
    try:
        return len(encoding_for_model(model).encode(text))
    except Exception:
        return approximate_token_count(text)


def approximate_token_count(text: str) -> int:
    if not text:
        return 0
    word_count = len(text.split())
    punctuation_count = sum(1 for char in text if char in ".,;:!?()[]{}")
    return max(1, int(word_count * 1.3) + punctuation_count)
