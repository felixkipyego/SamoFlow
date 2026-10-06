# backend/app/ingest/chunking.py
# Task 2.4.c: chunking primitive + content_hash (Step 2.4 decision (c)/(e))
# -- shared logic, called by Step 2.6's adapters, not duplicated or
# rebuilt there. Both functions are pure over an ExtractedContent (2.4.b's
# own output): no network, no database, no Qdrant.
#
# Tokenizer: text-embedding-3-small's own tiktoken encoding, confirmed
# live via tiktoken.encoding_for_model() rather than assumed -- it is
# "cl100k_base". Built as a lazy @lru_cache singleton (same pattern as
# get_settings()/get_engine()/get_qdrant_client()): Encoding.__init__
# triggers a one-time network fetch of the encoding's merge-rank file
# (confirmed live: cl100k_base is NOT bundled in the tiktoken package
# itself -- tiktoken_ext.openai_public.cl100k_base() downloads it from
# openaipublic.blob.core.windows.net on first use, cached under the OS
# temp dir afterward) -- this must happen on first real CALL, never at
# module import, so merely importing this module never needs network.
#
# Block-boundary design (Step 2.4.c's own research question, decided):
# chunk WITHIN each ContentBlock independently -- NEVER merge text across
# a heading_path boundary into one chunk, even when that leaves a block's
# own final chunk under 512 tokens. The alternative (flowing continuously
# across blocks) would leave some chunks straddling two different
# heading_paths with no single correct answer for which one to report,
# directly undermining docs/SPEC.md's own "keeping the heading path" per
# chunk guarantee. Each chunk therefore inherits its own source block's
# heading_path exactly, with zero ambiguity. chunk_index still increments
# sequentially across the WHOLE document, not per block.
import hashlib
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from app.ingest.embedding import EMBEDDING_MODEL
from app.ingest.extract_html import ExtractedContent

# Step 2.4 decision (c): module constants, matching DENSE_VECTOR_SIZE's
# own precedent, not Settings fields -- nothing else needs either
# configurable yet.
MAX_CHUNK_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64


# Duplication check after 2.5.a/b/c: this used to be its own private
# _EMBEDDING_MODEL copy of the identical literal -- now imported from
# embedding.py (the module that owns this value, Task 2.5.a) instead of
# redeclared here. Two independent copies risked silent drift: changing
# the dense embedding model in one file without the other would leave
# this tokenizer picking the WRONG encoding for the real model. No
# circular import: embedding.py never imports chunking.py.
@lru_cache
def _get_encoding() -> tiktoken.Encoding:
    return tiktoken.encoding_for_model(EMBEDDING_MODEL)


@dataclass(frozen=True)
class Chunk:
    text: str
    heading_path: tuple[str, ...]
    chunk_index: int


def _token_windows(token_count: int) -> list[tuple[int, int]]:
    # Sliding window with a fixed per-window stride of (MAX_CHUNK_TOKENS -
    # CHUNK_OVERLAP_TOKENS): each window after the first starts exactly
    # CHUNK_OVERLAP_TOKENS tokens before the PREVIOUS window's own end, so
    # the overlap region is byte-for-byte the previous window's last
    # CHUNK_OVERLAP_TOKENS tokens == this window's first CHUNK_OVERLAP_
    # TOKENS tokens, by construction, not by coincidence. Stops the moment
    # a window reaches the end of the sequence -- never emits a further,
    # redundant trailing window. This also resolves the "short final
    # block" overlap edge case named in the task: a window's own length
    # is always (this window's own end) - (previous end - overlap), and
    # since end <= token_count always, the final window is NEVER shorter
    # than CHUNK_OVERLAP_TOKENS + 1 once a second window exists at all --
    # proved, not merely tested, by this formula. A block with fewer
    # tokens than MAX_CHUNK_TOKENS produces exactly one window spanning
    # everything, with no second window and therefore no overlap concept
    # to apply at all (test (c)'s own case).
    windows: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + MAX_CHUNK_TOKENS, token_count)
        windows.append((start, end))
        if end == token_count:
            break
        start = end - CHUNK_OVERLAP_TOKENS
    return windows


def chunk_text(content: ExtractedContent) -> list[Chunk]:
    # No special-casing for content.is_nearly_empty: that flag is a
    # signal for whichever ADAPTER calls this to decide whether a
    # document is even worth chunking/indexing at all -- not this
    # function's own job to enforce. An empty `blocks` tuple simply never
    # enters the loop below, naturally producing an empty chunk list with
    # no explicit rejection branch needed (test (f)).
    encoding = _get_encoding()
    chunks: list[Chunk] = []
    chunk_index = 0
    for block in content.blocks:
        tokens = encoding.encode(block.text)
        if not tokens:
            continue
        for start, end in _token_windows(len(tokens)):
            chunks.append(
                Chunk(
                    text=encoding.decode(tokens[start:end]),
                    heading_path=block.heading_path,
                    chunk_index=chunk_index,
                )
            )
            chunk_index += 1
    return chunks


def compute_content_hash(content: ExtractedContent) -> str:
    # Step 2.4 decision (e): hashes the NORMALIZED/EXTRACTED text (title +
    # every block's own text, in document order), never raw fetched
    # bytes -- so trivial source-side noise (whitespace/formatting
    # differences between tags that extract_html() never captures in the
    # first place) can't trigger a false "changed" detection. "\n" is a
    # fixed, explicit separator between parts -- avoids the ambiguous-
    # concatenation collision a bare "".join() could theoretically allow
    # (different block boundaries producing the identical joined string).
    # Deliberately nothing non-content-derived: no timestamps, no id()/
    # memory addresses -- purely a function of content.title/content.
    # blocks, which is exactly what makes this deterministic across runs.
    # SHA-256, matching this project's own established content-
    # fingerprinting default (app/auth/secrets.py, app/auth/tokens.py).
    parts = []
    if content.title is not None:
        parts.append(content.title)
    parts.extend(block.text for block in content.blocks)
    normalized_text = "\n".join(parts)
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
