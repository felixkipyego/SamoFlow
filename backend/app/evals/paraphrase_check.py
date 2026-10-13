# backend/app/evals/paraphrase_check.py
# Task 3.7.c: a machine check for this project's own item-writing rule
# ("questions paraphrase the corpus's own wording rather than quoting
# it" -- evals/golden_set/items.yaml's own header comment) -- claimed
# there, now actually checked here, not merely asserted in prose.
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    return [match.group().lower() for match in _WORD_RE.finditer(text)]


@dataclass(frozen=True)
class SharedWordRun:
    length: int
    words: tuple[str, ...]


def longest_shared_word_run(question: str, passage: str) -> SharedWordRun:
    """The length (in whole words, case-insensitive, punctuation-
    stripped) of the longest CONTIGUOUS word sequence that appears, in
    the same order, in both `question` and `passage` -- a classic
    longest-common-substring over token sequences, computed with a
    simple O(n*m) DP table (both inputs are short -- one question, one
    evidence passage capped at 300 characters -- so this never needs to
    scale further).
    """
    question_tokens = _tokenize(question)
    passage_tokens = _tokenize(passage)
    if not question_tokens or not passage_tokens:
        return SharedWordRun(length=0, words=())

    previous_row = [0] * (len(passage_tokens) + 1)
    best_length = 0
    best_end = 0
    for i in range(1, len(question_tokens) + 1):
        current_row = [0] * (len(passage_tokens) + 1)
        for j in range(1, len(passage_tokens) + 1):
            if question_tokens[i - 1] == passage_tokens[j - 1]:
                current_row[j] = previous_row[j - 1] + 1
                if current_row[j] > best_length:
                    best_length = current_row[j]
                    best_end = i
        previous_row = current_row

    words = tuple(question_tokens[best_end - best_length : best_end])
    return SharedWordRun(length=best_length, words=words)
