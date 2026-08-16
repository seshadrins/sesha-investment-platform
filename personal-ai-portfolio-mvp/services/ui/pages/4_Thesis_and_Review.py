from datetime import date
import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label

st.title("Thesis & Review")
instruments = api_get("/instruments")
snapshot = api_get("/portfolio")

if not instruments:
    st.info("Add instruments first.")
    st.stop()

instrument_by_label = {instrument_label(i): i for i in instruments}
selected_label = st.selectbox("Instrument", instrument_by_label)
selected = instrument_by_label[selected_label]
current = api_get(f"/theses/{selected['id']}") or {}

with st.form("thesis_form"):
    status_options = ["ACTIVE", "WATCH", "INVALID"]
    status = st.selectbox(
        "Thesis status",
        status_options,
        index=status_options.index(current.get("status", "ACTIVE")),
    )
    reason = st.text_area("Why I own / may own this stock", value=current.get("reason", ""))
    catalysts = st.text_area("Expected catalysts", value=current.get("catalysts", ""))
    risks = st.text_area("Risks", value=current.get("risks", ""))
    invalidation = st.text_area(
        "Conditions that invalidate the thesis",
        value=current.get("invalidation_conditions", ""),
    )
    horizon = st.number_input(
        "Target horizon in months",
        min_value=1,
        max_value=120,
        value=int(current.get("target_horizon_months", 12)),
    )
    if st.form_submit_button("Save thesis"):
        result = api_post(
            "/theses",
            json={
                "instrument_id": selected["id"],
                "status": status,
                "reason": reason,
                "catalysts": catalysts,
                "risks": risks,
                "invalidation_conditions": invalidation,
                "target_horizon_months": horizon,
            },
        )
        if result:
            st.success("Thesis saved.")

st.divider()
st.subheader("Current recommendation")
position = next(
    (p for p in snapshot["positions"] if p["instrument_id"] == selected["id"]), None
)
if position:
    st.metric("Recommendation", position["recommendation"])
    for reason in position["recommendation_reasons"]:
        st.write(f"• {reason}")

    with st.form("journal_form"):
        user_decision = st.selectbox(
            "Your decision", ["PENDING", "ACCEPT", "REJECT", "DEFER"]
        )
        notes = st.text_area("Decision notes")
        if st.form_submit_button("Add to decision journal"):
            result = api_post(
                "/decisions",
                json={
                    "instrument_id": selected["id"],
                    "decision_date": date.today().isoformat(),
                    "recommendation": position["recommendation"],
                    "rationale": "\n".join(position["recommendation_reasons"]),
                    "user_decision": user_decision,
                    "notes": notes or None,
                },
            )
            if result:
                st.success("Decision journal entry saved.")
else:
    st.info("This instrument is not currently held.")

st.divider()
st.subheader("Decision journal")
st.dataframe(pd.DataFrame(api_get("/decisions")), use_container_width=True, hide_index=True)
