import nltk

try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)


def segment(raw_chunk: str) -> list[str]:
    """Split a Whisper chunk into sentences using NLTK Punkt tokenizer."""
    text = raw_chunk.strip()
    if not text:
        return []
    return nltk.sent_tokenize(text)
