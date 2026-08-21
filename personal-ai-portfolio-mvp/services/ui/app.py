import os

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title=os.getenv("APP_TITLE", "Personal AI Portfolio Manager"),
    page_icon="📈",
    layout="wide",
)

from common import api_get, api_post, render_sidebar
from workbench_view import render_scope

render_sidebar()
st.title("Personal AI Portfolio Manager")
st.caption("Review every stock across portfolio, data, financial, style, and followed-investor evidence.")

workbench = api_get("/stock-workbench")
# Non-critical: the automation health/schedule summary is secondary to the workbench data
# itself, so a failure here shows a warning and falls back gracefully instead of blanking
# the whole dashboard the workbench data already loaded fine for.
automation = api_get("/analysis-schedule", critical=False) or {}

# G5: the scheduler already recomputes every owned stock's recommendation on every run —
# this is what makes a change visible the moment the user next opens the dashboard, rather
# than only if they happen to notice it in the table.
recommendation_alerts = api_get(
    "/notifications?category=RECOMMENDATION&unread_only=true&limit=20", critical=False
) or []
if recommendation_alerts:
    with st.expander(
        f"🔔 {len(recommendation_alerts)} recommendation change(s) since your last visit",
        expanded=True,
    ):
        for alert in recommendation_alerts:
            st.markdown(f"**{alert['title']}**")
            st.caption(alert["message"])
        if st.button("Mark all as read"):
            for alert in recommendation_alerts:
                api_post(f"/notifications/{alert['id']}/read")
            st.rerun()

snapshot = workbench["snapshot"]

# Compact health banner: only rendered when something's actually wrong. Full schedule,
# metrics, and run history live on the System Status page so they don't push the stock
# tables below the fold.
health = automation.get("health")
if health and health["status"] in {"OVERDUE", "MISSED", "FAILED", "STALLED", "HEARTBEAT_STALE"}:
    latest_error = (health.get("latest_run") or {}).get("error")
    st.error(
        f"Scheduled analysis health: {health['status']}. Expected run: "
        f"{health['expected_scheduled_for']}. "
        + (f"Last error: {latest_error}. " if latest_error else "")
        + "The scheduler will attempt bounded recovery; use System Status → Run now "
          "if manual recovery is required."
    )
elif health and health["status"] == "DEGRADED":
    st.warning("The morning run completed partially. Failed activities are queued for bounded retry. "
               "Full history: System Status page.")
elif health and health["status"] == "RUNNING":
    st.info("The scheduled morning analysis is currently running. The last snapshot remains available.")

if snapshot["mode"] == "COLD_START":
    st.info("The scheduler had not produced its first snapshot, so an initial snapshot was generated once.")
if snapshot["last_status"] == "FAILED":
    st.warning(
        "The last scheduled refresh failed; the dashboard is showing the previous successful snapshot. "
        f"Error: {snapshot['last_error']}"
    )


portfolio_raw = api_get("/portfolio") or {}
all_positions = portfolio_raw.get("positions", [])
account_names = sorted({p["account_name"] for p in all_positions})

account_filter_col, sync_prices_col, _ = st.columns([2, 2, 2])
selected_account = account_filter_col.selectbox(
    "Account view", ["All accounts"] + account_names,
    help="Filter the dashboard to a single account's own holdings, cost, and weight.",
)
sync_prices_col.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
if sync_prices_col.button(
    "Sync prices only", width="stretch",
    help="Refresh stored closing prices for owned and Buy/Strong Buy prospective stocks "
         "without running the rest of the morning automation.",
):
    with st.spinner("Syncing prices…"):
        synced = api_post("/analysis-schedule/run?job=prices", timeout=900)
    if synced:
        st.success(
            f"Prices refreshed for {synced['owned']} owned and "
            f"{synced['prospective']} prospective stocks."
        )
        st.rerun()

owned_rows = workbench["owned"]
account_positions = None
if selected_account != "All accounts":
    filtered_positions = [p for p in all_positions if p["account_name"] == selected_account]
    account_total_value = sum(
        p["market_value"] for p in filtered_positions if p["market_value"] is not None
    )
    for p in filtered_positions:
        p["weight"] = (
            p["market_value"] / account_total_value
            if p["market_value"] is not None and account_total_value
            else None
        )
    account_positions = {p["instrument_id"]: p for p in filtered_positions}
    owned_rows = [row for row in workbench["owned"] if row["instrument_id"] in account_positions]
    summary = {
        "remaining_cost": sum(p["remaining_cost"] for p in filtered_positions),
        "market_value": sum(p["market_value"] for p in filtered_positions if p["market_value"] is not None),
        "unrealised_profit": sum(
            p["unrealised_profit"] for p in filtered_positions if p["unrealised_profit"] is not None
        ),
        "realised_profit": sum(p["realised_profit"] for p in filtered_positions),
        "dividend_income": sum(p["dividend_income"] for p in filtered_positions),
    }
    summary["total_profit"] = (
        summary["unrealised_profit"] + summary["realised_profit"] + summary["dividend_income"]
    )
else:
    summary = workbench.get("summary")

accounts = api_get("/accounts", critical=False) or []
deployable_cash = sum(float(item["cash_balance"]) for item in accounts)

if summary:
    summary_cols = st.columns(7)
    summary_cols[0].metric("Purchase cost", f"₹{summary['remaining_cost']:,.0f}")
    summary_cols[1].metric("Current value", f"₹{summary['market_value']:,.0f}")
    summary_cols[2].metric("Unrealised P&L", f"₹{summary['unrealised_profit']:,.0f}")
    summary_cols[3].metric("Realised P&L", f"₹{summary['realised_profit']:,.0f}")
    summary_cols[4].metric("Dividend income", f"₹{summary['dividend_income']:,.0f}")
    summary_cols[5].metric("Total profit", f"₹{summary['total_profit']:,.0f}")
    # G3: deployable cash has no in-app ledger — it's whatever was last set on Portfolio Setup.
    summary_cols[6].metric("Deployable cash", f"₹{deployable_cash:,.0f}",
        help="Set per account on Portfolio Setup. There is no cash transaction ledger.")
else:
    st.caption(
        "Total portfolio value isn't in the cached snapshot yet — use \"Dashboard snapshot "
        "only\" and Run now above to refresh it."
    )

with st.expander("📈 Performance history & diversification"):
    perf_tab, diversification_tab = st.tabs(["Performance history", "Sector & cap-segment mix"])
    with perf_tab:
        # G1: the real ledger's own return/drawdown/benchmark history — previously only the
        # simulated notional portfolio had this.
        performance = api_get("/portfolio/performance", critical=False)
        if not performance or not performance["rows"]:
            st.info("Performance history appears once at least one stored price date falls "
                     "on or after your earliest transaction.")
        else:
            perf_metrics = st.columns(3)
            perf_metrics[0].metric("Max drawdown", f"{performance['max_drawdown'] * 100:.2f}%")
            perf_metrics[1].metric("Time-weighted return",
                f"{performance['time_weighted_return'] * 100:.2f}%"
                if performance["time_weighted_return"] is not None else "—")
            perf_metrics[2].metric("Money-weighted return (XIRR)",
                f"{performance['money_weighted_return'] * 100:.2f}%"
                if performance["money_weighted_return"] is not None else "—")
            history = pd.DataFrame(performance["rows"])
            series = ["total_value", "net_contributions"]
            if "benchmark_value" in history.columns:
                series.append("benchmark_value")
            st.line_chart(history.set_index("date")[series])
            for limitation in performance["limitations"]:
                st.caption(f"• {limitation}")
    with diversification_tab:
        # G2: portfolio-level sector/cap-segment mix — per-stock weight caps alone can miss
        # concentration spread across several well-sized positions in the same sector.
        diversification = api_get("/portfolio/diversification", critical=False)
        if not diversification or not diversification["by_sector"]:
            st.info("No priced owned positions yet.")
        else:
            div_cols = st.columns(2)
            with div_cols[0]:
                st.caption("By sector")
                sector_frame = pd.DataFrame(diversification["by_sector"]).set_index("label")
                st.bar_chart(sector_frame["weight"] * 100)
            with div_cols[1]:
                st.caption("By market-cap segment")
                cap_frame = pd.DataFrame(diversification["by_cap_segment"]).set_index("label")
                st.bar_chart(cap_frame["weight"] * 100)

owned_tab, prospective_tab = st.tabs([
    f"Owned stocks ({len(owned_rows)})",
    f"Prospective · Buy & Strong Buy ({len(workbench['prospective'])})",
])
with owned_tab:
    render_scope(workbench, owned_rows, "OWNED", account_positions)
with prospective_tab:
    render_scope(workbench, workbench["prospective"], "PROSPECTIVE")

with st.expander("Manage data and workflows"):
    links = st.columns(7)
    links[0].page_link("pages/1_Portfolio_Setup.py", label="Portfolio Setup")
    links[1].page_link("pages/10_Prices.py", label="Data Sources & Sync")
    links[2].page_link("pages/4_Financial_Analysis.py", label="Financial Analysis")
    links[3].page_link("pages/5_Investor_Styles.py", label="Styles & Screening")
    links[4].page_link("pages/6_Followed_Investors.py", label="Followed Investors")
    links[5].page_link("pages/13_Watchlist_and_Strategy.py", label="Watchlist & Strategy")
    links[6].page_link("pages/12_System_Status.py", label="System Status")
