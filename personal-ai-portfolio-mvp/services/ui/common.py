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
        st.error(f"API request failed: {exc}")
        st.stop()


def api_post(path: str, json=None, files=None):
    try:
        response = requests.post(f"{API}{path}", json=json, files=files, timeout=60)
        if not response.ok:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            st.error(f"Request failed: {detail}")
            return None
        return response.json()
    except requests.RequestException as exc:
        st.error(f"API request failed: {exc}")
        return None


def instrument_label(item):
    return f"{item['exchange']}:{item['symbol']} — {item['company_name']}"
