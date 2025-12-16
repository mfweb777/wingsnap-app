import streamlit as st
import requests
import time
import os
import google.generativeai as genai
from PIL import Image

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Wingsnap", 
    page_icon="🦅", 
    layout="centered"
)

# --- CONFIGURATION ---
EBIRD_API_KEY = "sspka81ifcmr"
GOOGLE_API_KEY = "AIzaSyDrvXOEPuDLby1zOaySqRHXs0xsiwGXLWE" # Your Key

# Default: Central Park, NY
DEFAULT_LAT = 40.7812
DEFAULT_LON = -73.9665

# --- SETUP AI ---
genai.configure(api_key=GOOGLE_API_KEY)

# --- SESSION STATE ---
if 'score' not in st.session_state: st.session_state.score = 0
if 'level' not in st.session_state: st.session_state.level = 1
if 'inventory' not in st.session_state: st.session_state.inventory = []
if 'nearby_birds' not in st.session_state: st.session_state.nearby_birds = []

# --- HELPER FUNCTIONS ---
def identify_bird_with_ai(image):
    """Strict AI identification."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (
            "You are a helpful bird watcher. Look at this photo. "
            "Is there a REAL, LIVING BIRD in this image? "
            "If it is a rug, carpet, toy, drawing, or empty, respond with 'NOT_A_BIRD'. "
            "If it is a real bird, respond with ONLY the common name (e.g., 'Northern Cardinal')."
        )
        response = model.generate_content([prompt, image])
        return response.text.strip()
    except Exception as e:
        return f"Error: {e}"

def calculate_xp(is_notable, how_many):
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
    url_recent = "https://api.ebird.org/v2/data/obs/geo/recent"
    url_notable = "https://api.ebird.org/v2/data/obs/geo/recent/notable"
    headers = {"X-eBirdApiToken": EBIRD_API_KEY}
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
    except:
        return []

# --- AUTOMATIC STARTUP (The Grandma Fix) ---
# If we haven't scanned yet, do it automatically right now.
if not st.session_state.nearby_birds:
    with st.spinner("🚀 Warming up scanners... finding birds near you..."):
        birds = fetch_local_birds(DEFAULT_LAT, DEFAULT_LON)
        if birds:
            st.session_state.nearby_birds = birds
        else:
            # Fallback if eBird is down or location is empty
            st.session_state.nearby_birds = [{"name": "Sparrow", "rarity": "COMMON", "xp": 10}]

# --- SIDEBAR (Hidden Settings) ---
with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    st.header("⚙️ Settings")
    st.write("Change location if you travel:")
    lat = st.number_input("Latitude", value=DEFAULT_LAT, format="%.4f")
    lon = st.number_input("Longitude", value=DEFAULT_LON, format="%.4f")
    if st.button("Update Location"):
        st.session_state.nearby_birds = [] # Clear old birds
        st.rerun() # Restart app to trigger auto-scan

# --- MAIN APP UI ---
st.title("🦅 Wingsnap")

# Simple Stats
st.info(f"📍 **We found {len(st.session_state.nearby_birds)} types of birds in your area.** Go find one!")

tab1, tab2 = st.tabs(["📸 Camera", "🏆 My Birds"])

# --- TAB 1: EASY CAMERA ---
with tab1:
    st.write("### 1. Take a picture")
    
    # Toggle for Zoom (kept simple)
    use_zoom = st.toggle("🔍 Enable Zoom (Upload Photo)", value=False)

    img_file = None
    if use_zoom:
        img_file = st.file_uploader("Tap to select photo", type=['jpg', 'png', 'jpeg'], label_visibility="collapsed")
    else:
        img_file = st.camera_input("Take a photo", label_visibility="collapsed")

    if img_file:
        st.write("### 2. Identifying...")
        with st.spinner("Checking with bird experts..."):
            # AI Logic
            image = Image.open(img_file)
            identified_name = identify_bird_with_ai(image)
            
            if "NOT_A_BIRD" in identified_name or len(identified_name) > 40:
                st.error("🤔 That doesn't look like a bird.")
                st.caption("Try to get closer, or make sure the bird is in the center.")
            else:
                st.balloons()
                
                # Match logic
                match = next((b for b in st.session_state.nearby_birds if b['name'] in identified_name or identified_name in b['name']), None)
                
                if match:
                    final_name = match['name']
                    final_rarity = match['rarity']
                    final_xp = match['xp']
                    msg = "You found a local bird! Great job!"
                else:
                    final_name = identified_name
                    final_rarity = "UNCOMMON"
                    final_xp = 25
                    msg = "Wow! You found a bird we didn't expect!"

                # BIG SUCCESS CARD
                st.success(f"**It's a {final_name}!**")
                st.metric("Points Scored", f"+{final_xp} XP")
                st.caption(msg)

                # Save automatically
                if not any(b['id'] == img_file.name for b in st.session_state.inventory):
                    st.session_state.score += final_xp
                    st.session_state.inventory.append({
                        "id": img_file.name,
                        "name": final_name,
                        "rarity": final_rarity,
                        "xp": final_xp
                    })
                    time.sleep(4)
                    st.rerun()

# --- TAB 2: SIMPLE COLLECTION ---
with tab2:
    if not st.session_state.inventory:
        st.write("You haven't caught any birds yet.")
    else:
        st.write(f"You have {len(st.session_state.inventory)} birds!")
        for bird in reversed(st.session_state.inventory):
            with st.container(border=True):
                st.subheader(bird['name'])
                st.write(f"**{bird['rarity']}** | {bird['xp']} Points")
