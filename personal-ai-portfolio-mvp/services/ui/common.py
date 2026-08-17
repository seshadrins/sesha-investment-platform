import os

import requests
import streamlit as st


API = os.getenv("API_BASE_URL", "http://localhost:8000")


def api_get(path: str):
    try:
        response = requests.get(f"{API}{path}", timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"The portfolio service is unavailable: {exc}")
        st.info("Check that the Docker services are running, then refresh this page.")
        st.stop()


def api_post(path: str, json=None, files=None, data=None, timeout=120):
    try:
        response = requests.post(f"{API}{path}", json=json, files=files, data=data, timeout=timeout)
        if not response.ok:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            st.error(f"Could not complete the request: {detail}")
            return None
        return response.json()
    except requests.RequestException as exc:
        st.error(f"The portfolio service is unavailable: {exc}")
        return None


def api_put(path: str, json=None, timeout=120):
    try:
        response = requests.put(f"{API}{path}", json=json, timeout=timeout)
        if not response.ok:
            detail = response.text
            try: detail = response.json().get("detail", detail)
            except ValueError: pass
            st.error(f"Could not complete the request: {detail}")
            return None
        return response.json()
    except requests.RequestException as exc:
        st.error(f"The portfolio service is unavailable: {exc}")
        return None


def instrument_label(item):
    return f"{item['exchange']}:{item['symbol']} — {item['company_name']}"


def render_sidebar():
    with st.sidebar:
        st.caption("WORKFLOW")
        st.markdown(
            "1. **Portfolio Setup** — accounts and opening data\n"
            "2. **Transactions** — buys, sells and income\n"
            "3. **Prices** — valuations and Upstox sync\n"
            "4. **Thesis & Review** — research decisions\n"
            "5. **Reconcile** — compare with your broker\n"
            "6. **Financial Analysis** — owned and Strong Buy prospects\n"
            "7. **Investor Styles** — NIFTY 500 screen, rules, and backtests\n"
            "8. **Followed Investors** — public-disclosure signals\n"
            "9. **Document Analysis** — cited reports and transcripts"
        )
        st.divider()
        st.caption("Read-only decision support. No orders are placed.")
