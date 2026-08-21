import pandas as pd
import streamlit as st

from common import api_get, api_put, render_sidebar

render_sidebar()
st.title("System Status")
st.caption(
    "Automation schedule, health, metrics, and run history — moved off the main dashboard "
    "so the stock tables there aren't pushed below the fold."
)

automation = api_get("/analysis-schedule")
run_history = api_get("/analysis-schedule/runs?limit=20")
automation_alerts = api_get("/notifications?category=AUTOMATION&limit=20", critical=False) or []

health = automation["health"]
if health["status"] in {"OVERDUE", "MISSED", "FAILED", "STALLED", "HEARTBEAT_STALE"}:
    latest_error = (health.get("latest_run") or {}).get("error")
    st.error(
        f"Scheduled analysis health: {health['status']}. Expected run: "
        f"{health['expected_scheduled_for']}. "
        + (f"Last error: {latest_error}. " if latest_error else "")
        + "The scheduler will attempt bounded recovery; force a run from the main dashboard "
          "if manual recovery is required."
    )
elif health["status"] == "DEGRADED":
    st.warning("The morning run completed partially. Failed activities are queued for bounded retry.")
elif health["status"] == "RUNNING":
    st.info("The scheduled morning analysis is currently running. The last snapshot remains available.")
else:
    st.success(f"Scheduled analysis health: {health['status']}.")

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

st.page_link("app.py", label="← Back to dashboard")
