"""Forge '26 - Emergency Relief Logistics Agent (UI + integration)."""
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

from tools import (
    get_active_alerts, find_facilities, get_route,
    get_inventory, update_inventory,
)
from agent import run_agent

st.set_page_config(page_title="Relief Logistics Agent", page_icon="🚨", layout="wide")

KIND_STYLE = {
    "hospital": ("red", "plus-sign"),
    "warehouse": ("blue", "home"),
    "shelter": ("green", "tent"),
}

# ---------- session state ----------
for key, default in {"alerts": [], "plan": None, "log": [], "scan_error": None}.items():
    st.session_state.setdefault(key, default)


# ---------- helpers ----------
def route_points(geometry):
    """Convert OSRM geometry (GeoJSON dict, [lon,lat] list, or polyline string) to [(lat, lon)]."""
    if not geometry:
        return []
    if isinstance(geometry, dict):
        geometry = geometry.get("coordinates", [])
    if isinstance(geometry, str):
        try:
            import polyline
            return polyline.decode(geometry)
        except Exception:
            return []
    return [(c[1], c[0]) for c in geometry if len(c) >= 2]


def build_map(alert, plan):
    m = folium.Map(location=[alert["lat"], alert["lon"]], zoom_start=7, tiles="OpenStreetMap")
    radius_km = 50
    if plan and isinstance(plan.get("zone"), dict):
        radius_km = plan["zone"].get("radius_km", radius_km)
    folium.Circle(
        [alert["lat"], alert["lon"]], radius=radius_km * 1000,
        color="orange", fill=True, fill_opacity=0.2,
        tooltip=f"{alert.get('title', 'Alert')} (severity: {alert.get('severity')})",
    ).add_to(m)
    folium.Marker([alert["lat"], alert["lon"]], tooltip="Disaster centre",
                  icon=folium.Icon(color="orange", icon="warning-sign")).add_to(m)

    if plan:
        for f in plan.get("facilities", []) or []:
            color, icon = KIND_STYLE.get(f.get("kind"), ("gray", "info-sign"))
            folium.Marker([f["lat"], f["lon"]], tooltip=f"{f.get('name')} ({f.get('kind')})",
                          icon=folium.Icon(color=color, icon=icon)).add_to(m)
        for r in plan.get("routes", []) or []:
            pts = route_points(r.get("geometry"))
            if pts:
                folium.PolyLine(
                    pts, weight=4, color="purple",
                    tooltip=f"{r.get('distance_km', '?')} km, {r.get('duration_min', '?')} min",
                ).add_to(m)
    return m


# ---------- pages ----------
def page_command_center():
    st.title("🚨 Emergency Relief Logistics Agent")
    st.caption("Live alert → assess → find resources → check stock → plan routes → dispatch → replan")

    if st.button("🔍 Scan live disasters", type="primary"):
        with st.spinner("Fetching live alerts from GDACS..."):
            result = get_active_alerts()
        if isinstance(result, dict) and "error" in result:
            st.session_state.scan_error = result["error"]
            st.session_state.alerts = []
        else:
            st.session_state.scan_error = None
            st.session_state.alerts = result or []
        st.session_state.plan = None
        st.session_state.log = []

    if st.session_state.scan_error:
        st.error(f"Could not fetch alerts: {st.session_state.scan_error}")
    alerts = st.session_state.alerts
    if not alerts:
        st.info("Click **Scan live disasters** to load active alerts.")
        return

    idx = st.selectbox(
        "Select an alert", range(len(alerts)),
        format_func=lambda i: f"[{alerts[i].get('severity')}] {alerts[i].get('type')} - {alerts[i].get('title')}",
    )
    alert = alerts[idx]

    left, right = st.columns([3, 2])

    with right:
        st.subheader("Agent log")
        log_box = st.container()
        if st.button("🚀 Run relief agent on this alert"):
            st.session_state.log = []
            with log_box:
                with st.status("Agent working...", expanded=True) as status:
                    def on_step(msg):
                        st.session_state.log.append(str(msg))
                        st.write(f"• {msg}")
                    try:
                        st.session_state.plan = run_agent(alert, on_step)
                        status.update(label="Agent finished", state="complete")
                    except Exception as e:
                        st.session_state.plan = None
                        status.update(label="Agent failed", state="error")
                        st.error(f"Agent error: {e}")
        elif st.session_state.log:
            with log_box:
                for msg in st.session_state.log:
                    st.write(f"• {msg}")

    with left:
        st.subheader("Map")
        st_folium(build_map(alert, st.session_state.plan), height=520,
                  use_container_width=True, returned_objects=[])

    plan = st.session_state.plan
    if plan:
        st.divider()
        st.subheader("Final plan")
        if isinstance(plan, dict) and "error" in plan:
            st.error(plan["error"])
            return
        # Adjust these keys to match whatever Goutam's run_agent returns
        st.write(plan.get("summary", "") if isinstance(plan, dict) else str(plan))
        dispatch = plan.get("dispatch", {}) if isinstance(plan, dict) else {}
        sent = dispatch.get("success", dispatch.get("sent")) if isinstance(dispatch, dict) else None
        if sent is True:
            st.success("Telegram dispatch sent ✅")
        elif sent is False:
            st.error(f"Telegram dispatch failed: {dispatch.get('error', 'unknown error')}")
        routes = plan.get("routes", []) if isinstance(plan, dict) else []
        if routes:
            st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != "geometry"} for r in routes]),
                         use_container_width=True)
        with st.expander("Raw plan"):
            st.json(plan)


def page_inventory():
    st.title("📦 Inventory")
    st.caption("Live data from the Supabase inventory table")

    c1, c2 = st.columns([3, 1])
    facility = c1.text_input("Facility name", placeholder="Exact facility name, e.g. from the map")
    if c2.button("Load") or facility:
        if not facility:
            return
        with st.spinner("Loading inventory..."):
            rows = get_inventory(facility)
        if isinstance(rows, dict) and "error" in rows:
            st.error(rows["error"])
            return
        df = pd.DataFrame(rows or [], columns=["facility_name", "item", "quantity", "updated_at"]) \
            if not rows else pd.DataFrame(rows)
        if df.empty:
            st.warning("No stock recorded for this facility yet. Add an item below.")
        else:
            edited = st.data_editor(df, disabled=["facility_name", "updated_at"],
                                    use_container_width=True, key=f"ed_{facility}")
            if st.button("💾 Save changes"):
                for _, row in edited.iterrows():
                    res = update_inventory(facility, row["item"], int(row["quantity"]))
                    if isinstance(res, dict) and "error" in res:
                        st.error(f"{row['item']}: {res['error']}")
                st.success("Saved")

        with st.form("add_item", clear_on_submit=True):
            st.markdown("**Add / update an item**")
            item = st.text_input("Item (e.g. water, medicine, blankets)")
            qty = st.number_input("Quantity", min_value=0, step=1)
            if st.form_submit_button("Add"):
                res = update_inventory(facility, item, int(qty))
                if isinstance(res, dict) and "error" in res:
                    st.error(res["error"])
                else:
                    st.success("Updated. Click Load to refresh.")


# ---------- router ----------
page = st.sidebar.radio("Page", ["Command Center", "Inventory"])
if page == "Command Center":
    page_command_center()
else:
    page_inventory()
