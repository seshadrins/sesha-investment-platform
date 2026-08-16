from datetime import date

import pandas as pd
import streamlit as st

from common import api_get, api_post, instrument_label, render_sidebar

render_sidebar()
st.title("Transactions")
st.caption("The immutable ledger is the source of truth. Corrections should use adjustment transactions with notes.")

accounts = api_get("/accounts")
instruments = api_get("/instruments")

instrument_tab, transaction_tab, ledger_tab = st.tabs(["Instruments", "Record transaction", "Ledger"])

with instrument_tab:
    st.subheader("Instrument master")
    with st.form("instrument_form"):
        c1, c2 = st.columns(2)
        exchange = c1.selectbox("Exchange", ["NSE", "BSE"])
        symbol = c2.text_input("Trading symbol", placeholder="INFY")
        company_name = st.text_input("Company name", placeholder="Infosys Limited")
        isin = st.text_input("ISIN", help="Required for Upstox price and company-profile sync.")
        c3, c4 = st.columns(2)
        sector = c3.text_input("Sector (optional)")
        industry = c4.text_input("Industry (optional)")
        if st.form_submit_button("Save instrument", type="primary"):
            if not symbol.strip():
                st.error("Trading symbol is required.")
            else:
                result = api_post("/instruments", json={"exchange": exchange, "symbol": symbol.strip(),
                    "company_name": company_name.strip() or symbol.strip(), "isin": isin.strip() or None,
                    "sector": sector.strip() or None, "industry": industry.strip() or None})
                if result:
                    st.success("Instrument saved.")
                    st.rerun()
    if instruments:
        st.dataframe(instruments, width="stretch", hide_index=True,
                     column_order=["exchange", "symbol", "company_name", "isin", "sector", "industry"])

with transaction_tab:
    if not accounts:
        st.info("Create an account under Portfolio Setup first.")
    elif not instruments:
        st.info("Add an instrument before recording a transaction.")
    else:
        account_map = {f"{a['name']} — {a['broker_name']}": a["id"] for a in accounts}
        instrument_map = {instrument_label(i): i["id"] for i in instruments}
        with st.form("transaction_form"):
            c1, c2 = st.columns(2)
            account_label = c1.selectbox("Account", account_map)
            instrument_text = c2.selectbox("Instrument", instrument_map)
            tx_type = st.selectbox("Transaction type", ["BUY", "SELL", "DIVIDEND", "ADJUSTMENT_IN", "ADJUSTMENT_OUT"])
            st.caption("Dividend: quantity = eligible shares, price = dividend per share. Adjustments should document the reason.")
            c3, c4, c5 = st.columns(3)
            trade_date = c3.date_input("Trade date", value=date.today())
            quantity = c4.number_input("Quantity / eligible shares", min_value=0.0, step=1.0, format="%.6f")
            price = c5.number_input("Unit price / dividend per share", min_value=0.0, step=1.0, format="%.4f")
            charges = st.number_input("Total charges or withholding", min_value=0.0, step=1.0)
            notes = st.text_area("Reason / notes", placeholder="Why was this transaction made?")
            if st.form_submit_button("Record transaction", type="primary"):
                if quantity <= 0:
                    st.error("Quantity must be greater than zero.")
                else:
                    result = api_post("/transactions", json={"account_id": account_map[account_label],
                        "instrument_id": instrument_map[instrument_text], "transaction_type": tx_type,
                        "trade_date": trade_date.isoformat(), "quantity": quantity, "price": price,
                        "charges": charges, "notes": notes or None})
                    if result:
                        st.success(f"Transaction recorded with ID {result['id']}.")

with ledger_tab:
    ledger = api_get("/transactions")
    if not ledger:
        st.info("No transactions recorded yet.")
    else:
        types = sorted({row["transaction_type"] for row in ledger})
        selected_types = st.multiselect("Filter transaction types", types, default=types)
        visible = [row for row in ledger if row["transaction_type"] in selected_types]
        df = pd.DataFrame(visible).rename(columns={"trade_date": "Date", "account": "Account", "exchange": "Exchange",
            "symbol": "Symbol", "transaction_type": "Type", "quantity": "Quantity", "price": "Unit price",
            "charges": "Charges", "notes": "Notes", "source": "Source"})
        st.dataframe(df, width="stretch", hide_index=True, column_config={
            "Quantity": st.column_config.NumberColumn(format="%.4f"),
            "Unit price": st.column_config.NumberColumn(format="₹%.2f"),
            "Charges": st.column_config.NumberColumn(format="₹%.2f"),
        })
