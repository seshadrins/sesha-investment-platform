from urllib.parse import quote

import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label, render_sidebar

render_sidebar()
st.title("Financial Analysis")
st.caption("Evidence-based company quality, trends, valuation context, and governance review.")

instruments = api_get("/instruments?include_candidates=true")
portfolio = api_get("/portfolio")
prospectives = api_get("/prospective-stocks")
owned_ids = {item["instrument_id"] for item in portfolio["positions"]}
prospective_ids = {item["instrument_id"] for item in prospectives}

with st.expander("Research an individual stock"):
    st.write("Search Upstox and add a company to the research pool without recording a purchase. This does not place it in Prospective Stocks.")
    query = st.text_input("Company, symbol, or ISIN", placeholder="For example: TCS or INE467B01029")
    if st.button("Search Upstox", disabled=len(query.strip()) < 2):
        st.session_state["stock_search_results"] = api_get(f"/providers/upstox/search?q={quote(query.strip())}")
    results = st.session_state.get("stock_search_results", [])
    if results:
        result_map = {f"{x['exchange']}:{x['symbol']} — {x['company_name']} ({x['isin']})": x for x in results}
        selected_result = result_map[st.selectbox("Search results", result_map)]
        if st.button("Add for individual research", type="primary"):
            saved = api_post("/instruments", json={"exchange": selected_result["exchange"],
                "symbol": selected_result["symbol"], "company_name": selected_result["company_name"],
                "isin": selected_result["isin"], "sector": None, "industry": None})
            if saved:
                st.success("Company added to the research pool. It must pass the Strong Buy gate before appearing as prospective.")
                st.rerun()
    elif "stock_search_results" in st.session_state:
        st.info("No NSE/BSE equity instruments matched that search.")

if not instruments:
    st.info("Add or discover an instrument to begin.")
    st.stop()

scope = st.radio(
    "Analysis view",
    ["Owned stocks", "Prospective · Strong Buy", "Other research and candidates"],
    horizontal=True,
    help="NIFTY 500 candidates remain in the research pool unless they pass the Strong Buy screen.",
)
if scope == "Owned stocks":
    visible_instruments = [item for item in instruments if item["id"] in owned_ids]
elif scope == "Prospective · Strong Buy":
    visible_instruments = [item for item in instruments if item["id"] in prospective_ids]
else:
    visible_instruments = [item for item in instruments
                           if item["id"] not in owned_ids and item["id"] not in prospective_ids]

if not visible_instruments:
    messages = {
        "Owned stocks": "No currently owned stocks were found.",
        "Prospective · Strong Buy": "No NIFTY 500 candidate currently passes the Strong Buy gate.",
        "Other research and candidates": "No additional research candidates were found.",
    }
    st.info(messages[scope])
    st.stop()

if scope == "Prospective · Strong Buy":
    st.success("Every company in this view is non-owned and currently satisfies the Strong Buy gate.")
    with st.expander("Why these stocks qualified"):
        for item in prospectives:
            st.markdown(f"**{item['symbol']} · score {item['financial_score']}/100 · "
                        f"{item['style_matches']} style matches**")
            for reason in item["recommendation_reasons"]:
                st.write(f"• {reason}")

instrument_map = {instrument_label(item): item for item in visible_instruments}
labels = list(instrument_map)
default_index = next((index for index, label in enumerate(labels) if "VAIGLO" in label), 0)
selected = instrument_map[st.selectbox("Company to analyse", labels, index=default_index)]

c1, c2 = st.columns([1, 3])
refresh = c1.button("Refresh fundamentals", type="primary", disabled=not selected.get("isin"))
c2.caption(f"ISIN: {selected.get('isin') or 'Missing — edit the instrument first'} · Source: Upstox Analytics")
if refresh:
    with st.spinner("Fetching annual, quarterly, ratio, ownership, and corporate-action data…"):
        result = api_post(f"/providers/upstox/fundamentals/{selected['id']}/sync")
    if result:
        st.success(f"Refreshed {result['datasets_imported']} fundamental datasets.")
        if result["provider_errors"]:
            st.warning("Some optional Upstox datasets were unavailable: " + "; ".join(result["provider_errors"]))

analysis = api_get(f"/analysis/{selected['id']}")
if analysis["status"] == "MISSING_DATA":
    st.info("Fundamentals have not been synced for this company. Select **Refresh fundamentals**.")
    st.caption("Missing datasets: " + ", ".join(analysis["missing"]))
    st.stop()
if analysis["status"] == "SECTOR_SPECIFIC_REQUIRED":
    st.warning("This appears to be a bank, NBFC, or insurer. Industrial leverage and cash-conversion rules are not applied; review capital adequacy and asset-quality measures separately.")

st.caption(f"Consolidated statements · {analysis['units']} · Evidence refreshed {analysis['as_of']} · Scores are review aids, not forecasts.")
score_tab, trends_tab, valuation_tab, governance_tab, evidence_tab = st.tabs(
    ["Quality score", "Earnings trends", "Valuation", "Governance review", "Evidence & peers"]
)

with score_tab:
    score_cols = st.columns(5)
    score_cols[0].metric("Overall", analysis["overall_score"] if analysis["overall_score"] is not None else "N/A")
    for col, (name, value) in zip(score_cols[1:], analysis["scores"].items()):
        col.metric(name.replace("_", " ").title(), value if value is not None else "N/A")
    st.caption("Scores range from 0–100. N/A means unavailable or inappropriate for the sector.")
    rows = [{"Metric": name, "Company": values["company"], "Sector": values["sector"]}
            for name, values in analysis["ratios"].items()]
    st.subheader("Company versus sector")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    metrics = analysis["metrics"]
    st.subheader("Calculated operating metrics")
    st.dataframe(pd.DataFrame([{"Operating margin %": metrics["operating_margin_pct"],
        "Net margin %": metrics["net_margin_pct"], "Liabilities / assets": metrics["liabilities_to_assets"],
        "Operating cash / net profit": metrics["cash_conversion"]}]), width="stretch", hide_index=True)

with trends_tab:
    frequency = st.radio("Reporting frequency", ["Annual", "Quarterly"], horizontal=True)
    trends = analysis["annual_trends"] if frequency == "Annual" else analysis["quarterly_trends"]
    trend_rows = [{"Period": point["period"], "Metric": metric.replace("_", " ").title(),
                   "Value": point["value"], "Change": point["change"]}
                  for metric, history in trends.items() for point in history]
    trend_df = pd.DataFrame(trend_rows)
    if trend_df.empty:
        st.info("No trend series was returned for this frequency.")
    else:
        st.line_chart(trend_df.pivot_table(index="Period", columns="Metric", values="Value", aggfunc="first"))
        st.dataframe(trend_df, width="stretch", hide_index=True)

with valuation_tab:
    st.write("Valuation bands use dated snapshots. At least four observations are required before a range is labelled ready.")
    if not analysis["valuation"]:
        st.info("No valuation ratios were returned.")
    for metric, values in analysis["valuation"].items():
        with st.container(border=True):
            cols = st.columns(5)
            cols[0].metric(metric, f"{values['current']:.2f}")
            cols[1].metric("Sector", "N/A" if values["sector"] is None else f"{values['sector']:.2f}")
            cols[2].metric("Observed median", f"{values['median']:.2f}")
            cols[3].metric("Observed range", f"{values['min']:.2f}–{values['max']:.2f}")
            cols[4].metric("Observations", values["observations"])
            if values["band_status"] != "READY":
                st.caption("Building history—this is not yet a reliable valuation band.")

with governance_tab:
    st.warning(analysis["governance_scope"])
    if analysis["governance_flags"]:
        for flag in analysis["governance_flags"]:
            if flag["severity"] == "HIGH":
                st.error(f"{flag['severity']}: {flag['message']}")
            else:
                st.warning(f"{flag['severity']}: {flag['message']}")
    else:
        st.info("No automated warning was triggered. Manual governance review is still required.")

with evidence_tab:
    st.subheader("Stored evidence")
    st.write(", ".join(name.replace("_", " ").title() for name in analysis["available_datasets"]))
    if analysis["provider_errors"]:
        st.subheader("Unavailable from Upstox")
        for error in analysis["provider_errors"]:
            st.warning(error)
    st.subheader("Competitors")
    if analysis["competitors"]:
        st.dataframe(pd.DataFrame(analysis["competitors"]), width="stretch", hide_index=True)
    else:
        st.info("No competitor dataset is available from Upstox for this ISIN. No peers have been inferred or fabricated.")
