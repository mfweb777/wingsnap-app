import streamlit as st
import requests
import random
import time
import os

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Wingsnap", 
    page_icon="🦅", 
    layout="centered"
)

# --- CONFIGURATION & API ---
API_KEY = "sspka81ifcmr"
DEFAULT_LAT = 40.7812
DEFAULT_LON = -73.9665

# --- SESSION STATE INITIALIZATION ---
if 'score' not in st.session_state: st.session_state.score = 0
if 'level' not in st.session_state: st.session_state.level = 1
if 'inventory' not in st.session_state: st.session_state.inventory = []
if 'nearby_birds' not in st.session_state: st.session_state.nearby_birds = []

# --- HELPER FUNCTIONS ---
def calculate_xp(is_notable, how_many):
    """Calculates points based on rarity and scarcity."""
    base_xp = 10
    if is_notable:
        rarity_mult = 10.0
        label = "LEGENDARY"
    else:
        rarity_mult = 1.0
        label = "COMMON"
    scarcity_bonus = 50 if how_many == 1 else 0
    return int((base_xp * rarity_mult) + scarcity_bonus), label

def fetch_local_birds(lat, lon):
    """Connects to eBird API to find real birds reported nearby."""
    url_recent = "https://api.ebird.org/v2/data/obs/geo/recent"
    url_notable = "https://api.ebird.org/v2/data/obs/geo/recent/notable"
    headers = {"X-eBirdApiToken": API_KEY}
    params = {"lat": lat, "lng": lon, "dist": 5, "back": 7}

    try:
        resp_notable = requests.get(url_notable, headers=headers, params=params)
        notable_codes = {b['speciesCode'] for b in resp_notable.json()} if resp_notable.status_code == 200 else set()
        
        resp_recent = requests.get(url_recent, headers=headers, params=params)
        if resp_recent.status_code != 200: return []

        gamified_birds = []
        for bird in resp_recent.json():
            is_rare = bird.get('speciesCode') in notable_codes
            xp, label = calculate_xp(is_rare, bird.get('howMany', 1))
            gamified_birds.append({
                "name": bird.get('comName'),
                "xp": xp,
                "rarity": label,
                "count": bird.get('howMany', 1)
            })
        return gamified_birds
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return []

# --- SIDEBAR (MENU & SETTINGS) ---
with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    
    st.divider()
    st.header("📍 Scout Location")
    lat = st.number_input("Latitude", value=DEFAULT_LAT, format="%.4f")
    lon = st.number_input("Longitude", value=DEFAULT_LON, format="%.4f")
    
    if st.button("📡 Scan Area", type="primary"):
        with st.spinner("Scanning local bio-signals..."):
            birds = fetch_local_birds(lat, lon)
            if birds:
                st.session_state.nearby_birds = birds
                st.success(f"Tracked {len(birds)} species nearby!")
            else:
                st.error("No signals found in this area.")
    
    st.divider()
    st.caption("Wingsnap Beta v1.4")

# --- MAIN APP INTERFACE ---
c1, c2, c3 = st.columns(3)
c1.metric("Level", st.session_state.level)
c2.metric("Total XP", st.session_state.score)
c3.metric("Birds Nearby", len(st.session_state.nearby_birds))

st.divider()

tab1, tab2 = st.tabs(["📸 Capture", "🎒 Collection"])

# --- TAB 1: CAPTURE SCREEN ---
with tab1:
    if not st.session_state.nearby_birds:
        st.info("👈 Open the Sidebar menu (top left) and click **Scan Area** to begin!")
    else:
        st.write("### Ready to snap?")
        
        # --- NEW ZOOM FEATURE: PRO MODE TOGGLE ---
        use_native_cam = st.toggle("🔭 Use Pro Camera (Enables Zoom)", value=False)
        
        img_file = None
        
        if use_native_cam:
            # Native Camera Mode: Lets user use their phone app
            st.caption("Opens your phone's native camera app. Supports Zoom & Focus.")
            img_file = st.file_uploader("Tap to Open Camera", type=['jpg', 'png', 'jpeg'], label_visibility="collapsed")
        else:
            # Standard Mode: Fast live preview
            img_file = st.camera_input("Take a photo", label_visibility="hidden")

        # --- PROCESSING LOGIC (Works for both cameras) ---
        if img_file:
            with st.spinner("Analyzing bio-signature..."):
                time.sleep(1.5)
                caught_bird = random.choice(st.session_state.nearby_birds)

            st.balloons()
            st.success(f"**Target Acquired!**")
            
            with st.container(border=True):
                st.markdown(f"""
                ## {caught_bird['name']}
                **Rarity:** {caught_bird['rarity']}  
                **XP:** +{caught_bird['xp']}
                """)
                
                try:
                    wiki_name = caught_bird['name'].replace(" ", "_")
                    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_name}"
                    headers = {"User-Agent": "WingsnapApp/1.0"}
                    fact_resp = requests.get(url, headers=headers).json()
                    fact = fact_resp.get('extract',
