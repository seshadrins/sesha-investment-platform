import os
from datetime import date

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title=os.getenv("APP_TITLE", "Personal AI Portfolio Manager"),
    page_icon="📈",
    layout="wide",
)

from common import api_get, render_sidebar

render_sidebar()
st.title("Portfolio Dashboard")
st.caption("Consolidated holdings, returns, allocation, and transparent review prompts")

snapshot = api_get("/portfolio")
positions = snapshot["positions"]

if not positions:
    st.info("Your portfolio is empty. Open **Portfolio Setup** to create an account and import holdings.")
else:
    accounts = sorted({item["account_name"] for item in positions})
    selected_account = st.selectbox("Portfolio view", ["All accounts", *accounts])
    filtered = positions if selected_account == "All accounts" else [
        item for item in positions if item["account_name"] == selected_account
    ]

    market_value = sum(item["market_value"] or 0 for item in filtered)
    remaining_cost = sum(item["remaining_cost"] or 0 for item in filtered)
    unrealised = sum(item["unrealised_profit"] or 0 for item in filtered)
    realised = sum(item["realised_profit"] or 0 for item in filtered)
    dividends = sum(item.get("dividend_income") or 0 for item in filtered)
    total_profit = unrealised + realised + dividends
    total_return = total_profit / remaining_cost * 100 if remaining_cost else 0
    unpriced = [item for item in filtered if item["current_price"] is None]

    top = st.columns(5)
    top[0].metric("Market value", f"₹{market_value:,.2f}")
    top[1].metric("Open cost", f"₹{remaining_cost:,.2f}")
    top[2].metric("Total P&L", f"₹{total_profit:,.2f}", f"{total_return:,.2f}%")
    top[3].metric("Realised + dividends", f"₹{realised + dividends:,.2f}")
    top[4].metric("Open positions", len(filtered))

    if unpriced:
        st.warning(
            f"{len(unpriced)} position(s) have no price and are excluded from market value and allocation: "
            + ", ".join(item["symbol"] for item in unpriced)
        )
    priced_dates = [date.fromisoformat(item["price_date"]) for item in filtered if item["price_date"]]
    if priced_dates:
        oldest = min(priced_dates)
        age = (date.today() - oldest).days
        st.caption(f"Portfolio as of {snapshot['as_of']} · Oldest valuation price: {oldest} ({age} days old)")

    overview, details, actions = st.tabs(["Allocation", "Holdings", "Action centre"])
    with overview:
        allocation = pd.DataFrame(filtered)[["symbol", "market_value"]].dropna()
        if allocation.empty:
            st.info("Add prices to see portfolio allocation.")
        else:
            allocation = allocation.groupby("symbol", as_index=False)["market_value"].sum()
            st.bar_chart(allocation.sort_values("market_value", ascending=False).set_index("symbol"), height=340)

    with details:
        search = st.text_input("Search holdings", placeholder="Symbol or company name").strip().lower()
        visible = [item for item in filtered if not search or search in item["symbol"].lower()
                   or search in item["company_name"].lower()]
        display = pd.DataFrame(visible)
        if display.empty:
            st.info("No holdings match your search.")
        else:
            display["return_pct"] = display["return_pct"].map(lambda value: value * 100 if pd.notna(value) else None)
            display["weight"] = display["weight"] * 100
            display = display[["account_name", "exchange", "symbol", "company_name", "quantity",
                "average_cost", "current_price", "price_date", "market_value", "unrealised_profit",
                "return_pct", "weight", "dividend_income", "holding_days", "thesis_status", "recommendation"]]
            display.columns = ["Account", "Exchange", "Symbol", "Company", "Quantity", "Average cost",
                "Current price", "Price date", "Market value", "Unrealised P&L", "Return", "Weight",
                "Dividends", "Holding days", "Thesis", "Review prompt"]
            st.dataframe(display, width="stretch", hide_index=True, column_config={
                "Quantity": st.column_config.NumberColumn(format="%.4f"),
                "Average cost": st.column_config.NumberColumn(format="₹%.2f"),
                "Current price": st.column_config.NumberColumn(format="₹%.2f"),
                "Market value": st.column_config.NumberColumn(format="₹%.2f"),
                "Unrealised P&L": st.column_config.NumberColumn(format="₹%.2f"),
                "Dividends": st.column_config.NumberColumn(format="₹%.2f"),
                "Return": st.column_config.NumberColumn(format="%.2f%%"),
                "Weight": st.column_config.NumberColumn(format="%.1f%%"),
            })

    with actions:
        st.caption("Prompts are deterministic portfolio checks, not buy or sell instructions.")
        action_counts = pd.Series([x["recommendation"] for x in filtered]).value_counts()
        cols = st.columns(max(1, len(action_counts)))
        for col, (action, count) in zip(cols, action_counts.items()):
            col.metric(action, int(count))
        for item in filtered:
            with st.expander(f"{item['symbol']} — {item['recommendation']}"):
                for reason in item["recommendation_reasons"]:
                    st.write(f"• {reason}")

st.warning("For personal research and decision support only. Verify data independently before acting.")
