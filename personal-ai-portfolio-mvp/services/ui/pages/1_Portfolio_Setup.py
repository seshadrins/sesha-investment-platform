import streamlit as st

from common import api_get, api_post

st.title("Portfolio Setup")

st.subheader("Create an account")
with st.form("account_form"):
    name = st.text_input("Account name", value="Primary")
    broker = st.text_input("Broker name", value="Manual")
    currency = st.selectbox("Currency", ["INR", "USD"])
    submitted = st.form_submit_button("Create account")
    if submitted:
        result = api_post(
            "/accounts",
            json={"name": name, "broker_name": broker, "currency": currency},
        )
        if result:
            st.success(f"Account ready: {result['name']}")

st.divider()
st.subheader("Import opening portfolio")
st.write(
    "Use the template in `imports/opening_portfolio_template.csv`. "
    "Each row becomes an OPENING transaction."
)
opening = st.file_uploader("Opening portfolio CSV", type=["csv"], key="opening")
if st.button("Import opening holdings", disabled=opening is None):
    result = api_post(
        "/imports/opening",
        files={"file": (opening.name, opening.getvalue(), "text/csv")},
    )
    if result:
        st.success(f"Imported {result['imported']} opening holdings.")

st.divider()
st.subheader("Import transaction history")
st.write("Use `imports/transactions_template.csv`.")
transactions = st.file_uploader("Transactions CSV", type=["csv"], key="tx")
if st.button("Import transactions", disabled=transactions is None):
    result = api_post(
        "/imports/transactions",
        files={"file": (transactions.name, transactions.getvalue(), "text/csv")},
    )
    if result:
        st.success(f"Imported {result['imported']} transactions.")

st.divider()
st.subheader("Current accounts")
st.dataframe(api_get("/accounts"), use_container_width=True, hide_index=True)
