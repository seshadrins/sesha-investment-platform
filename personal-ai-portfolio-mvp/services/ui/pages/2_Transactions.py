import pandas as pd
import streamlit as st

from common import api_get, api_post, render_sidebar

render_sidebar()
st.title("Transactions")
st.caption("The immutable ledger is the source of truth. Corrections should use adjustment transactions with notes.")

instruments = api_get("/instruments")

instrument_tab, upload_tab, ledger_tab = st.tabs(["Instruments", "Upload transactions", "Ledger"])

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

with upload_tab:
    st.subheader("Import transaction history")
    st.write("Use this for buys, sells, dividends, and quantity adjustments after the opening date.")
    st.download_button("Download transaction template", data=(
        "account_name,broker_name,exchange,symbol,company_name,isin,transaction_type,trade_date,quantity,price,charges,notes\n"
        "Primary,Upstox,NSE,INFY,Infosys Limited,INE009A01021,BUY,2026-08-01,5,1520,25,Additional purchase\n"
    ), file_name="transactions_template.csv", mime="text/csv")
    with st.expander("Transaction conventions"):
        st.markdown("For dividends, enter shares eligible in `quantity`, dividend per share in `price`, and withholding/fees in `charges`. Adjustment rows change quantity and should include an explanatory note.")
    transactions = st.file_uploader("Choose transactions CSV", type=["csv"], key="tx")
    confirm_tx = st.checkbox("I understand this appends transactions and have checked for duplicates.", key="confirm_tx")
    if st.button("Import transactions", type="primary", disabled=transactions is None or not confirm_tx):
        result = api_post("/imports/transactions", files={"file": (transactions.name, transactions.getvalue(), "text/csv")})
        if result:
            st.success(f"Imported {result['imported']} transactions.")

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
