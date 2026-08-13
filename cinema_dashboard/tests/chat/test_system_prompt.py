"""Tests for build_system_message — the assets/system_prompt.md template layer.

Deliberately *not* a full-text golden. The prompt is pinned prose that grows by
insertion (CLAUDE.md: insert new paragraphs, never reword or reflow old ones), so
a byte snapshot would have to be regenerated on every legitimate edit, which
trains people to update it unread. These assert the things a template can break
that prose cannot: an unsubstituted placeholder, a dropped value, a reordered
section, and a template file that has gone missing.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

from chat.prompt import _SYSTEM_PROMPT_PATH, ChatContext, build_system_message

#: The prompt's rules, in the order the model reads them. Order is load-bearing:
#: the ABSOLUTE RULE is written to be read first, and REFUSAL FLOW is defined
#: against the STYLE-ANCHOR exception stated above it.
#:
#: Anchored on the *full* heading, not the bare name, because several sections
#: cite each other in prose — "ABSOLUTE RULE" appears 3 times and "Known
#: theaters" twice, the latter inside THEATER LOOKUP *before* its own section,
#: so a bare-name search reports a false reordering.
SECTIONS = [
    "ABSOLUTE RULE — read first, applies to every response:",
    "STYLE-ANCHOR REQUESTS — when the user names",
    "REFUSAL FLOW — when the user asks FOR",
    "THEATER LOOKUP — the ONE exception",
    "TASTE & SHOWTIME TOOLS — two read-only tools",
    "STREAMING TOOL — a third read-only tool",
    "User taste profile (from their Letterboxd ratings history):",
    "These are the watchlist movies currently showing at their theaters:",
    "Known theaters (the only ones with showtimes data):",
    "Other rules:",
]


def _ctx(**overrides) -> ChatContext:
    base = {
        "taste": "TASTE-MARKER",
        "showtimes_md": "SHOWTIMES-MARKER",
        "streaming_md": "STREAMING-MARKER",
        "known_theaters": ["THEATER-MARKER"],
        "theaters_csv": None,
        "wl_shows": pd.DataFrame(),
        "wl_scored": pd.DataFrame(),
        "streaming_df": pd.DataFrame(),
        "slug_by_title": {},
        "n_movies": 0,
        "n_screenings": 0,
    }
    return ChatContext(**{**base, **overrides})


def test_every_placeholder_is_substituted():
    """A surviving ``$name`` would ship the literal token to the model."""
    content = build_system_message(_ctx())["content"]
    assert not re.search(r"\$\w+", content), "unsubstituted placeholder in the rendered prompt"


def test_all_four_values_reach_the_prompt():
    content = build_system_message(_ctx())["content"]
    for marker in ("TASTE-MARKER", "SHOWTIMES-MARKER", "STREAMING-MARKER", "THEATER-MARKER"):
        assert marker in content, f"{marker} was dropped"


def test_sections_appear_in_the_pinned_order():
    content = build_system_message(_ctx())["content"]
    positions = [content.find(s) for s in SECTIONS]
    assert all(p >= 0 for p in positions), [s for s, p in zip(SECTIONS, positions, strict=True) if p < 0]
    assert all(content.count(s) == 1 for s in SECTIONS), "a heading anchor is not unique"
    assert positions == sorted(positions), "the prompt's sections were reordered"


def test_role_is_system():
    assert build_system_message(_ctx())["role"] == "system"


def test_empty_streaming_renders_the_none_branch():
    """The if/else stays in Python; both arms must still land in the template slot."""
    content = build_system_message(_ctx(streaming_md=""))["content"]
    assert "FR streaming availability for watchlist films: NONE" in content
    assert "STREAMING-MARKER" not in content


def test_no_known_theaters_renders_none():
    assert "\nNone\n" in build_system_message(_ctx(known_theaters=[]))["content"]


def test_theaters_are_sorted_and_bulleted():
    content = build_system_message(_ctx(known_theaters=["mk2", "Le Champo"]))["content"]
    assert "- Le Champo\n- mk2" in content


# ── the template file itself ─────────────────────────────────────────────────


def test_template_file_ships_and_is_not_empty():
    """It is read at runtime, so an unshipped asset is a production failure."""
    assert _SYSTEM_PROMPT_PATH.exists(), f"missing {_SYSTEM_PROMPT_PATH}"
    assert _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def test_template_has_no_stray_dollar_signs():
    """``$`` is a metacharacter here: a literal one must be written ``$$``.

    Guards the next prose edit — a price or a ``$``-prefixed term added to the
    file would otherwise make ``.substitute`` raise at the first chat turn.
    """
    raw = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    known = {"$taste", "$showtimes_md", "$streaming_block", "$known_theaters"}
    assert set(re.findall(r"\$\w+|\$\$|\$", raw)) <= known, "unescaped $ in the template"


def test_the_files_trailing_newline_does_not_reach_the_prompt():
    """The file must end with a newline; the rendered prompt must not.

    ``end-of-file-fixer`` enforces the former on every text file in the repo, and
    it silently added one here — which drifted the prompt a byte from the string
    concatenation this replaced. `build_system_message` strips exactly one.
    """
    assert _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").endswith("\n")
    assert not build_system_message(_ctx())["content"].endswith("\n")


def test_a_missing_template_raises_rather_than_degrading(mocker, tmp_path):
    """The opposite of ``ui.theme.inject_css``, on purpose.

    Absent CSS costs styling; an absent system prompt silently ungrounds the
    model and voids the closed-set guarantee, so it must fail loudly.
    """
    mocker.patch("chat.prompt._SYSTEM_PROMPT_PATH", tmp_path / "gone.md")
    with pytest.raises(OSError):
        build_system_message(_ctx())
