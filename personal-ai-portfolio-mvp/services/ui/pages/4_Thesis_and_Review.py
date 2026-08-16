from datetime import date

import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label, render_sidebar

render_sidebar()
st.title("Thesis & Review")
st.caption("Keep your original reasoning, invalidation tests, external research, and decisions together.")

instruments = api_get("/instruments")
snapshot = api_get("/portfolio")
if not instruments:
    st.info("Add an instrument under Transactions first.")
    st.stop()

instrument_by_label = {instrument_label(i): i for i in instruments}
selected = instrument_by_label[st.selectbox("Instrument", instrument_by_label)]
position = next((p for p in snapshot["positions"] if p["instrument_id"] == selected["id"]), None)
research = api_get(f"/research/{selected['id']}")

summary_tab, thesis_tab, journal_tab = st.tabs(["Review summary", "Investment thesis", "Decision journal"])

with summary_tab:
    st.subheader(f"{selected['symbol']} — {selected['company_name']}")
    st.caption(f"{selected['exchange']} · ISIN: {selected.get('isin') or 'Missing'} · Sector: {selected.get('sector') or 'Not classified'}")
    if position:
        cols = st.columns(4)
        cols[0].metric("Review prompt", position["recommendation"].replace("_", " "))
        cols[1].metric("Market value", f"₹{(position['market_value'] or 0):,.2f}")
        cols[2].metric("Portfolio weight", f"{position['weight']:.1%}")
        return_text = "No price" if position["return_pct"] is None else f"{position['return_pct']:.2%}"
        cols[3].metric("Unrealised return", return_text)
        st.markdown("**Why this prompt was generated**")
        for reason in position["recommendation_reasons"]:
            st.write(f"• {reason}")
    else:
        st.info("This instrument is not currently held; you can still maintain a research thesis.")

    profile = next((row for row in research if row["research_type"] == "COMPANY_PROFILE"), None)
    st.markdown("**Company research**")
    if profile:
        st.write(profile["payload"].get("company_profile") or "No profile text returned.")
        st.caption(f"Source: {profile['provider']} · Refreshed: {profile['as_of']}")
    else:
        st.info("No company profile stored. Use Upstox sync on the Prices page.")

with thesis_tab:
    current = api_get(f"/theses/{selected['id']}") or {}
    versions = api_get(f"/theses/{selected['id']}/history")
    with st.form("thesis_form"):
        status_options = ["ACTIVE", "WATCH", "INVALID"]
        status = st.selectbox("Thesis status", status_options,
            index=status_options.index(current.get("status", "ACTIVE")),
            help="INVALID triggers a STRONG SELL prompt; WATCH triggers a REVIEW prompt.")
        reason = st.text_area("Why I own or may own this company", value=current.get("reason", ""),
                              placeholder="Business quality, valuation, and expected source of return")
        catalysts = st.text_area("Expected catalysts", value=current.get("catalysts", ""))
        risks = st.text_area("Key risks", value=current.get("risks", ""))
        invalidation = st.text_area("Observable conditions that invalidate the thesis",
                                    value=current.get("invalidation_conditions", ""))
        horizon = st.number_input("Target horizon (months)", min_value=1, max_value=120,
                                  value=int(current.get("target_horizon_months", 12)))
        if st.form_submit_button("Save thesis", type="primary"):
            result = api_post("/theses", json={"instrument_id": selected["id"], "status": status,
                "reason": reason, "catalysts": catalysts, "risks": risks,
                "invalidation_conditions": invalidation, "target_horizon_months": horizon})
            if result:
                st.success(f"Thesis version {result['version']} saved.")
    if versions:
        with st.expander(f"Version history ({len(versions)})"):
            for version in versions:
                st.markdown(f"**Version {version['version']} · {version['status']} · {version['created_at'][:10]}**")
                st.write(version["reason"] or "No ownership rationale recorded.")
                st.caption("Invalidation conditions: " + (version["invalidation_conditions"] or "Not recorded"))

with journal_tab:
    if position:
        with st.form("journal_form"):
            decision_date = st.date_input("Decision date", value=date.today())
            user_decision = st.selectbox("Your decision", ["PENDING", "ACCEPT", "REJECT", "DEFER"])
            notes = st.text_area("Decision notes", placeholder="What did you decide, and why?")
            if st.form_submit_button("Add immutable journal entry", type="primary"):
                result = api_post("/decisions", json={"instrument_id": selected["id"],
                    "decision_date": decision_date.isoformat(), "recommendation": position["recommendation"],
                    "rationale": "\n".join(position["recommendation_reasons"]),
                    "user_decision": user_decision, "notes": notes or None})
                if result:
                    st.success("Decision journal entry saved.")
    decisions = [row for row in api_get("/decisions") if row["symbol"] == selected["symbol"]
                 and row["exchange"] == selected["exchange"]]
    if decisions:
        st.dataframe(pd.DataFrame(decisions), width="stretch", hide_index=True)
    else:
        st.info("No decisions recorded for this instrument.")
