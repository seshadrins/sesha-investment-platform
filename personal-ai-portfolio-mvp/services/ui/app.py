import os

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title=os.getenv("APP_TITLE", "Personal AI Portfolio Manager"),
    page_icon="📈",
    layout="wide",
)

from common import api_get, api_post, api_put, render_sidebar

render_sidebar()
st.title("Personal AI Portfolio Manager")
st.caption("Review every stock across portfolio, data, financial, style, and followed-investor evidence.")

workbench = api_get("/stock-workbench")
automation = api_get("/analysis-schedule")
run_history = api_get("/analysis-schedule/runs?limit=20")
automation_alerts = api_get("/notifications?category=AUTOMATION&limit=20")
TABLE_HEIGHT = 650

snapshot = workbench["snapshot"]
schedule = snapshot["schedule"]
schedule_info, force_choice, force_action = st.columns([4, 2, 1])
schedule_info.caption(
    f"Cached analysis generated {snapshot['generated_at']} · scheduled {schedule['days']} at "
    f"{schedule['hour']:02d}:{schedule['minute']:02d} {schedule['timezone']}"
)
health = automation["health"]
if health["status"] in {"OVERDUE", "MISSED", "FAILED", "STALLED", "HEARTBEAT_STALE"}:
    latest_error = (health.get("latest_run") or {}).get("error")
    st.error(
        f"Scheduled analysis health: {health['status']}. Expected run: "
        f"{health['expected_scheduled_for']}. "
        + (f"Last error: {latest_error}. " if latest_error else "")
        + "The scheduler will attempt bounded recovery; use ‘All due morning activities’ "
          "and Run now if manual recovery is required."
    )
elif health["status"] == "DEGRADED":
    st.warning("The morning run completed partially. Failed activities are queued for bounded retry.")
elif health["status"] == "RUNNING":
    st.info("The scheduled morning analysis is currently running. The last snapshot remains available.")
force_options = {
    "All due morning activities": "morning",
    "Prices only": "prices",
    "Financial statements due": "fundamentals",
    "Next NIFTY 500 batch": "screening",
    "NIFTY constituents": "constituents",
    "Investor disclosure check": "disclosures",
    "Dashboard snapshot only": "snapshot",
}
selected_force = force_choice.selectbox(
    "Forced run scope", list(force_options), label_visibility="collapsed"
)
if force_action.button("Run now", type="secondary", width="stretch"):
    with st.spinner(f"Running {selected_force.lower()}…"):
        forced = api_post(
            f"/analysis-schedule/run?job={force_options[selected_force]}", timeout=900
        )
    if forced:
        st.success(
            f"Analysis refreshed for {forced['owned']} owned and "
            f"{forced['prospective']} prospective stocks."
        )
        st.rerun()

with st.expander("Automation schedule and latest status"):
    schedule_rows = [{
        "Activity": item["name"],
        "Frequency": item["frequency"],
        "Policy": item["policy"],
        "Last attempted": item["last_attempted_at"],
        "Status": item["last_status"],
        "Last error": item["last_error"],
    } for item in automation["jobs"]]
    st.dataframe(pd.DataFrame(schedule_rows), width="stretch", hide_index=True)
    st.caption(
        f"Health: {health['status']} · heartbeat {health['heartbeat_at'] or 'not received'} · "
        f"start grace {health['start_grace_minutes']} min · stall limit {health['stall_minutes']} min"
    )
    config = automation["schedule"]
    with st.form("edit_automation_schedule"):
        enabled = st.checkbox("Scheduled runs enabled", value=config["enabled"])
        edit_cols = st.columns(4)
        days = edit_cols[0].text_input("Days", value=config["days"], help="APScheduler syntax, e.g. tue-sat")
        hour = edit_cols[1].number_input("Hour", 0, 23, int(config["hour"]))
        minute = edit_cols[2].number_input("Minute", 0, 59, int(config["minute"]))
        timezone = edit_cols[3].text_input("Time zone", value=config["timezone"])
        save_schedule = st.form_submit_button("Save schedule")
    if save_schedule and api_put("/analysis-schedule", json={"enabled": enabled, "days": days,
            "hour": hour, "minute": minute, "timezone": timezone}):
        st.success("Schedule saved. The worker will apply it within 30 seconds."); st.rerun()

with st.expander("Automation metrics and alerts"):
    metrics = automation["metrics"]
    metric_cols = st.columns(5)
    metric_cols[0].metric("Runs (30d)", metrics["runs"])
    metric_cols[1].metric("Success rate", f"{metrics['success_rate_pct']:.1f}%" if metrics["success_rate_pct"] is not None else "—")
    metric_cols[2].metric("Recovery runs", metrics["recovery_runs"])
    metric_cols[3].metric("Median duration", f"{metrics['duration_seconds']['p50']:.1f}s" if metrics["duration_seconds"]["p50"] is not None else "—")
    metric_cols[4].metric("95th percentile", f"{metrics['duration_seconds']['p95']:.1f}s" if metrics["duration_seconds"]["p95"] is not None else "—")
    if metrics["activities"]:
        st.dataframe(pd.DataFrame([{"Activity": name, **values} for name, values in metrics["activities"].items()]), width="stretch", hide_index=True)
    if not automation_alerts:
        st.info("No automation alerts have been recorded.")
    for alert in automation_alerts:
        st.markdown(f"**{alert['title']}** — {alert['message']}")
        st.caption(f"{alert['created_at']} · {alert['severity']} · external delivery {alert['delivery_status']}")

with st.expander("Automation run history"):
    history_rows = [{
        "Scheduled for": item["scheduled_for"],
        "Trigger": item["trigger"].replace("_", " ").title(),
        "Attempt": item["attempt"],
        "Status": item["status"],
        "Started": item["started_at"],
        "Completed": item["completed_at"],
        "Actions": ", ".join(
            f"{name}: {status}" for name, status in item["action_status"].items()
        ),
        "Error": item["error"],
    } for item in run_history]
    if history_rows:
        st.dataframe(pd.DataFrame(history_rows), width="stretch", hide_index=True)
    else:
        st.info("No tracked scheduled or forced runs have been recorded yet.")
if snapshot["mode"] == "COLD_START":
    st.info("The scheduler had not produced its first snapshot, so an initial snapshot was generated once.")
if snapshot["last_status"] == "FAILED":
    st.warning(
        "The last scheduled refresh failed; the dashboard is showing the previous successful snapshot. "
        f"Error: {snapshot['last_error']}"
    )
market_refresh = workbench.get("data_refresh", {}).get("market_prices")
if market_refresh:
    st.caption(
        f"Market refresh: {market_refresh['status']} · target {market_refresh.get('target_date')} · "
        f"{market_refresh.get('prices_imported', 0)} prices stored"
    )
    market_errors = market_refresh.get("errors") or (
        [market_refresh["error"]] if market_refresh.get("error") else []
    )
    if market_errors:
        with st.expander(f"Market refresh messages ({len(market_errors)})"):
            for error in market_errors:
                st.write(f"• {error}")

for key, label in (
    ("financial_statements", "Financial statements"),
    ("nifty500_screening", "NIFTY 500 screening"),
    ("nifty500_constituents", "NIFTY 500 constituents"),
    ("investor_disclosures", "Investor disclosures"),
):
    result = workbench.get("data_refresh", {}).get(key)
    if result:
        detail = result.get("processed", result.get("constituents", ""))
        suffix = f" · {detail} processed" if detail != "" else ""
        if key == "investor_disclosures" and result.get("coverage_status"):
            progress = result.get("coverage_progress", {})
            suffix += (f" · coverage {result['coverage_status']} · "
                       f"{progress.get('checked_mappings', 0)}/{progress.get('active_mappings', 0)} mappings")
        st.caption(f"{label}: {result.get('status', 'UNKNOWN')}{suffix}")


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


def render_scope(rows, scope):
    if not rows:
        if scope == "OWNED":
            st.info("No owned stocks were found. Use Portfolio Setup to add opening holdings.")
        else:
            st.info(
                "No prospective stock currently passes the Strong Buy gate. "
                "Continue the NIFTY 500 screen in Styles & Screening."
            )
            st.page_link("pages/7_Investor_Styles.py", label="Open NIFTY 500 screening")
        return

    st.caption(
        "The same alphabetically ordered stock rows appear in every tab; no stock selection is required."
    )
    portfolio_tab, data_tab, financial_tab, style_tab, investor_tab, summary_tab = st.tabs([
        "Portfolio & Thesis", "Data & Freshness", "Financial Analysis",
        "Investor Style Fit", "Followed Investors", "Summary & Recommendation",
    ])

    with portfolio_tab:
        if scope == "OWNED":
            frame = pd.DataFrame([{
                "Stock": stock_name(row),
                "Company": row["company_name"],
                "Accounts": ", ".join(row["portfolio"]["accounts"]),
                "Quantity": row["portfolio"]["quantity"],
                "Average cost": row["portfolio"]["average_cost"],
                "Current price": row["portfolio"]["current_price"],
                "Market value": row["portfolio"]["market_value"],
                "Unrealised P&L": row["portfolio"]["unrealised_profit"],
                "Return": row["portfolio"]["return_pct"] * 100
                          if row["portfolio"]["return_pct"] is not None else None,
                "Weight": row["portfolio"]["weight"] * 100
                          if row["portfolio"]["weight"] is not None else None,
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
        render_table(frame)
        st.download_button(
            f"Download {scope.lower()} summary",
            frame.to_csv(index=False).encode("utf-8"),
            file_name=f"{scope.lower()}_stock_summary.csv",
            mime="text/csv",
        )
        st.warning("Decision support only. Verify source data, suitability, valuation, and risk before acting.")


owned_tab, prospective_tab = st.tabs([
    f"Owned stocks ({len(workbench['owned'])})",
    f"Prospective · Strong Buy ({len(workbench['prospective'])})",
])
with owned_tab:
    render_scope(workbench["owned"], "OWNED")
with prospective_tab:
    render_scope(workbench["prospective"], "PROSPECTIVE")

with st.expander("Manage data and workflows"):
    links = st.columns(5)
    links[0].page_link("pages/1_Portfolio_Setup.py", label="Portfolio Setup")
    links[1].page_link("pages/3_Prices.py", label="Data Sources & Sync")
    links[2].page_link("pages/6_Financial_Analysis.py", label="Financial Analysis")
    links[3].page_link("pages/7_Investor_Styles.py", label="Styles & Screening")
    links[4].page_link("pages/8_Followed_Investors.py", label="Followed Investors")
