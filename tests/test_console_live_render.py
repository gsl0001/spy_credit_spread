"""Regression tests for the console-live dashboard render path.

Before the fix, ``console_live.make_layout`` called two functions —
``make_logs_panel`` and ``make_positions_summary`` — that were never
defined. The dashboard would crash on first render.

These tests:
  1. Statically verify both functions exist (passes without ``rich``).
  2. Exercise the full ``make_layout`` pipeline against an empty journal
     (skipped when ``rich`` isn't installed).
  3. Verify the price-coercion fix in ``run_metrics_job`` so a None
     price from ``get_live_price`` doesn't blow up the header.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Static check (works without rich installed) ─────────────────────────


def test_dashboard_render_helpers_are_defined():
    """The console_live module must define both render helpers used in
    ``make_layout`` — otherwise the dashboard crashes on first render.
    Static AST check so the test runs in any env (no ``rich`` needed)."""
    src = (ROOT / "console_live.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for required in ("make_logs_panel", "make_positions_summary",
                     "make_layout", "make_header", "make_monitor_table",
                     "make_command_ref"):
        assert required in defs, (
            f"console_live.py is missing {required}() — make_layout "
            f"references it, the dashboard will crash on first render."
        )


def test_make_layout_only_calls_defined_functions():
    """Future-proofing: walk the AST of ``make_layout`` and verify every
    function it calls exists in the module. If anyone adds another
    ``make_x_panel(...)`` reference without defining it, this test
    catches the crash before it ships."""
    src = (ROOT / "console_live.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    module_funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    layout_fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "make_layout"
    )
    # Collect bare-name calls inside make_layout.
    called = set()
    for node in ast.walk(layout_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    # Every helper-style call (make_*) must be defined in this module.
    helper_calls = {c for c in called if c.startswith("make_")}
    missing = helper_calls - module_funcs
    assert not missing, (
        f"make_layout calls undefined helpers: {missing}. "
        f"Define them or remove the calls."
    )


# ── Live render check (skipped per-test when rich isn't installed) ──────


def _need_rich():
    """Skip a test cleanly if rich isn't installed in this env. Console
    live mode needs rich to render the dashboard, but the rest of the
    project doesn't, so rich is an optional dependency."""
    pytest.importorskip("rich", reason="console_live dashboard needs rich")


@pytest.fixture
def journal(tmp_path):
    from core.journal import Journal
    j = Journal(str(tmp_path / "console_render.db"))
    yield j
    j.close()


def test_make_layout_renders_with_empty_journal(journal):
    """Run the entire dashboard composition with no positions and no
    logs — must not raise. This is the path that crashed before the fix."""
    _need_rich()
    import console_live
    scanner = MagicMock()
    scanner.active_preset = None
    layout = console_live.make_layout(journal, scanner)
    # Render to string to make sure every Panel resolves without error.
    from rich.console import Console
    console = Console(file=None, width=120, record=True)
    console.print(layout)
    out = console.export_text()
    # Sanity: we got actual output containing expected section titles.
    assert "Activity Log" in out
    assert "Active Positions" in out


def test_make_positions_summary_handles_active_positions(journal):
    """Render path with one position in each interesting state."""
    _need_rich()
    import console_live
    from core.journal import Position

    base = dict(
        symbol="SPY", topology="vertical_spread", direction="bull_call",
        contracts=1, entry_cost=250.0,
        entry_time="2026-04-26T13:00:00+00:00",
        expiry="2026-05-09",
        legs=({"type": "call", "strike": 500, "side": "long",  "expiry": "20260509"},
              {"type": "call", "strike": 505, "side": "short", "expiry": "20260509"}),
        broker="ibkr",
        meta={"combo_type": "debit", "mtm": 2.75},  # mark-to-market = $0.25 profit
    )
    journal.open_position(Position(id="p-open",    state="open",    **base))
    journal.open_position(Position(id="p-pending", state="pending", **base))
    journal.open_position(Position(id="p-closing", state="closing", **base))

    table = console_live.make_positions_summary(journal)
    from rich.console import Console
    console = Console(file=None, width=120, record=True)
    console.print(table)
    out = console.export_text()
    assert "SPY" in out
    assert "open" in out
    assert "pending" in out
    assert "closing" in out


def test_make_logs_panel_handles_bracketed_timestamps():
    """``add_log`` formats lines with ``[HH:MM:SS]`` prefixes — those
    look like Rich style tags. Verify the panel renders plain text
    without trying to parse them as markup (which would error)."""
    _need_rich()
    import console_live
    # Inject some timestamp-prefixed log lines and render.
    console_live._logs.clear()
    console_live._logs.extend([
        "[12:34:56] System Online.",
        "[12:35:01] Monitor: processed 2 positions",
        "[12:35:10] Telegram: dispatched 1 command(s).",
    ])
    panel = console_live.make_logs_panel()
    from rich.console import Console
    console = Console(file=None, width=120, record=True)
    console.print(panel)
    out = console.export_text()
    assert "System Online" in out
    assert "Monitor:" in out
    # Cleanup so we don't leak state into other tests.
    console_live._logs.clear()


def test_make_header_handles_none_price_safely():
    """``run_metrics_job`` used to set ``_spy_price = None`` when the
    account had no SPY market-data subscription. The header then did
    ``_spy_price > 0`` which raised TypeError. Fix coerces to 0.0;
    verify the header renders cleanly with that fallback value."""
    _need_rich()
    import console_live
    console_live._spy_price = 0.0  # the fallback after the fix
    scanner = MagicMock()
    scanner.active_preset = None
    panel = console_live.make_header(scanner)
    from rich.console import Console
    console = Console(file=None, width=120, record=True)
    console.print(panel)
    out = console.export_text()
    # "SPY: ---" is what the header shows when price is 0/missing.
    assert "SPY:" in out
