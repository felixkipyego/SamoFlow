# backend/tests/test_ignore_files_guard.py
# Task 1.1.n: static checks on the repo-root .gitignore and backend/.dockerignore,
# so a future edit cannot silently stop ignoring secrets/caches, or start
# excluding a file the Dockerfile actually COPYs (which would break the
# image build). Line-based text checks only (no git shell-out, no docker
# build), matching test_makefile_guard.py's / test_ci_guard.py's style.
from tests.conftest import REPO_ROOT, is_comment_or_blank, read_lines

_GITIGNORE = REPO_ROOT / ".gitignore"
_DOCKERIGNORE = REPO_ROOT / "backend" / ".dockerignore"

# Files the Dockerfile actually COPYs from the build context (backend/):
# pyproject.toml, app/, entrypoint.sh, alembic.ini, alembic/. A .dockerignore
# pattern that would exclude any of these breaks the image build.
_DOCKERFILE_COPY_PATTERNS = ("app", "alembic", "alembic.ini", "pyproject.toml", "entrypoint.sh")


def _gitignore_lines():
    return read_lines(_GITIGNORE)


def _dockerignore_lines():
    return read_lines(_DOCKERIGNORE)


def test_gitignore_ignores_secrets_and_caches():
    lines = _gitignore_lines()
    required = [".env", "__pycache__/", ".venv/", ".pytest_cache/", ".ruff_cache/"]
    for pattern in required:
        assert pattern in lines, f".gitignore is missing required entry: {pattern!r}"


def test_gitignore_venv_pattern_covers_suffixed_venv_names():
    lines = _gitignore_lines()
    assert ".venv/" in lines or any(
        line.strip() in (".venv*/", ".venv*") for line in lines
    ), ".gitignore must ignore '.venv/' and also cover suffixed names like '.venv312/'"
    assert any(
        line.strip() in (".venv*/", ".venv*") for line in lines
    ), ".gitignore is missing a '.venv*/' pattern to cover suffixed venv names (e.g. .venv312)"


def test_env_example_negation_comes_after_env_wildcard():
    lines = _gitignore_lines()
    env_wildcard_index = next(
        (i for i, line in enumerate(lines) if line.strip() == ".env.*"), None
    )
    negation_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "!.env.example"), None
    )
    assert env_wildcard_index is not None, ".gitignore is missing a '.env.*' entry"
    assert negation_index is not None, ".gitignore is missing a '!.env.example' entry"
    assert negation_index > env_wildcard_index, (
        "'!.env.example' must come after '.env.*' in .gitignore: gitignore "
        "negations only take effect if listed after the rule they override, "
        f"got env_wildcard at line {env_wildcard_index + 1}, negation at "
        f"line {negation_index + 1}"
    )


def test_gitignore_does_not_ignore_env_example():
    # Checked textually (no git shell-out): .env.example must not appear as a
    # plain (non-negated) ignore pattern anywhere in the file.
    lines = _gitignore_lines()
    for line in lines:
        stripped = line.strip()
        assert stripped != ".env.example", (
            ".gitignore must not contain a bare '.env.example' ignore rule "
            "(.env.example must stay tracked)"
        )


def test_dockerignore_excludes_tests_secrets_and_vcs():
    lines = _dockerignore_lines()
    stripped = [line.strip() for line in lines]

    assert any(line.startswith("tests") for line in stripped), (
        "backend/.dockerignore must exclude tests/ (not needed at runtime, "
        "and the Dockerfile never COPYs it)"
    )
    assert any(line.startswith(".env") for line in stripped), (
        "backend/.dockerignore must exclude .env / .env.* (secrets must "
        "never enter the build context)"
    )
    assert ".git" in stripped, "backend/.dockerignore must exclude .git"
    assert any("__pycache__" in line for line in stripped), (
        "backend/.dockerignore must exclude __pycache__ (directly or via a "
        "'**/__pycache__' pattern)"
    )


def test_dockerignore_never_excludes_a_file_the_dockerfile_copies():
    lines = _dockerignore_lines()
    for raw_line in lines:
        line = raw_line.strip()
        if is_comment_or_blank(raw_line) or line.startswith("!"):
            continue
        # Strip a single trailing slash (directory-only patterns like "app/")
        # before comparing, so "app/" is recognized as matching "app".
        bare = line[:-1] if line.endswith("/") else line
        for copied in _DOCKERFILE_COPY_PATTERNS:
            # Exact match, or a wildcard pattern that would still match the
            # copied path (e.g. "*.ini" matching "alembic.ini", "alembic*"
            # matching "alembic"/"alembic.ini").
            matches_exact = bare == copied
            matches_suffix_wildcard = bare.startswith("*") and copied.endswith(bare[1:])
            matches_prefix_wildcard = bare.endswith("*") and copied.startswith(bare[:-1])
            if matches_exact or matches_suffix_wildcard or matches_prefix_wildcard:
                raise AssertionError(
                    f"backend/.dockerignore line {raw_line!r} would exclude "
                    f"{copied!r}, which backend/Dockerfile COPYs into the "
                    "image — this would break the build"
                )
