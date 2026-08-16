from datetime import date
import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label

st.title("Transactions")

accounts = api_get("/accounts")
instruments = api_get("/instruments")

if not accounts:
    st.info("Create an account under Portfolio Setup first.")
else:
    st.subheader("Add an instrument")
    with st.form("instrument_form"):
        c1, c2 = st.columns(2)
        exchange = c1.text_input("Exchange", value="NSE")
        symbol = c2.text_input("Symbol")
        company_name = st.text_input("Company name")
        isin = st.text_input("ISIN (optional)")
        sector = st.text_input("Sector (optional)")
        if st.form_submit_button("Save instrument"):
            result = api_post(
                "/instruments",
                json={
                    "exchange": exchange,
                    "symbol": symbol,
                    "company_name": company_name or symbol,
                    "isin": isin or None,
                    "sector": sector or None,
                    "industry": None,
                },
            )
            if result:
                st.success("Instrument saved.")
                st.rerun()

    st.divider()
    if not instruments:
        st.info("Add an instrument before recording a transaction.")
    else:
        account_map = {f"{a['name']} — {a['broker_name']}": a["id"] for a in accounts}
        instrument_map = {instrument_label(i): i["id"] for i in instruments}

        st.subheader("Record a transaction")
        with st.form("transaction_form"):
            c1, c2 = st.columns(2)
            account_label = c1.selectbox("Account", account_map)
            instrument_text = c2.selectbox("Instrument", instrument_map)
            tx_type = st.selectbox(
                "Transaction type",
                ["BUY", "SELL", "DIVIDEND", "ADJUSTMENT_IN", "ADJUSTMENT_OUT"],
            )
            c3, c4, c5 = st.columns(3)
            trade_date = c3.date_input("Trade date", value=date.today())
            quantity = c4.number_input("Quantity", min_value=0.0, step=1.0, format="%.6f")
            price = c5.number_input("Price", min_value=0.0, step=1.0, format="%.4f")
            charges = st.number_input("Total charges", min_value=0.0, step=1.0)
            notes = st.text_area("Reason / notes")
            submitted = st.form_submit_button("Record transaction")
            if submitted:
                result = api_post(
                    "/transactions",
                    json={
                        "account_id": account_map[account_label],
                        "instrument_id": instrument_map[instrument_text],
                        "transaction_type": tx_type,
                        "trade_date": trade_date.isoformat(),
                        "quantity": quantity,
                        "price": price,
                        "charges": charges,
                        "notes": notes or None,
                    },
                )
                if result:
                    st.success(f"Transaction recorded with ID {result['id']}.")

st.divider()
st.subheader("Transaction ledger")
ledger = api_get("/transactions")
st.dataframe(pd.DataFrame(ledger), use_container_width=True, hide_index=True)
