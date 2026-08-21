import streamlit as st

from common import api_get, api_patch, api_post, render_sidebar

render_sidebar()
st.title("Portfolio Setup")
st.caption("Create accounts, then import opening holdings. Ongoing transactions are uploaded on the Transactions page.")

accounts = api_get("/accounts")
instruments = api_get("/instruments")
c1, c2 = st.columns(2)
c1.metric("Accounts", len(accounts))
c2.metric("Instruments", len(instruments))

account_tab, opening_tab = st.tabs(["Accounts", "Opening holdings"])

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
        st.dataframe(accounts, width="stretch", hide_index=True,
            column_order=["name", "broker_name", "currency", "cash_balance"],
            column_config={"cash_balance": st.column_config.NumberColumn("Cash", format="₹%.2f")})

        st.subheader("Deployable cash")
        st.caption(
            "There is no cash transaction ledger — set each account's current uninvested "
            "cash directly (e.g. from your broker statement) so \"how much do I have to "
            "deploy\" is answerable in-app."
        )
        account_options = {item["name"]: item for item in accounts}
        with st.form("update_cash"):
            account_label = st.selectbox("Account", list(account_options))
            selected = account_options[account_label]
            new_cash = st.number_input("Cash balance", min_value=0.0,
                value=float(selected["cash_balance"]), step=1000.0, format="%.2f")
            if st.form_submit_button("Update cash balance"):
                result = api_patch(f"/accounts/{selected['id']}/cash", json={"cash_balance": new_cash})
                if result:
                    st.success(f"{selected['name']}'s cash balance is now ₹{new_cash:,.2f}.")
                    st.rerun()

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
