"""Split page texts into overlapping chunks that respect paragraphs and sentences."""

from __future__ import annotations

import re

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile(r"[ \t\f\v ]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)  # re-join words split across lines
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _MANY_NEWLINES.sub("\n\n", text).strip()


def _pieces(text: str, size: int) -> list[str]:
    """Paragraphs, further split into sentences, then hard-split, so no piece exceeds `size`."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            out.append(para)
            continue
        for sentence in _SENTENCE_END.split(para):
            while len(sentence) > size:
                cut = sentence.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                out.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                out.append(sentence)
    return out


def chunk_pages(pages: list[tuple[int | None, str]], size: int = 1200, overlap: int = 200) -> list[tuple[int | None, str]]:
    """Greedy packing of pieces into chunks of at most `size` characters.

    Each new chunk starts with the tail (about `overlap` characters) of the previous
    chunk on the same page, so ideas cut at a boundary stay retrievable.
    """
    if size < 100:
        raise ValueError("chunk size must be at least 100 characters")
    overlap = max(0, min(overlap, size // 2))
    chunks: list[tuple[int | None, str]] = []
    for page, raw in pages:
        current: list[str] = []
        length = 0
        for piece in _pieces(clean_text(raw), size):
            if current and length + len(piece) + 1 > size:
                chunks.append((page, "\n".join(current)))
                tail: list[str] = []
                tail_len = 0
                for prev in reversed(current):
                    if tail_len + len(prev) > overlap:
                        break
                    tail.insert(0, prev)
                    tail_len += len(prev) + 1
                current, length = tail, tail_len
            current.append(piece)
            length += len(piece) + 1
        if current:
            chunks.append((page, "\n".join(current)))
    return chunks
