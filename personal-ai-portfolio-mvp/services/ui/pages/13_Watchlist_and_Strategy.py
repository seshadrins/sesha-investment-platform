import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label, render_sidebar

render_sidebar()
st.title("Watchlist & Strategy")
st.caption(
    "A mid-funnel watchlist for stocks you're tracking that don't (yet) pass the Strong Buy "
    "gate, and a single view of where new capital should go once you have some to deploy."
)

watch_tab, deploy_tab = st.tabs(["Watching", "Deploy new capital"])

with watch_tab:
    st.subheader("Add a stock to Watching")
    instruments = api_get("/instruments?include_candidates=true") or []
    instrument_options = {instrument_label(item): item["id"] for item in instruments}
    with st.form("add_to_watching"):
        label = st.selectbox("Stock", list(instrument_options))
        notes = st.text_area("Why are you tracking this?")
        add = st.form_submit_button("Add to Watching", type="primary")
    if add:
        result = api_post(f"/watchlist/{instrument_options[label]}/watch", json={"notes": notes or None})
        if result:
            st.success("Added to Watching."); st.rerun()

    st.subheader("Currently watching")
    watching = api_get("/watchlist/watching") or []
    if not watching:
        st.info("Nothing is being watched yet. Research a stock's Financial Analysis and "
                 "Investor Style Fit, then add it above to track it here.")
    else:
        frame = pd.DataFrame([{
            "Stock": f"{item['exchange']}:{item['symbol']}",
            "Company": item["company_name"],
            "Sector": item["sector"] or "Not classified",
            "Current rating": item["recommendation"].replace("_", " "),
            "Primary reason": item["recommendation_reasons"][0] if item["recommendation_reasons"] else "",
            "Financial score": item["financial_score"],
            "Style matches": item["style_matches"],
            "Added": item["added_at"][:10],
            "Notes": item["notes"] or "",
            "_instrument_id": item["instrument_id"],
        } for item in watching])
        st.dataframe(frame.drop(columns=["_instrument_id"]), width="stretch", hide_index=True,
            column_config={"Notes": st.column_config.TextColumn(width="large")})
        remove_options = {f"{item['Stock']} — {item['Company']}": item["_instrument_id"]
                          for item in frame.to_dict("records")}
        remove_label = st.selectbox("Stop watching", list(remove_options))
        if st.button("Remove from Watching"):
            if api_post(f"/watchlist/{remove_options[remove_label]}/archive"):
                st.success("Removed."); st.rerun()
        st.caption(
            "A stock that later passes the Strong Buy gate is shown here until it's promoted "
            "to Prospective on the main dashboard, or removed here."
        )

with deploy_tab:
    st.caption(
        "Combines rebalancing (trim candidates) and new-idea sourcing (Prospective Strong "
        "Buy + Watching) into one ranked view, scoped by deployable cash and the portfolio's "
        "current sector/cap-segment mix."
    )
    plan = api_get("/portfolio/deployment-plan")
    if plan:
        st.metric("Deployable cash across accounts", f"₹{plan['available_cash']:,.2f}",
            help="Set per account on Portfolio Setup.")

        st.subheader("Consider trimming")
        if not plan["trim_candidates"]:
            st.info("No owned position currently carries a TRIM/SELL/STRONG_SELL recommendation.")
        else:
            st.dataframe(pd.DataFrame([{
                "Stock": item["stock"], "Company": item["company_name"],
                "Recommendation": item["recommendation"].replace("_", " "),
                "Reason": item["reasons"][0] if item["reasons"] else "",
                "Market value": item["market_value"], "Weight": (item["weight"] or 0) * 100,
            } for item in plan["trim_candidates"]]), width="stretch", hide_index=True,
                column_config={
                    "Market value": st.column_config.NumberColumn(format="₹%.2f"),
                    "Weight": st.column_config.NumberColumn(format="%.2f%%"),
                })

        st.subheader("Consider adding")
        if not plan["buy_candidates"]:
            st.info("No Prospective Strong Buy or Watching stock is currently rated Buy or Strong Buy.")
        else:
            st.dataframe(pd.DataFrame([{
                "Stock": item["stock"], "Company": item["company_name"],
                "Sector": item["sector"] or "Not classified", "Source": item["tier"].replace("_", " ").title(),
                "Rating": item["recommendation"].replace("_", " "),
                "Financial score": item["financial_score"],
                "Reason": item["reasons"][0] if item["reasons"] else "",
            } for item in plan["buy_candidates"]]), width="stretch", hide_index=True)

        st.subheader("Current sector / cap-segment mix")
        div_cols = st.columns(2)
        with div_cols[0]:
            st.caption("By sector")
            sector_frame = pd.DataFrame(plan["diversification"]["by_sector"]).set_index("label")
            if not sector_frame.empty:
                st.bar_chart(sector_frame["weight"] * 100)
        with div_cols[1]:
            st.caption("By market-cap segment")
            cap_frame = pd.DataFrame(plan["diversification"]["by_cap_segment"]).set_index("label")
            if not cap_frame.empty:
                st.bar_chart(cap_frame["weight"] * 100)
        st.warning("Decision support only. Verify source data, suitability, valuation, and risk before acting.")

st.page_link("app.py", label="← Back to dashboard")
