"""Static contract for the mobile daily Model Picks results table.

The responsive change is presentation-only: the W/L calculations and scoring
stay on the production server, untouched.
"""

from pathlib import Path

from sports_edge_tennis.dashboard import production_app


_DASHBOARD_ROOT = Path(production_app.__file__).resolve().parent


def test_daily_model_picks_results_keeps_date_visible_on_mobile():
    template = (_DASHBOARD_ROOT / "templates" / "model_pick_results.html").read_text(encoding="utf-8")
    css = (_DASHBOARD_ROOT / "static" / "dashboard.css").read_text(encoding="utf-8")

    assert 'class="table-wrap model-picks-daily-scroll"' in template
    assert 'id="daily-results-scroll-hint"' in template
    assert 'aria-describedby="daily-results-scroll-hint"' in template
    assert 'tabindex="0"' in template
    assert "Swipe sideways for all columns. Dates stay visible." in template

    # Scope sticky styling strictly to the aligned daily table and small widths.
    assert "@media (max-width: 620px)" in css
    assert ".model-picks-daily-scroll table.data > tbody > tr > th:first-child" in css
    assert "position: sticky;" in css
    assert "left: 0;" in css
    assert ".model-picks-daily-scroll table.data > thead > tr > th:first-child" in css
