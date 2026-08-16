import os

import pandas as pd
import streamlit as st

from common import api_get


st.set_page_config(
    page_title=os.getenv("APP_TITLE", "Personal AI Portfolio Manager"),
    page_icon="📈",
    layout="wide",
)

st.title("Personal AI Portfolio Manager")
st.caption("Account-wise holdings, performance, and decision support")

snapshot = api_get("/portfolio")
positions = snapshot["positions"]

if not positions:
    st.info("No holdings yet. Start with **Portfolio Setup** in the left navigation.")
else:
    accounts = sorted({item["account_name"] for item in positions})
    selected_account = st.selectbox("Account", ["All accounts", *accounts])
    filtered = (
        positions
        if selected_account == "All accounts"
        else [item for item in positions if item["account_name"] == selected_account]
    )

    market_value = sum(item["market_value"] or 0 for item in filtered)
    remaining_cost = sum(item["remaining_cost"] or 0 for item in filtered)
    unrealised_profit = sum(item["unrealised_profit"] or 0 for item in filtered)
    realised_profit = sum(item["realised_profit"] or 0 for item in filtered)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Market value", f"₹{market_value:,.2f}")
    c2.metric("Remaining cost", f"₹{remaining_cost:,.2f}")
    c3.metric("Unrealised P&L", f"₹{unrealised_profit:,.2f}")
    c4.metric("Realised P&L", f"₹{realised_profit:,.2f}")
    c5.metric("Open positions", len(filtered))

    st.subheader("Allocation")
    allocation = pd.DataFrame(filtered)[["symbol", "market_value"]].dropna()
    allocation = allocation.groupby("symbol", as_index=False)["market_value"].sum()
    allocation = allocation.sort_values("market_value", ascending=False).set_index("symbol")
    st.bar_chart(allocation, y="market_value", height=300)

    st.subheader("Scrip details")
    search = st.text_input(
        "Search by symbol or company",
        placeholder="For example: HDFBAN or HDFC Bank",
    ).strip().lower()
    visible = filtered
    if search:
        visible = [
            item
            for item in filtered
            if search in item["symbol"].lower()
            or search in item["company_name"].lower()
        ]

    display = pd.DataFrame(visible)
    if display.empty:
        st.info("No scrips match the search.")
    else:
        account_market_value = sum(item["market_value"] or 0 for item in filtered)
        display["account_weight"] = display["market_value"].map(
            lambda value: value / account_market_value
            if pd.notna(value) and account_market_value
            else 0
        )
        display["return_pct"] = display["return_pct"].map(
            lambda value: value * 100 if pd.notna(value) else None
        )
        display["account_weight"] = display["account_weight"] * 100
        display = display[
            [
                "account_name",
                "exchange",
                "symbol",
                "company_name",
                "quantity",
                "average_cost",
                "remaining_cost",
                "current_price",
                "price_date",
                "market_value",
                "unrealised_profit",
                "return_pct",
                "account_weight",
                "first_purchase_date",
                "holding_days",
                "thesis_status",
                "recommendation",
            ]
        ].sort_values("market_value", ascending=False, na_position="last")
        display = display.rename(
            columns={
                "account_name": "Account",
                "exchange": "Exchange",
                "symbol": "Symbol",
                "company_name": "Company",
                "quantity": "Quantity",
                "average_cost": "Average cost",
                "remaining_cost": "Total cost",
                "current_price": "Current price",
                "price_date": "Price date",
                "market_value": "Market value",
                "unrealised_profit": "Unrealised P&L",
                "return_pct": "Return",
                "account_weight": "Weight",
                "first_purchase_date": "First purchase",
                "holding_days": "Holding days",
                "thesis_status": "Thesis status",
                "recommendation": "Recommendation",
            }
        )
        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Quantity": st.column_config.NumberColumn(format="%.2f"),
                "Average cost": st.column_config.NumberColumn(format="₹%.2f"),
                "Total cost": st.column_config.NumberColumn(format="₹%.2f"),
                "Current price": st.column_config.NumberColumn(format="₹%.2f"),
                "Market value": st.column_config.NumberColumn(format="₹%.2f"),
                "Unrealised P&L": st.column_config.NumberColumn(format="₹%.2f"),
                "Return": st.column_config.NumberColumn(format="%.2f%%"),
                "Weight": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

    st.subheader("Action centre")
    action_counts = pd.Series([x["recommendation"] for x in filtered]).value_counts()
    cols = st.columns(max(1, len(action_counts)))
    for col, (action, count) in zip(cols, action_counts.items()):
        col.metric(action, int(count))

    st.subheader("Recommendation rationale")
    for item in visible:
        with st.expander(
            f"{item['symbol']} — {item['company_name']} — {item['recommendation']}"
        ):
            for reason in item["recommendation_reasons"]:
                st.write(f"• {reason}")

st.warning(
    "This application is for personal research and decision support. "
    "It does not guarantee returns or replace independent judgement."
)
