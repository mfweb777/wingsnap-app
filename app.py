import streamlit as st
import requests
import time
import os
import google.generativeai as genai
from PIL import Image

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Wingsnap",
    page_icon="🦅",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# --- CUSTOM STYLING (CANVA-STYLE) ---
st.markdown("""
    <style>
    /* Make the main title huge and centered */
    .stApp h1 {
        text-align: center;
        color: #FF4B4B;
        font-family: 'Helvetica Neue', sans-serif;
    }
    
    /* Style the metrics to look like cards */
    [data-testid="stMetric"] {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 10px;
        text-align: center;
    }
    
    /* Make buttons big and friendly */
    .stButton button {
        width: 100%;
        border-radius: 20px;
        font-weight: bold;
    }
    
    /* Hide the default Streamlit menu for a cleaner app look */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# --- API KEYS (CONFIGURATION) ---
EBIRD_API_KEY = "sspka81ifcmr"
GOOGLE_API_KEY = "AIzaSyDrvXOEPuDLby1zOaySqRHXs0xsiwGXLWE" # Your Key

# Default Location: Central Park, NY
DEFAULT_LAT = 40.7812
DEFAULT_LON = -73.9665

# --- SETUP AI ---
if GOOGLE_API_KEY and GOOGLE_API_KEY != "AIzaSyDrvXOEPuDLby1zOaySqRHXs0xsiwGXLWE":
     # Only configure if the key has been changed from the placeholder
     # For this specific user who provided the key in chat, we assume it's correct in the logic below
     pass 

# Force configuration for this session
genai.configure(api_key=GOOGLE_API_KEY)

# --- SESSION STATE ---
if 'score' not in st.session_state: st.session_state.score = 0
if 'level' not in st.session_state: st.session_state.level = 1
if 'inventory' not in st.session_state: st.session_state.inventory = []
if 'nearby_birds' not in st.session_state: st.session_state.nearby_birds = []

# --- HELPER FUNCTIONS ---
def identify_bird_with_ai(image):
    """Sends image to Google Gemini with strict instructions."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (
            "You are a friendly expert ornithologist. Look at this photo. "
            "Is there a REAL, LIVING BIRD in this image? "
            "If it is a rug, carpet, toy, drawing, fuzzy object, or empty, respond with 'NOT_A_BIRD'. "
            "If it is a real bird, respond with ONLY the common English name (e.g., 'Blue Jay')."
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

# --- SIDEBAR SETTINGS ---
with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    
    st.header("⚙️ Settings")
    st.write("Current Location:")
    lat = st.number_input("Latitude", value=DEFAULT_LAT, format="%.4f")
    lon = st.number_input("Longitude", value=DEFAULT_LON, format="%.4f")
    
    if st.button("🔄 Refresh Location"):
        st.session_state.nearby_birds = []
        st.rerun()

    st.divider()
    if st.button("🗑️ Reset Game Data"):
        st.session_state.inventory = []
        st.session_state.score = 0
        st.rerun()

# --- MAIN APP LOGIC ---

# 1. HEADER & LOGO
col1, col2, col3 = st.columns([1,2,1])
with col2:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    else:
        st.title("🦅 Wingsnap")

# 2. AUTO-SCANNER
if not st.session_state.nearby_birds:
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    status_text.write("🚀 Starting engines...")
    time.sleep(0.5)
    progress_bar.progress(30)
    
    status_text.write("📡 Scanning local area for bird calls...")
    birds = fetch_local_birds(DEFAULT_LAT, DEFAULT_LON)
    progress_bar.progress(80)
    
    if birds:
        st.session_state.nearby_birds = birds
        progress_bar.progress(100)
        time.sleep(0.5)
        status_text.empty()
        progress_bar.empty()
        st.toast(f"Success! Found {len(birds)} species nearby.", icon="✅")
    else:
        # Fallback for empty scans
        st.session_state.nearby_birds = [{"name": "Sparrow", "rarity": "COMMON", "xp": 10, "count": 1}]
        status_text.error("Could not reach eBird towers. Using backup database.")

# 3. SCOREBOARD
st.markdown("---")
s1, s2, s3 = st.columns(3)
s1.metric("Level", f"{st.session_state.level}")
s2.metric("Total XP", f"{st.session_state.score}")
s3.metric("Birds Found", f"{len(st.session_state.inventory)}")
st.markdown("---")

# 4. TABS
tab_cam, tab_dex = st.tabs(["📸 **CAMERA**", "🏆 **MY COLLECTION**"])

# --- TAB 1: CAMERA ---
with tab_cam:
    st.info("Tap the button below to snap a picture of a bird!")
    
    # Simple Mode Switcher
    mode = st.radio("Camera Mode:", ["Quick Snap", "Upload / Zoom"], horizontal=True, label_visibility="collapsed")

    img_file = None
    if mode == "Upload / Zoom":
        img_file = st.file_uploader("📂 Select photo from gallery", type=['jpg', 'png', 'jpeg'])
    else:
        img_file = st.camera_input("Take a photo", label_visibility="collapsed")

    if img_file:
        st.divider()
        st.write("### 🔍 Identifying...")
        
        # progress bar for AI thinking
        ai_progress = st.progress(0)
        for i in range(100):
            time.sleep(0.01)
            ai_progress.progress(i + 1)
        ai_progress.empty()

        # AI Analysis
        image = Image.open(img_file)
        identified_name = identify_bird_with_ai(image)
        
        # Result Logic
        if "NOT_A_BIRD" in identified_name or len(identified_name) > 50:
            st.warning("🤔 **That doesn't look like a bird.**")
            st.caption("Our expert says: it might be a rug, toy, or blurry object. Try again!")
        else:
            st.balloons()
            
            # Check for Match
            match = next((b for b in st.session_state.nearby_birds if b['name'].lower() in identified_name.lower()), None)
            
            if match:
                bird_name = match['name']
                rarity = match['rarity']
                xp = match['xp']
                msg = "🎯 **CONFIRMED LOCAL SIGHTING!**"
            else:
                bird_name = identified_name
                rarity = "UNCOMMON"
                xp = 25
                msg = "✨ **WILD DISCOVERY!**"

            # SUCCESS CARD
            with st.container():
                st.markdown(f"""
                <div style="background-color: #dbf0da; padding: 20px; border-radius: 15px; border: 2px solid #4CAF50; text-align: center;">
                    <h2 style="color: #2E7D32; margin:0;">{bird_name}</h2>
                    <p style="font-size: 18px; margin: 5px 0;"><b>{rarity}</b> | +{xp} XP</p>
                    <hr style="border-top: 1px dashed #4CAF50;">
                    <p style="font-size: 14px; margin:0;">{msg}</p>
                </div>
                """, unsafe_allow_html=True)

            # Auto-save
            if not any(b['id'] == img_file.name for b in st.session_state.inventory):
                st.session_state.score += xp
                st.session_state.level = 1 + (st.session_state.score // 1000)
                st.session_state.inventory.append({
                    "id": img_file.name,
                    "name": bird_name,
                    "rarity": rarity,
                    "xp": xp,
                    "date": time.strftime("%H:%M")
                })
                time.sleep(4)
                st.rerun()

# --- TAB 2: COLLECTION ---
with tab_dex:
    if not st.session_state.inventory:
        st.info("Your journal is empty. Go take some photos!")
    else:
        st.write(f"### Recent Catches")
        for bird in reversed(st.session_state.inventory):
            # Styling for rarity
            border_color = "#FFD700" if bird['rarity'] == "LEGENDARY" else "#e0e0e0"
            bg_color = "#fffbf0" if bird['rarity'] == "LEGENDARY" else "#ffffff"
            
            st.markdown(f"""
            <div style="
                background-color: {bg_color}; 
                padding: 15px; 
                border-radius: 12px; 
                border: 2px solid {border_color}; 
                margin-bottom: 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;">
                <div>
                    <h3 style="margin: 0; font-size: 20px;">{bird['name']}</h3>
                    <span style="font-size: 12px; color: grey;">{bird['rarity']} • Captured at {bird['date']}</span>
                </div>
                <div style="font-weight: bold; font-size: 24px; color: #555;">
                    +{bird['xp']}
                </div>
            </div>
            """, unsafe_allow_html=True)
