import streamlit as st

from common import api_get, api_post, render_sidebar

render_sidebar()
st.title("Portfolio Setup")
st.caption("Create accounts, then establish the auditable transaction ledger from CSV or manual entries.")

accounts = api_get("/accounts")
instruments = api_get("/instruments")
c1, c2 = st.columns(2)
c1.metric("Accounts", len(accounts))
c2.metric("Instruments", len(instruments))

account_tab, opening_tab, history_tab = st.tabs(["Accounts", "Opening holdings", "Transaction history"])

with account_tab:
    st.subheader("Create an account")
    with st.form("account_form"):
        name = st.text_input("Account name", value="Primary", help="A unique label such as Primary or Retirement.")
        broker = st.text_input("Broker name", value="Upstox")
        currency = st.selectbox("Currency", ["INR", "USD"])
        if st.form_submit_button("Create account", type="primary"):
            if not name.strip():
                st.error("Account name is required.")
            else:
                result = api_post("/accounts", json={"name": name.strip(), "broker_name": broker.strip() or "Manual", "currency": currency})
                if result:
                    st.success(f"Account ready: {result['name']}")
                    st.rerun()
    if accounts:
        st.subheader("Current accounts")
        st.dataframe(accounts, width="stretch", hide_index=True, column_order=["name", "broker_name", "currency"])

with opening_tab:
    st.subheader("Import opening holdings")
    st.write("Use this once when starting from current broker holdings rather than full trade history.")
    st.download_button("Download opening holdings template", data=(
        "account_name,broker_name,exchange,symbol,company_name,isin,as_of_date,quantity,average_price,notes\n"
        "Primary,Upstox,NSE,INFY,Infosys Limited,INE009A01021,2026-08-01,5,1520,Opening balance\n"
    ), file_name="opening_portfolio_template.csv", mime="text/csv")
    with st.expander("Required columns and import behavior"):
        st.markdown("`account_name`, `symbol`, `as_of_date`, `quantity`, and `average_price` are required. Each row appends an immutable OPENING transaction; importing the same file twice duplicates holdings.")
    opening = st.file_uploader("Choose opening portfolio CSV", type=["csv"], key="opening")
    confirm_opening = st.checkbox("I understand this appends transactions and have checked for duplicates.")
    if st.button("Import opening holdings", type="primary", disabled=opening is None or not confirm_opening):
        result = api_post("/imports/opening", files={"file": (opening.name, opening.getvalue(), "text/csv")})
        if result:
            st.success(f"Imported {result['imported']} opening holdings.")

with history_tab:
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
