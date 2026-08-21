import pandas as pd
import streamlit as st

from common import api_get, api_post, api_put, render_sidebar

render_sidebar()
st.title("System Status")
st.caption(
    "Automation schedule, health, metrics, and run history — moved off the main dashboard "
    "so the stock tables there aren't pushed below the fold."
)

automation = api_get("/analysis-schedule")
run_history = api_get("/analysis-schedule/runs?limit=20")
automation_alerts = api_get("/notifications?category=AUTOMATION&limit=20", critical=False) or []
workbench = api_get("/stock-workbench")

snapshot = workbench["snapshot"]
schedule = snapshot["schedule"]
schedule_info, force_choice, force_action = st.columns([4, 2, 1])
schedule_info.caption(
    f"Cached analysis generated {snapshot['generated_at']} · scheduled {schedule['days']} at "
    f"{schedule['hour']:02d}:{schedule['minute']:02d} {schedule['timezone']}"
)
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

health = automation["health"]
if health["status"] in {"OVERDUE", "MISSED", "FAILED", "STALLED", "HEARTBEAT_STALE"}:
    latest_error = (health.get("latest_run") or {}).get("error")
    st.error(
        f"Scheduled analysis health: {health['status']}. Expected run: "
        f"{health['expected_scheduled_for']}. "
        + (f"Last error: {latest_error}. " if latest_error else "")
        + "The scheduler will attempt bounded recovery; use ‘Run now’ above "
          "if manual recovery is required."
    )
elif health["status"] == "DEGRADED":
    st.warning("The morning run completed partially. Failed activities are queued for bounded retry.")
elif health["status"] == "RUNNING":
    st.info("The scheduled morning analysis is currently running. The last snapshot remains available.")
else:
    st.success(f"Scheduled analysis health: {health['status']}.")

data_refresh = workbench.get("data_refresh", {})
if data_refresh:
    with st.expander("Today's data refresh details"):
        market_refresh = data_refresh.get("market_prices")
        if market_refresh:
            st.caption(
                f"Market refresh: {market_refresh['status']} · target {market_refresh.get('target_date')} · "
                f"{market_refresh.get('prices_imported', 0)} prices stored"
            )
            market_errors = market_refresh.get("errors") or (
                [market_refresh["error"]] if market_refresh.get("error") else []
            )
            for error in market_errors:
                st.write(f"• {error}")
        for key, label in (
            ("financial_statements", "Financial statements"),
            ("nifty500_screening", "NIFTY 500 screening"),
            ("nifty500_constituents", "NIFTY 500 constituents"),
            ("investor_disclosures", "Investor disclosures"),
        ):
            result = data_refresh.get(key)
            if result:
                detail = result.get("processed", result.get("constituents", ""))
                suffix = f" · {detail} processed" if detail != "" else ""
                if key == "investor_disclosures" and result.get("coverage_status"):
                    progress = result.get("coverage_progress", {})
                    suffix += (f" · coverage {result['coverage_status']} · "
                               f"{progress.get('checked_mappings', 0)}/{progress.get('active_mappings', 0)} mappings")
                st.caption(f"{label}: {result.get('status', 'UNKNOWN')}{suffix}")

with st.expander("Automation schedule and latest status", expanded=True):
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

with st.expander("Automation metrics and alerts", expanded=True):
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

with st.expander("Automation run history", expanded=True):
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

with st.expander("Portfolio risk settings", expanded=True):
    st.caption(
        "Portfolio-wide risk parameters applied identically to every stock, independent of "
        "any individual thesis. Read-only for now; editing these is a follow-up."
    )
    portfolio_settings = api_get("/settings")["portfolio_risk_settings"]
    percent_fields = {"max_position_weight", "trim_position_weight", "loss_review_threshold", "profit_review_threshold"}
    settings_rows = [{
        "Setting": item["name"],
        "Value": f"{item['value']:.0%}" if item["name"] in percent_fields else f"{item['value']}/100",
        "What it gates": item["description"],
    } for item in portfolio_settings]
    st.dataframe(pd.DataFrame(settings_rows), width="stretch", hide_index=True)

st.page_link("app.py", label="← Back to dashboard")
