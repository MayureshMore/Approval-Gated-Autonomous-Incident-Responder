"""
The dashboard is Person B's file, but it renders Person A's event stream. These
tests guard the seam: if a new event kind lands and the dashboard has no icon,
no filter group, or no renderer for it, the operator finds out on stage.

Nothing here needs a browser. The dashboard is one self-contained HTML file, so
its maps and rules can be read straight out of the source.
"""
import os
import re
from html.parser import HTMLParser

import pytest

from agent.bus import KNOWN_KINDS

DASHBOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "ui", "dashboard.html")


@pytest.fixture(scope="module")
def html() -> str:
    # The dashboard is UTF-8 (emoji in the header and the step icons). Without an
    # explicit encoding, open() uses the locale codec, which is cp1252 on Windows
    # and raises UnicodeDecodeError before a single assertion runs.
    with open(DASHBOARD, encoding="utf-8") as f:
        return f.read()


def _js_map(html: str, name: str) -> set[str]:
    """Pull the keys out of a `const NAME = {...}` object literal."""
    m = re.search(rf"const {name}\s*=\s*\{{(.*?)\}};", html, re.S)
    assert m, f"{name} map not found in dashboard.html"
    return set(re.findall(r"(\w+)\s*:", m.group(1)))


# --- every event kind must be renderable -----------------------------------
def test_every_event_kind_has_an_icon(html):
    missing = KNOWN_KINDS - _js_map(html, "ICON")
    assert not missing, f"no timeline icon for {sorted(missing)}"


def test_every_event_kind_has_a_filter_group(html):
    """An ungrouped kind renders, but vanishes the moment a filter is on."""
    missing = KNOWN_KINDS - _js_map(html, "GROUP")
    assert not missing, f"no filter group for {sorted(missing)}"


def test_every_group_used_by_group_map_has_a_chip(html):
    groups = set(re.findall(r'data-filter="(\w+)"', html))
    used = set(re.findall(r':"(\w+)"', re.search(r"const GROUP\s*=\s*\{(.*?)\};",
                                                 html, re.S).group(1)))
    assert used <= groups, f"GROUP uses {sorted(used - groups)} with no chip to select it"


def test_every_event_kind_has_a_renderer(html):
    """Anything without an explicit branch hits `else continue` and never shows."""
    rendered = set(re.findall(r'e\.kind==="(\w+)"', html))
    missing = KNOWN_KINDS - rendered
    assert not missing, f"no timeline renderer for {sorted(missing)}"


# --- the regressions this file was written for -----------------------------
def test_the_timeline_actually_scrolls(html):
    """It had `tl.scrollTop = tl.scrollHeight` and no height rule, so the scroll
    was a no-op and the card grew until the approval buttons left the screen."""
    rule = re.search(r"#timeline\{([^}]*)\}", html)
    assert rule, "#timeline has no CSS rule at all"
    assert "overflow-y:auto" in rule.group(1)
    assert "max-height" in rule.group(1)


def test_the_rationale_survives_newlines_and_long_tokens(html):
    """Rationales are multi-line now (core.py's fallback chain). Without these
    the card collapses to one line or overflows the panel."""
    rule = re.search(r"\.reason\{([^}]*)\}", html).group(1)
    assert "pre-wrap" in rule
    assert "overflow-wrap:anywhere" in rule


def test_model_controlled_text_is_never_interpolated_raw(html):
    """Every ${...} that reaches innerHTML must go through escapeHtml.

    Scoped to the row-building loop specifically — that is the only region whose
    template literals become innerHTML. Elsewhere the same values are assigned
    with textContent, which is already safe, and flagging those would train
    whoever hits this into ignoring it.

    The agent writes the sandbox code and its own messages, so this is the
    difference between rendering a diagnostic and running one.
    """
    # Values that cannot carry attacker-controlled text: locals we built
    # ourselves, a lookup in our own icon table, and one ternary that yields a
    # string literal either way. Everything else must be escaped.
    SAFE = {"cls", "text", "evIdx", "i", "open",
            'e.approved?"Approved":"Rejected"'}
    body = html[html.index("const rows=[]"):html.index("counts.all=rows.length")]
    raw = [m for m in re.findall(r"\$\{([^}]*)\}", body)
           if "escapeHtml" not in m and "ICON[" not in m and m.strip() not in SAFE]
    assert not raw, f"unescaped interpolation into innerHTML: {raw}"


def test_approval_reason_is_set_as_text_not_html(html):
    """textContent, never innerHTML — the reason is model-authored."""
    assert re.search(r'getElementById\("ap-reason"\)\.textContent', html)
    assert not re.search(r'getElementById\("ap-reason"\)\.innerHTML', html)


# --- interaction wiring -----------------------------------------------------
def test_approve_and_reject_are_keyboard_reachable(html):
    assert 'k==="a"' in html and 'k==="r"' in html


def test_keyboard_cannot_approve_when_no_gate_is_open(html):
    """A stray keypress between runs must never authorise anything."""
    handler = html[html.index('document.addEventListener("keydown"'):]
    guard = handler.index("if(!pendingKey)return;")
    assert guard < handler.index('k==="a"'), "the a/r shortcuts are not behind the pendingKey guard"


def test_keyboard_shortcuts_do_not_hijack_typing(html):
    handler = html[html.index('document.addEventListener("keydown"'):]
    assert 'tagName==="INPUT"' in handler
    assert "metaKey" in handler and "ctrlKey" in handler


def test_the_page_is_well_formed(html):
    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.bad = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in ("meta", "br", "input", "img", "link", "hr"):
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
            elif tag in self.stack:
                self.bad.append(tag)
                self.stack.remove(tag)

    p = P()
    p.feed(html)
    assert not p.stack, f"unclosed tags: {p.stack}"
    assert not p.bad, f"mismatched tags: {p.bad}"


def test_it_stays_a_single_self_contained_file(html):
    """No CDN, no build step — it must open from disk on a dead network."""
    assert "src=\"http" not in html and "href=\"http" not in html
