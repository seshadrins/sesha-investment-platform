from datetime import date
import streamlit as st

from common import api_get, api_post, instrument_label

st.title("Prices")
instruments = api_get("/instruments")

if not instruments:
    st.info("Add instruments first.")
else:
    instrument_map = {instrument_label(i): i["id"] for i in instruments}
    with st.form("price_form"):
        selected = st.selectbox("Instrument", instrument_map)
        c1, c2 = st.columns(2)
        price_date = c1.date_input("Price date", value=date.today())
        close_price = c2.number_input("Closing price", min_value=0.01, step=1.0)
        if st.form_submit_button("Save price"):
            result = api_post(
                "/prices",
                json={
                    "instrument_id": instrument_map[selected],
                    "price_date": price_date.isoformat(),
                    "close_price": close_price,
                },
            )
            if result:
                st.success("Price saved.")

st.divider()
st.subheader("Import prices")
st.write("Use `imports/prices_template.csv`.")
upload = st.file_uploader("Prices CSV", type=["csv"])
if st.button("Import prices", disabled=upload is None):
    result = api_post(
        "/imports/prices",
        files={"file": (upload.name, upload.getvalue(), "text/csv")},
    )
    if result:
        st.success(f"Imported {result['imported']} prices.")

st.divider()
st.subheader("Import company research")
st.write("Update company name, ISIN, sector and industry using `imports/company_research_template.csv`.")
research_upload = st.file_uploader("Company research CSV", type=["csv"], key="research_csv")
if st.button("Import company research", disabled=research_upload is None):
    result = api_post(
        "/imports/company-research",
        files={"file": (research_upload.name, research_upload.getvalue(), "text/csv")},
    )
    if result:
        st.success(f"Updated {result['imported']} instruments.")
