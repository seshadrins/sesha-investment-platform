from datetime import date

import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label, render_sidebar

render_sidebar()
st.title("Investor Styles, Shortlist & Backtests")
st.caption("Version-controlled rules, a Buy/Strong Buy-only prospective shortlist, and point-in-time backtests.")

instruments = api_get("/instruments?include_candidates=true")
styles = api_get("/investor-styles")
portfolio = api_get("/portfolio")
prospectives = api_get("/prospective-stocks")
owned_ids = {item["instrument_id"] for item in portfolio["positions"]}
prospective_ids = {item["instrument_id"] for item in prospectives}

if not styles:
    st.error("No investor-style configuration was found.")
    st.stop()

focus_ids = owned_ids | prospective_ids
focus_instruments = [item for item in instruments if item["id"] in focus_ids]
analysis_instruments = focus_instruments or instruments

evidence_tab, screening_tab, config_tab, backtest_tab, matrix_tab = st.tabs([
    "Rule evidence", "NIFTY 500 screening", "Style configuration",
    "Point-in-time backtest", "Owned vs prospective",
])

with evidence_tab:
    if not analysis_instruments:
        st.info("Add a holding or run the screener before inspecting rule evidence.")
    else:
        instrument_map = {instrument_label(item): item for item in analysis_instruments}
        labels = list(instrument_map)
        # Consumed once: a deep link from the dashboard's Summary & Recommendation tab
        # pre-selects this stock (open this tab to see it applied).
        deep_link_symbol = st.session_state.pop("deep_link_symbol", None)
        default_index = next(
            (index for index, label in enumerate(labels) if deep_link_symbol and label.startswith(deep_link_symbol)),
            0,
        )
        selected = instrument_map[st.selectbox(
            "Company", labels, index=default_index, key="evidence_company"
        )]
        st.subheader(f"Rule evidence for {selected['symbol']}")
        evaluations = api_get(f"/investor-styles/evaluate/{selected['id']}")
        if not evaluations:
            st.info("Sync this company on Financial Analysis before evaluating styles.")
        for result in evaluations:
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns(4)
                c1.subheader(result["style_name"])
                c2.metric("Score", f"{result['score']:.0f}/100")
                c3.metric("Evidence coverage", f"{result['coverage']:.0f}%")
                c4.metric("Result", "MATCH" if result["matches"] else "NO MATCH")
                if not result["applicable"]:
                    st.warning("This style is not applicable to financial-sector companies.")
                rule_rows = [{
                    "Rule": rule["label"], "Observed": rule["actual"],
                    "Threshold": f"{rule['operator']} {rule['value']}",
                    "Result": "NO DATA" if rule["passed"] is None else "PASS" if rule["passed"] else "FAIL",
                    "Weight": rule["weight"],
                } for rule in result["rules"]]
                st.dataframe(pd.DataFrame(rule_rows), width="stretch", hide_index=True)

with screening_tab:
    universes = api_get("/screening-universes")
    universe = next((item for item in universes if item["id"] == "nifty500"), None)
    if not universe:
        st.error("The NIFTY 500 screening configuration is unavailable.")
    else:
        st.subheader("NIFTY 500 candidate funnel")
        st.write(universe["description"])
        counts = st.columns(6)
        counts[0].metric("Constituents", universe["constituents"])
        counts[1].metric("Large cap", universe["segments"]["LARGE"])
        counts[2].metric("Mid cap", universe["segments"]["MID"])
        counts[3].metric("Small cap", universe["segments"]["SMALL"])
        counts[4].metric("Screened this quarter", universe["screened_this_cycle"])
        counts[5].metric("Strong Buys", universe["strong_buys_this_cycle"])
        st.caption(
            f"Constituents as of {universe['constituents_as_of'] or 'not loaded'} · "
            f"Financial evidence cycle {universe['cycle_started_on']} · "
            f"{universe['pending_this_cycle']} non-owned candidates remain. "
            "Owned stocks are excluded from prospective screening."
        )

        col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
        batch_size = col4.number_input("Companies per batch", min_value=1, max_value=50, value=10,
                                       help="A batch is interleaved across Large, Mid, and Small cap.")
        if col1.button("Refresh constituents", type="secondary"):
            with st.spinner("Downloading the official index constituent files…"):
                refreshed = api_post("/screening-universes/nifty500/refresh", timeout=180)
            if refreshed:
                st.success(
                    f"Loaded {refreshed['constituents']} constituents: "
                    f"{refreshed['segments']['LARGE']} Large, {refreshed['segments']['MID']} Mid, "
                    f"and {refreshed['segments']['SMALL']} Small cap."
                )
                st.rerun()
        if col2.button("Screen next batch", type="primary",
                       disabled=universe["constituents"] == 0 or universe["pending_this_cycle"] == 0):
            with st.spinner("Syncing Upstox fundamentals and applying every configured rule…"):
                screened = api_post(
                    f"/screening-universes/nifty500/screen?batch_size={int(batch_size)}", timeout=600
                )
            if screened:
                promoted = sum(item["recommendation"] in {"STRONG_BUY", "BUY"} for item in screened["results"])
                st.success(
                    f"Screened {screened['processed']} companies; {promoted} entered Prospective Stocks. "
                    f"{screened['remaining']} remain in the quarterly cycle."
                )
                st.rerun()
        if col3.button(
            "Reconcile prospective", type="secondary",
            help="Catch up companies already screened as Buy or Strong Buy before that "
                 "criteria applied, so they were never promoted. No external calls.",
        ):
            with st.spinner("Re-checking stored screening results against current criteria…"):
                reconciled = api_post("/screening-universes/nifty500/reconcile", timeout=60)
            if reconciled:
                st.success(
                    f"Checked {reconciled['checked']} screened companies; "
                    f"{reconciled['promoted']} newly entered Prospective Stocks."
                )
                st.rerun()

        results = api_get("/screening-universes/nifty500/results")
        st.subheader("Latest screening audit")
        st.caption("Every evaluated candidate is retained here; only Buy and Strong Buy results enter Prospective Stocks.")
        if not results:
            st.info("Refresh constituents, then screen a batch to create the first audit results.")
        else:
            result_df = pd.DataFrame([{
                "Segment": item["cap_segment"].title(),
                "Stock": f"{item['exchange']}:{item['symbol']}",
                "Company": item["company_name"],
                "Result": item["recommendation"].replace("_", " "),
                "Financial score": item["financial_score"],
                "Style matches": item["style_matches"],
                "Analysis status": item["analysis_status"].replace("_", " "),
                "Why": " ".join(item["reasons"]),
                "Screened": item["screened_on"],
            } for item in results])
            st.dataframe(result_df, width="stretch", hide_index=True, height=480)
            st.download_button("Download screening audit", result_df.to_csv(index=False).encode("utf-8"),
                               file_name="nifty500_screening_audit.csv", mime="text/csv")

with config_tab:
    st.write("Styles live in version-controlled YAML files. Increment the version after changing a threshold.")
    style_map = {style["name"]: style for style in styles}
    style = style_map[st.selectbox("Style", style_map, key="config_style")]
    c1, c2, c3 = st.columns(3)
    c1.metric("Config version", style["version"])
    c2.metric("Minimum score", style["minimum_score"])
    c3.metric("Minimum coverage", f"{style['minimum_coverage']}%")
    st.write(style["description"])
    st.caption(f"File: {style['file']} · Applicability: {style['applicability']}")
    st.dataframe(pd.DataFrame(style["rules"]), width="stretch", hide_index=True)

with backtest_tab:
    if not analysis_instruments:
        st.info("Add a holding or run the screener before backtesting.")
    else:
        instrument_map = {instrument_label(item): item for item in analysis_instruments}
        selected = instrument_map[st.selectbox("Company", instrument_map, key="backtest_company")]
        style_map = {style["name"]: style for style in styles}
        selected_style = style_map[st.selectbox("Investor style", style_map, key="backtest_style")]
        c1, c2, c3 = st.columns(3)
        from_date = c1.date_input("Price history from", value=date(2022, 1, 1))
        horizon = c2.selectbox("Forward-return horizon", [180, 365, 730], index=1,
                               format_func=lambda days: f"{days} days")
        c3.caption("Fundamentals use a conservative 120-day reporting lag.")
        if st.button("Sync prices and run backtest", type="primary"):
            with st.spinner("Syncing historical closes and evaluating point-in-time decisions…"):
                synced = api_post(
                    f"/providers/upstox/history/{selected['id']}/sync?"
                    f"from_date={from_date.isoformat()}&to_date={date.today().isoformat()}", timeout=300
                )
                if synced:
                    st.session_state["style_backtest"] = api_get(
                        f"/backtests/{selected['id']}/{selected_style['id']}?horizon_days={horizon}"
                    )
                    st.success(f"Stored {synced['prices_imported']} daily prices and completed the backtest.")

        result = st.session_state.get("style_backtest")
        if result and result["instrument_id"] == selected["id"] and result["style"]["id"] == selected_style["id"]:
            summary = result["summary"]
            cols = st.columns(4)
            cols[0].metric("Historical signals", summary["signals"])
            cols[1].metric("Completed signals", summary["completed_signals"])
            cols[2].metric("Average forward return", "N/A" if summary["average_forward_return"] is None
                           else f"{summary['average_forward_return']:.2%}")
            cols[3].metric("Positive-return rate", "N/A" if summary["positive_return_rate"] is None
                           else f"{summary['positive_return_rate']:.1%}")
            decisions = pd.DataFrame([{
                **{key: row[key] for key in ("period", "decision_date", "score", "coverage",
                    "signal", "entry_date", "entry_price", "exit_date", "exit_price")},
                "forward_return_pct": (
                    row["forward_return"] * 100 if row["forward_return"] is not None else None
                ),
            } for row in result["decisions"]])
            st.dataframe(decisions, width="stretch", hide_index=True,
                         column_config={"forward_return_pct": st.column_config.NumberColumn(
                             "Forward return", format="%.2f%%")})
            with st.expander("Methodology and limitations", expanded=True):
                methodology = result["methodology"]
                st.write(
                    f"Reporting lag: {methodology['reporting_lag_days']} days · "
                    f"Horizon: {methodology['horizon_days']} days · "
                    f"Price tolerance: {methodology['price_tolerance_days']} days · "
                    f"Point-in-time: {'Yes' if methodology['point_in_time'] else 'No'}"
                )
                for limitation in result["limitations"]:
                    st.write(f"• {limitation}")


def matrix_frame(rows, style_definitions):
    output = []
    for row in rows:
        display = {
            "Stock": f"{row['exchange']}:{row['symbol']}",
            "Company": row["company_name"],
            "Sector": row["sector"] or "Not classified",
        }
        for style in style_definitions:
            cell = row["styles"][style["id"]]
            if cell["status"] in {"MATCH", "NO_MATCH"}:
                value = f"{cell['status'].replace('_', ' ')} · {cell['score']:.0f}"
            else:
                value = cell["status"].replace("_", " ")
            display[f"{style['name']} v{style['version']}"] = value
        output.append(display)
    return pd.DataFrame(output)


with matrix_tab:
    matrix = api_get("/investor-styles/matrix")
    owned_rows = [row for row in matrix["rows"] if row["universe"] == "OWNED"]
    prospective_rows = [row for row in matrix["rows"] if row["universe"] == "PROSPECTIVE"]
    owned_view, prospective_view = st.tabs([
        f"Owned stocks ({len(owned_rows)})", f"Prospective · Buy & Strong Buy ({len(prospective_rows)})"
    ])
    with owned_view:
        st.caption("Portfolio recommendations include Buy More, Hold, Review, Trim, Sell, or Strong Sell based on portfolio and thesis rules.")
        owned_df = matrix_frame(owned_rows, matrix["styles"])
        if owned_df.empty:
            st.info("No currently owned stocks were found.")
        else:
            st.dataframe(owned_df, width="stretch", hide_index=True, height=440)
            st.download_button("Download owned matrix", owned_df.to_csv(index=False).encode("utf-8"),
                               file_name="owned_stock_style_matrix.csv", mime="text/csv")
    with prospective_view:
        st.caption("Only non-owned companies that currently satisfy the Buy or Strong Buy gate appear here.")
        if not prospectives:
            st.info("No Buy or Strong Buy candidate has passed the NIFTY 500 screen yet.")
        else:
            recommendation_df = pd.DataFrame([{
                "Stock": f"{item['exchange']}:{item['symbol']}",
                "Company": item["company_name"],
                "Recommendation": item["recommendation"].replace("_", " "),
                "Financial score": item["financial_score"],
                "Style matches": item["style_matches"],
                "Why": " ".join(item["recommendation_reasons"]),
            } for item in prospectives])
            st.dataframe(recommendation_df, width="stretch", hide_index=True)
            prospective_df = matrix_frame(prospective_rows, matrix["styles"])
            st.dataframe(prospective_df, width="stretch", hide_index=True, height=440)

st.warning("Research support only. Strong Buy is a rules-based screen, not personalised investment advice.")
