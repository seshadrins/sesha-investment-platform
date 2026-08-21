from decimal import Decimal

import pandas as pd
import streamlit as st

from common import api_get, api_post, render_sidebar
from workbench_view import render_scope

render_sidebar()
st.title("Notional Portfolio")
st.caption("Simulated investing only. These actions never place, modify, or cancel a real order.")

portfolios = api_get("/notional-portfolios")
instruments = api_get("/instruments")
if not portfolios:
    st.subheader("Create a notional portfolio")
    instrument_options = {f"{item['exchange']}:{item['symbol']} — {item['company_name']}": item["id"]
                          for item in instruments}
    with st.form("create_notional"):
        name = st.text_input("Portfolio name", value="My Notional Portfolio")
        cash = st.number_input("Starting cash", min_value=1000.0, value=1000000.0, step=10000.0)
        max_weight = st.slider("Maximum position weight", 1, 100, 15) / 100
        brokerage = st.number_input("Transaction costs (%)", min_value=0.0, value=0.0, step=0.01) / 100
        tax = st.number_input("Simulated sell taxes (%)", min_value=0.0, value=0.0, step=0.01) / 100
        slippage = st.number_input("Simulated slippage (%)", min_value=0.0, value=0.0, step=0.01) / 100
        reinvest = st.checkbox("Automatically reinvest recorded dividends")
        benchmark = st.selectbox("Benchmark (optional)", ["None", *instrument_options])
        create = st.form_submit_button("Create portfolio", type="primary")
    if create:
        result = api_post("/notional-portfolios", json={"name": name, "starting_cash": cash,
            "currency": "INR", "max_position_weight": max_weight, "brokerage_pct": brokerage,
            "tax_pct": tax, "slippage_pct": slippage, "reinvest_dividends": reinvest,
            "benchmark_instrument_id": None if benchmark == "None" else instrument_options[benchmark]})
        if result: st.success("Notional portfolio created."); st.rerun()
    st.stop()

portfolio_options = {item["name"]: item for item in portfolios}
selected_meta = portfolio_options[st.selectbox("Portfolio", list(portfolio_options))]
portfolio_id = selected_meta["id"]
snapshot = api_get(f"/notional-portfolios/{portfolio_id}")
candidates = api_get("/notional-portfolios/candidates")
transactions = api_get(f"/notional-portfolios/{portfolio_id}/transactions")
performance = api_get(f"/notional-portfolios/{portfolio_id}/performance")
learning = api_get(f"/notional-portfolios/{portfolio_id}/learning")

metrics = st.columns(8)
metrics[0].metric("Total value", f"₹{snapshot['total_value']:,.2f}")
metrics[1].metric("Available cash", f"₹{snapshot['cash']:,.2f}")
metrics[2].metric("Invested", f"₹{snapshot['invested_value']:,.2f}")
metrics[3].metric("Total return", f"₹{snapshot['total_return']:,.2f}",
                  f"{snapshot['total_return_pct'] * 100:.2f}%" if snapshot["total_return_pct"] is not None else None)
metrics[4].metric("Realised P&L", f"₹{snapshot['realised_profit']:,.2f}")
metrics[5].metric("Max drawdown", f"{performance['max_drawdown'] * 100:.2f}%")
metrics[6].metric("Time-weighted", f"{performance['time_weighted_return'] * 100:.2f}%" if performance["time_weighted_return"] is not None else "—")
metrics[7].metric("Money-weighted", f"{performance['money_weighted_return'] * 100:.2f}%" if performance["money_weighted_return"] is not None else "—")

if snapshot["unpriced"]:
    st.warning("No stored valuation price for: " + ", ".join(snapshot["unpriced"]))
if snapshot["pending_orders"]:
    st.info(f"{len(snapshot['pending_orders'])} trade(s) await the next observable stored closing price.")

tabs = st.tabs([
    "Portfolio view", "Holdings & trade", "Add stock", "Pending & ledger",
    "Performance", "Learning", "Cash",
])

with tabs[0]:
    holdings = snapshot["holdings"]
    if not holdings:
        st.info("No notional holdings yet. Use Add stock to create a simulated buy.")
    else:
        st.caption(
            "Reuses the same Financial Analysis, Investor Style, Followed Investor, and "
            "Summary & Recommendation views as the main dashboard, scoped to this notional "
            "portfolio's current holdings."
        )
        workbench = api_get("/stock-workbench")
        owned_by_id = {row["instrument_id"]: row for row in workbench["owned"]}
        prospective_by_id = {row["instrument_id"]: row for row in workbench["prospective"]}

        # The notional portfolio's own simulated quantity/cost/value stand in for the real
        # account_positions override render_scope() already supports for the per-account
        # dashboard filter — same mechanism, different source of the position numbers.
        notional_positions = {}
        for item in holdings:
            cost_basis = (item["quantity"] or 0) * (item["average_cost"] or 0)
            notional_positions[item["instrument_id"]] = {
                "account_name": selected_meta["name"],
                "quantity": item["quantity"],
                "average_cost": item["average_cost"],
                "current_price": item["price"],
                "market_value": item["market_value"],
                "unrealised_profit": item["unrealised_profit"],
                "return_pct": (item["unrealised_profit"] / cost_basis) if cost_basis else None,
                "weight": item["weight"],
            }

        matched_owned = [owned_by_id[i] for i in notional_positions if i in owned_by_id]
        matched_prospective = [prospective_by_id[i] for i in notional_positions
                               if i in prospective_by_id and i not in owned_by_id]
        unmatched = [item for item in holdings
                     if item["instrument_id"] not in owned_by_id
                     and item["instrument_id"] not in prospective_by_id]

        if matched_owned:
            st.subheader("Owned-type holdings")
            render_scope(workbench, matched_owned, "OWNED", notional_positions)
        if matched_prospective:
            st.subheader("Prospective · Strong Buy holdings")
            render_scope(workbench, matched_prospective, "PROSPECTIVE")
        if unmatched:
            st.info(
                "No live evidence view for this holding; it no longer appears in Owned or "
                "Prospective: " + ", ".join(f"{item['stock']} — {item['company_name']}" for item in unmatched)
            )

with tabs[1]:
    holdings = snapshot["holdings"]
    if not holdings:
        st.info("No notional holdings yet. Use Add stock to create a simulated buy.")
    else:
        frame = pd.DataFrame([{"Stock": item["stock"], "Company": item["company_name"],
            "Quantity": item["quantity"], "Average cost": item["average_cost"],
            "Latest price": item["price"], "Price date": item["price_date"],
            "Market value": item["market_value"], "Unrealised P&L": item["unrealised_profit"],
            "Weight": item["weight"] * 100 if item["weight"] is not None else None} for item in holdings])
        st.dataframe(frame, width="stretch", hide_index=True, column_config={
            "Average cost": st.column_config.NumberColumn(format="₹%.2f"),
            "Latest price": st.column_config.NumberColumn(format="₹%.2f"),
            "Market value": st.column_config.NumberColumn(format="₹%.2f"),
            "Unrealised P&L": st.column_config.NumberColumn(format="₹%.2f"),
            "Weight": st.column_config.NumberColumn(format="%.2f%%"),
        })
        holding_options = {f"{item['stock']} — {item['company_name']}": item for item in holdings}
        with st.form("trade_holding"):
            label = st.selectbox("Holding", list(holding_options))
            action = st.radio("Action", ["BUY", "SELL"], horizontal=True,
                              format_func=lambda value: "Add More" if value == "BUY" else "Sell / Trim")
            quantity = st.number_input("Quantity", min_value=0.000001, value=1.0, format="%.6f")
            reason = st.text_area("Reason")
            trade = st.form_submit_button("Submit simulated trade", type="primary")
        if trade:
            item = holding_options[label]
            result = api_post(f"/notional-portfolios/{portfolio_id}/trades", json={
                "instrument_id": item["instrument_id"], "action": action,
                "quantity": quantity, "amount": None, "user_reason": reason or None})
            if result: st.success(f"Trade recorded with status {result['status']}."); st.rerun()

with tabs[2]:
    if not candidates:
        st.info("No owned or currently recommended stocks are eligible.")
    else:
        candidate_options = {f"[{item['scope']}] {item['stock']} — {item['company_name']} · {item['recommendation'].replace('_', ' ')}": item
                             for item in candidates}
        with st.form("add_stock"):
            label = st.selectbox("Owned or recommended stock", list(candidate_options))
            input_mode = st.radio("Size by", ["Investment amount", "Quantity"], horizontal=True)
            size = st.number_input(input_mode, min_value=0.01, value=10000.0 if input_mode == "Investment amount" else 1.0)
            reason = st.text_area("Why add this stock?")
            add = st.form_submit_button("Simulate Buy", type="primary")
        if add:
            item = candidate_options[label]
            body = {"instrument_id": item["instrument_id"], "action": "BUY",
                    "quantity": size if input_mode == "Quantity" else None,
                    "amount": size if input_mode == "Investment amount" else None,
                    "user_reason": reason or None}
            result = api_post(f"/notional-portfolios/{portfolio_id}/trades", json=body)
            if result: st.success(f"Buy recorded with status {result['status']}."); st.rerun()

with tabs[3]:
    if st.button("Settle against newly stored closes"):
        if api_post(f"/notional-portfolios/{portfolio_id}/settle"): st.rerun()
    if not transactions: st.info("No transactions recorded.")
    else:
        st.dataframe(pd.DataFrame([{"ID": item["id"], "Decision": item["decision_at"],
            "Stock": item["stock"], "Type": item["transaction_type"], "Status": item["status"],
            "Target price date": item["target_price_date"], "Execution date": item["execution_date"],
            "Quantity": item["quantity"], "Price": item["execution_price"], "Charges": item["charges"],
            "Reason": item["user_reason"], "Error": item["error"]} for item in transactions]),
            width="stretch", hide_index=True, column_config={
                "Price": st.column_config.NumberColumn(format="₹%.2f"),
                "Charges": st.column_config.NumberColumn(format="₹%.2f"),
            })
        with st.expander("Recommendation evidence retained with trades"):
            for item in transactions:
                if item["recommendation_snapshot"]:
                    st.markdown(f"**Transaction {item['id']} · {item['stock']}**")
                    st.json(item["recommendation_snapshot"])

with tabs[4]:
    history = pd.DataFrame(performance["rows"])
    if history.empty: st.info("Performance appears after a trade executes against a stored close.")
    else:
        series = ["value", "net_contributions"]
        if "benchmark_value" in history.columns: series.append("benchmark_value")
        st.line_chart(history.set_index("date")[series])
        st.dataframe(history, width="stretch", hide_index=True, column_config={
            column: st.column_config.NumberColumn(format="₹%.2f") for column in
            ("value", "net_contributions", "benchmark_value") if column in history.columns
        })
    for limitation in performance["limitations"]: st.caption(f"• {limitation}")

with tabs[5]:
    if not learning["outcomes"]:
        st.info("Recommendation outcomes appear after a recommendation-linked simulated buy executes.")
    else:
        outcome_rows = []
        for item in learning["outcomes"]:
            row = {"Transaction": item["transaction_id"], "Stock": item["stock"],
                "Recommendation": item["recommendation"], "Confidence": item["confidence_score"],
                "Cap segment": item["cap_segment"], "Regime": item["market_regime"],
                "Styles": ", ".join(item["styles"]),
                "Fundamental signal": item["fundamental_signal"],
                "Investor corroboration": item["investor_corroboration_count"]}
            for horizon in ("3m", "6m", "12m"):
                result = item["horizons"][horizon]
                row[f"{horizon} status"] = result["status"]
                for suffix, key in (
                    ("return", "forward_return"), ("relative", "benchmark_relative_return"),
                    ("MAE", "maximum_adverse_excursion"), ("MFE", "maximum_favourable_excursion"),
                ):
                    value = result.get(key)
                    row[f"{horizon} {suffix}"] = value * 100 if value is not None else None
            outcome_rows.append(row)
        outcome_df = pd.DataFrame(outcome_rows)
        percent_columns = [column for column in outcome_df.columns
                           if column.endswith((" return", " relative", " MAE", " MFE"))]
        st.dataframe(outcome_df, width="stretch", hide_index=True, column_config={
            column: st.column_config.NumberColumn(format="%.2f%%") for column in percent_columns
        })
        st.subheader("12-month confidence calibration")
        if learning["calibration_12m"]:
            st.dataframe(pd.DataFrame([{"Confidence bucket": key, **value}
                for key, value in learning["calibration_12m"].items()]), width="stretch", hide_index=True)
        else: st.info("No recommendation has reached its 12-month evaluation horizon.")
    for limitation in learning["limitations"]: st.caption(f"• {limitation}")

with tabs[6]:
    with st.form("cash_adjustment"):
        action = st.radio("Cash action", ["CASH_IN", "CASH_OUT", "DIVIDEND"], horizontal=True,
                          format_func=lambda value: value.replace("_", " ").title())
        amount = st.number_input("Amount", min_value=0.01, value=10000.0)
        reason = st.text_input("Reason")
        dividend_holding = None
        if action == "DIVIDEND" and selected_meta.get("reinvest_dividends") and snapshot["holdings"]:
            dividend_labels = {item["stock"]: item["instrument_id"] for item in snapshot["holdings"]}
            dividend_holding = dividend_labels[st.selectbox("Holding receiving dividend", list(dividend_labels))]
        submit_cash = st.form_submit_button("Record cash adjustment")
    if submit_cash:
        if api_post(f"/notional-portfolios/{portfolio_id}/cash", json={"action": action,
                "amount": amount, "user_reason": reason or None,
                "instrument_id": dividend_holding}): st.rerun()

st.warning("Simulated results may differ from executable prices, liquidity, taxes, and real portfolio constraints.")
