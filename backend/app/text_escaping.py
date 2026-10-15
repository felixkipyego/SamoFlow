# backend/app/text_escaping.py
# Step 4.4: the shared markdown-unescape helper, built once per the
# Phase 4 kickoff decision #13 -- the escalation email body (this task)
# and the dashboard Inbox screen (Step 6.4, a later phase) both need the
# EXACT inverse of app/agent/graph.py's own `_sanitize_answer_text()`
# escape table, applied in the same fixed order every time: markdown-
# unescape FIRST, then HTML-escape SECOND, NEVER the reverse and NEVER
# only one of the two. A small, top-level, framework-agnostic module
# (matching app/ratelimit.py's own "no web/DB imports" layering) rather
# than living inside app/agent/ -- Step 6.4's dashboard code has no
# other reason to import anything from the chat-graph package, and this
# function has nothing graph-specific about it.
#
# WHY THIS EXISTS: `_sanitize_answer_text()` persists every AI answer
# with six characters backslash-escaped (\, !, [, ], <, >) so the
# ALREADY-RENDERED text can never be reinterpreted as live CommonMark
# when it is echoed back into a NEW prompt (Task 3.4.c/e's own "answer
# text is also untrusted input to a later turn" finding). A human
# reading that persisted text verbatim would see literal backslashes in
# front of ordinary punctuation (a cited URL would read as
# `\[text\]\(url\)`-shaped) -- this function undoes exactly that, and
# ONLY that, so a human-facing rendering (an email body, a dashboard
# screen) shows the clean original text instead of the escaped form.
#
# [SECURITY]: `markdown_unescape()` is for PLAIN-TEXT OUTPUT ONLY. Its
# own output still contains whatever literal `<`/`>` characters the
# model originally wrote (now unescaped) -- feeding that TEXT directly
# into an HTML document without a SEPARATE, SUBSEQUENT html.escape()
# call would let a model-written (or injected-into-context) `<script>`-
# shaped string execute as real markup. This module deliberately does
# NOT also provide an "unescape and HTML-escape" combined helper: every
# caller must perform both steps itself, in this fixed order, making
# the two-step shape visible at every call site rather than hidden
# inside a function name that could later be called from a plain-text
# context with the HTML-escape half silently skipped (or vice versa).
import re

# The exact inverse of app/agent/graph.py's own `_ANSWER_TEXT_ESCAPE_
# TABLE` -- NOT imported from there (that table is a private module
# constant of a stable, already-hardened file; duplicating six
# characters here, cross-referenced by comment both directions, is
# smaller and safer than widening that module's own public surface for
# one constant). Backslash followed by exactly one of these six
# characters is, by construction, the ONLY shape `_sanitize_answer_
# text()` ever produces for any of them (str.translate() maps each
# original character to a fixed 2-character escape, independently, in
# one pass -- never re-scanning its own output) -- so a single regex
# substitution, consuming each matched pair left to right, is a well-
# defined, order-independent inverse. Proven directly, not just
# reasoned about: see tests/test_text_escaping.py's own round-trip
# test, which feeds `_sanitize_answer_text()`'s real output (random
# adversarial strings, including runs of backslashes) through this
# function and asserts the original string comes back exactly.
_ESCAPED_PAIR = re.compile(r"\\([\\!\[\]<>])")


def markdown_unescape(text: str) -> str:
    """Inverts `app.agent.graph._sanitize_answer_text()`'s own escaping.

    For PLAIN-TEXT rendering only -- see this module's own header
    comment. Never call this on text that was NOT produced by
    `_sanitize_answer_text()` (e.g. a visitor's own raw message): there
    is nothing to invert, and a visitor who happens to type a literal
    backslash immediately before one of the six escaped characters
    (`C:\\Users\\!important`) would have that backslash silently
    stripped -- harmless for display purposes either way, but not this
    function's contract.
    """
    return _ESCAPED_PAIR.sub(r"\1", text)
