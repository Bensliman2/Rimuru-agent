"""Words, in any language.

People write to Rimuru in their own language and script. Everything that has to
find words in a text - what a memory is about, a folder's name, a skill's name,
whether a message changes course - goes through these, so none of it quietly
assumes English.
"""
from __future__ import annotations

import unicodedata

# Typed differently, meant the same. Dropped: accents and Arabic vowel marks and
# the elongation stroke (phone keyboards leave them out, and NFKD has already
# split them off their letters, so أ is ا here). Merged: the Arabic letters people
# use interchangeably (ى/ي, ة/ه, and the Persian ی/ک) and the curly apostrophe.
# The marks other scripts need to spell a word - Indic vowel signs, kana voicing -
# stay.
_SAME: dict[int, str | None] = {
    code: None
    for code in (*range(0x0300, 0x0370), *range(0x0610, 0x061B), *range(0x064B, 0x0660), 0x0670, 0x0640)
}
_SAME.update({0x0649: "ي", 0x0629: "ه", 0x06CC: "ي", 0x06A9: "ك", 0x2019: "'"})


def fold(text: str | None) -> str:
    """How a text compares, however it was typed: 'Café' and 'cafe' are the same,
    and so are an Arabic word with its vowel marks and without them."""
    decomposed = unicodedata.normalize("NFKD", text or "").translate(_SAME)
    return unicodedata.normalize("NFC", decomposed).casefold()


def words(text: str | None) -> list[str]:
    """The words in a text, in any script: runs of letters and digits, with the
    marks that belong to them. Everything else (spaces, punctuation, apostrophes,
    hyphens) separates them."""
    found: list[str] = []
    word: list[str] = []
    for char in text or "":
        if char.isalnum() or (word and unicodedata.category(char)[0] == "M"):
            word.append(char)
        elif word:
            found.append("".join(word))
            word.clear()
    if word:
        found.append("".join(word))
    return found


def name_words(text: str | None) -> list[str]:
    """Words for naming a folder: the person's own script and spelling, lower case."""
    return words(unicodedata.normalize("NFC", text or "").lower())
