import os

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title=os.getenv("APP_TITLE", "Personal AI Portfolio Manager"),
    page_icon="📈",
    layout="wide",
)

from common import api_get, api_post, render_sidebar

render_sidebar()
st.title("Personal AI Portfolio Manager")
st.caption("Review every stock across portfolio, data, financial, style, and followed-investor evidence.")

workbench = api_get("/stock-workbench")
# Non-critical: the automation health/schedule summary is secondary to the workbench data
# itself, so a failure here shows a warning and falls back gracefully instead of blanking
# the whole dashboard the workbench data already loaded fine for.
automation = api_get("/analysis-schedule", critical=False) or {}
TABLE_HEIGHT = 650

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


def stock_name(row):
    return f"{row['exchange']}:{row['symbol']}"


def render_table(frame, column_config=None):
    if frame.empty:
        st.info("No rows are available for this view.")
        return
    st.dataframe(
        frame, width="stretch", height=TABLE_HEIGHT, hide_index=True,
        column_config=column_config or {},
    )
    st.caption(f"Showing {len(frame)} stocks. Scroll inside the table to see additional rows.")


def render_scope(rows, scope, account_positions=None):
    if not rows:
        if scope == "OWNED":
            st.info("No owned stocks were found. Use Portfolio Setup to add opening holdings.")
        else:
            st.info(
                "No prospective stock currently passes the Strong Buy gate. "
                "Continue the NIFTY 500 screen in Styles & Screening."
            )
            st.page_link("pages/5_Investor_Styles.py", label="Open NIFTY 500 screening")
        return

    st.caption(
        "The same stock rows appear in every tab (Owned is ordered by recommendation "
        "urgency — STRONG_SELL first; Prospective is alphabetical since every row there "
        "is already Strong Buy); no stock selection is required."
    )
    portfolio_tab, data_tab, financial_tab, style_tab, investor_tab, summary_tab = st.tabs([
        "Portfolio & Thesis", "Data & Freshness", "Financial Analysis",
        "Investor Style Fit", "Followed Investors", "Summary & Recommendation",
    ])

    with portfolio_tab:
        if scope == "OWNED":
            def portfolio_field(row, field):
                if account_positions is not None:
                    return account_positions.get(row["instrument_id"], {}).get(field)
                return row["portfolio"][field]

            frame = pd.DataFrame([{
                "Stock": stock_name(row),
                "Company": row["company_name"],
                "Accounts": ", ".join(row["portfolio"]["accounts"])
                            if account_positions is None
                            else account_positions.get(row["instrument_id"], {}).get("account_name", ""),
                "Quantity": portfolio_field(row, "quantity"),
                "Average cost": portfolio_field(row, "average_cost"),
                "Current price": portfolio_field(row, "current_price"),
                "Market value": portfolio_field(row, "market_value"),
                "Unrealised P&L": portfolio_field(row, "unrealised_profit"),
                "Return": (portfolio_field(row, "return_pct") or 0) * 100
                          if portfolio_field(row, "return_pct") is not None else None,
                "Weight": (portfolio_field(row, "weight") or 0) * 100
                          if portfolio_field(row, "weight") is not None else None,
                "Thesis": row["portfolio"]["thesis_status"] or "Not recorded",
                "Thesis summary": row["portfolio"]["thesis_reason"] or "Not recorded",
            } for row in rows])
            render_table(frame, {
                "Quantity": st.column_config.NumberColumn(format="%.4f"),
                "Average cost": st.column_config.NumberColumn(format="₹%.2f"),
                "Current price": st.column_config.NumberColumn(format="₹%.2f"),
                "Market value": st.column_config.NumberColumn(format="₹%.2f"),
                "Unrealised P&L": st.column_config.NumberColumn(format="₹%.2f"),
                "Return": st.column_config.NumberColumn(format="%.2f%%"),
                "Weight": st.column_config.NumberColumn(format="%.2f%%"),
                # Explicit widths for the free-text columns so the grid produces real
                # horizontal scroll instead of silently shrinking every column to fit —
                # without these, "Thesis summary" was pushed off-screen with no scrollbar.
                "Thesis": st.column_config.TextColumn(width="small"),
                "Thesis summary": st.column_config.TextColumn(width="large"),
            })
        else:
            frame = pd.DataFrame([{
                "Stock": stock_name(row),
                "Company": row["company_name"],
                "Sector": row["sector"] or "Not classified",
                "Shortlisted on": row["portfolio"]["shortlisted_on"],
                "Latest price": row["portfolio"]["current_price"],
                "Price date": row["portfolio"]["price_date"],
                "Thesis": row["portfolio"]["thesis_status"] or "Not recorded",
                "Thesis summary": row["portfolio"]["thesis_reason"] or "Not recorded",
            } for row in rows])
            render_table(frame, {
                "Latest price": st.column_config.NumberColumn(format="₹%.2f"),
                "Thesis": st.column_config.TextColumn(width="small"),
                "Thesis summary": st.column_config.TextColumn(width="large"),
            })

    with data_tab:
        frame = pd.DataFrame([{
            "Stock": stock_name(row),
            "Company": row["company_name"],
            "Stored datasets": row["data"]["dataset_count"],
            "Latest evidence": row["data"]["latest_evidence"],
            "Providers": ", ".join(row["data"]["providers"]) or "None",
            "Price": row["portfolio"]["current_price"],
            "Price date": row["portfolio"]["price_date"],
            "Provider errors": len(row["data"]["provider_errors"]),
            "Available evidence": ", ".join(
                item.replace("_", " ").title() for item in row["data"]["datasets"]
            ) or "None",
        } for row in rows])
        render_table(frame, {"Price": st.column_config.NumberColumn(format="₹%.2f")})

    with financial_tab:
        frame = pd.DataFrame([{
            "Stock": stock_name(row),
            "Company": row["company_name"],
            "Sector": row["sector"] or "Not classified",
            "Status": row["financials"]["status"].replace("_", " "),
            "As of": row["financials"]["as_of"],
            "Overall score": row["financials"]["overall_score"],
            "ROCE": row["financials"]["scores"].get("roce"),
            "ROE": row["financials"]["scores"].get("roe"),
            "Leverage": row["financials"]["scores"].get("leverage"),
            "Cash conversion": row["financials"]["scores"].get("cash_conversion"),
            "Governance flags": len(row["financials"]["governance_flags"]),
            "Missing evidence": ", ".join(
                item.replace("_", " ").title() for item in row["financials"]["missing"]
            ),
        } for row in rows])
        render_table(frame)

    with style_tab:
        style_rows = []
        for row in rows:
            item = {"Stock": stock_name(row), "Company": row["company_name"]}
            results = {result["style_id"]: result for result in row["styles"]}
            for definition in workbench["style_definitions"]:
                result = results.get(definition["id"])
                if not result:
                    value = "NO DATA"
                elif not result["applicable"]:
                    value = "NOT APPLICABLE"
                else:
                    state = "MATCH" if result["matches"] else "NO MATCH"
                    value = f"{state} · {result['score']:.0f}/100 · {result['coverage']:.0f}% evidence"
                item[f"{definition['name']} v{definition['version']}"] = value
            style_rows.append(item)
        render_table(pd.DataFrame(style_rows))

    with investor_tab:
        investor_rows = []
        for row in rows:
            item = {"Stock": stock_name(row), "Company": row["company_name"]}
            activity = {signal["investor_id"]: signal for signal in row["investors"]}
            for profile in workbench["investor_profiles"]:
                signal = activity.get(profile["id"])
                if not signal:
                    value = "—"
                else:
                    stale = " · STALE" if signal["stale"] else ""
                    value = (
                        f"{signal['signal'].replace('_', ' ')} · "
                        f"{signal['ownership_pct']:.2f}% · {signal['report_date']}{stale}"
                    )
                item[profile["name"]] = value
            investor_rows.append(item)
        render_table(pd.DataFrame(investor_rows))
        st.caption(
            "A blank cell means no attributable disclosure is stored. Investor activity is "
            "corroborating evidence and does not change the recommendation."
        )

    with summary_tab:
        frame = pd.DataFrame([{
            "Stock": stock_name(row),
            "Company": row["company_name"],
            "Recommendation": row["summary"]["recommendation"].replace("_", " "),
            "Brief rationale": row["summary"]["brief_reason"],
            "Financial score": row["financials"]["overall_score"],
            "Style matches": len(row["summary"]["style_matches"]),
            "Matched styles": ", ".join(row["summary"]["style_matches"]) or "None",
            "Governance flags": len(row["financials"]["governance_flags"]),
            "Investor signals": len(row["investors"]),
            "Financial evidence": row["financials"]["as_of"],
            "Price date": row["portfolio"]["price_date"],
        } for row in rows])
        if frame.empty:
            st.info("No rows are available for this view.")
        else:
            event = st.dataframe(
                frame, width="stretch", height=TABLE_HEIGHT, hide_index=True,
                on_select="rerun", selection_mode="single-row", key=f"summary_select_{scope}",
            )
            st.caption(
                f"Showing {len(frame)} stocks. Select a row, then jump to its Governance "
                "flags or Style Fit detail with that stock already selected."
            )
            selected_indices = event.selection.rows if event and event.selection else []
            if selected_indices:
                selected_row = rows[selected_indices[0]]
                jump_cols = st.columns([1, 1, 4])
                if jump_cols[0].button("Open Financial Analysis", key=f"jump_financial_{scope}"):
                    st.session_state["deep_link_symbol"] = stock_name(selected_row)
                    st.switch_page("pages/4_Financial_Analysis.py")
                if jump_cols[1].button("Open Investor Style Fit", key=f"jump_styles_{scope}"):
                    st.session_state["deep_link_symbol"] = stock_name(selected_row)
                    st.switch_page("pages/5_Investor_Styles.py")
        st.download_button(
            f"Download {scope.lower()} summary",
            frame.to_csv(index=False).encode("utf-8"),
            file_name=f"{scope.lower()}_stock_summary.csv",
            mime="text/csv",
        )
        st.warning("Decision support only. Verify source data, suitability, valuation, and risk before acting.")


portfolio_raw = api_get("/portfolio") or {}
all_positions = portfolio_raw.get("positions", [])
account_names = sorted({p["account_name"] for p in all_positions})

account_filter_col, _ = st.columns([2, 4])
selected_account = account_filter_col.selectbox(
    "Account view", ["All accounts"] + account_names,
    help="Filter the dashboard to a single account's own holdings, cost, and weight.",
)

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
    f"Prospective · Strong Buy ({len(workbench['prospective'])})",
])
with owned_tab:
    render_scope(owned_rows, "OWNED", account_positions)
with prospective_tab:
    render_scope(workbench["prospective"], "PROSPECTIVE")

with st.expander("Manage data and workflows"):
    links = st.columns(7)
    links[0].page_link("pages/1_Portfolio_Setup.py", label="Portfolio Setup")
    links[1].page_link("pages/10_Prices.py", label="Data Sources & Sync")
    links[2].page_link("pages/4_Financial_Analysis.py", label="Financial Analysis")
    links[3].page_link("pages/5_Investor_Styles.py", label="Styles & Screening")
    links[4].page_link("pages/6_Followed_Investors.py", label="Followed Investors")
    links[5].page_link("pages/13_Watchlist_and_Strategy.py", label="Watchlist & Strategy")
    links[6].page_link("pages/12_System_Status.py", label="System Status")
