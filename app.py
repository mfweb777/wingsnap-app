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
    """Sends image to Google Gemini with STRICTER instructions."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # --- NEW "PARANOID" PROMPT ---
        prompt = (
            "You are a strict ornithologist. Look at this photo. "
            "Is there a REAL, LIVING BIRD in this image? "
            "If the image is a rug, carpet, fabric pattern, drawing, toy, or empty, YOU MUST RESPOND WITH 'NOT_A_BIRD'. "
            "Do not hallucinate feathers in textures. "
            "If you are not 100% sure it is a real bird, respond with 'NOT_A_BIRD'. "
            "If it IS a real bird, respond with ONLY the common name."
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

# --- SIDEBAR ---
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
                st.error("No signals found.")
    
    st.divider()
    st.caption("Wingsnap AI v2.3 (Strict Mode)")

# --- MAIN APP ---
c1, c2, c3 = st.columns(3)
c1.metric("Level", st.session_state.level)
c2.metric("XP", st.session_state.score)
c3.metric("Birds Nearby", len(st.session_state.nearby_birds))

st.divider()

tab1, tab2 = st.tabs(["📸 Capture", "🎒 Collection"])

# --- TAB 1: CAPTURE ---
with tab1:
    if not st.session_state.nearby_birds:
        st.info("👈 Open Sidebar -> Click **Scan Area** first.")
    else:
        # PRO MODE TOGGLE
        use_native_cam = st.toggle("🔭 Use Pro Camera (Enables Zoom)", value=False)
        img_file = None
        
        if use_native_cam:
            img_file = st.file_uploader("Tap to Open Camera", type=['jpg', 'png', 'jpeg'], label_visibility="collapsed")
        else:
            img_file = st.camera_input("Take a photo", label_visibility="hidden")

        if img_file:
            with st.spinner("AI is analyzing feather patterns..."):
                image = Image.open(img_file)
                identified_name = identify_bird_with_ai(image)
                
            # --- STRICT CHECK ---
            # Debug: Uncomment the line below if you want to see exactly what the AI said
            # st.write(f"Debug Raw AI Response: {identified_name}")

            if "NOT_A_BIRD" in identified_name or len(identified_name) > 40:
                st.error("❌ No bird detected.")
                st.caption("The scanner detected a rug, object, or unclear image.")
            else:
                st.balloons()
                
                # Cross-reference
                match = next((b for b in st.session_state.nearby_birds if b['name'] in identified_name or identified_name in b['name']), None)
                
                if match:
                    st.success(f"**Identified: {match['name']}**")
                    final_rarity = match['rarity']
                    final_xp = match['xp']
                    note = "Confirmed Local Sighting!"
                else:
                    st.success(f"**Identified: {identified_name}**")
                    final_rarity = "UNCOMMON"
                    final_xp = 25
                    note = "Wild Catch (Not on local scanner)"

                # Display Card
                with st.container(border=True):
                    st.markdown(f"""
                    ## {identified_name}
                    **Rarity:** {final_rarity}  
                    **XP:** +{final_xp}  
                    *{note}*
                    """)

                # Save to Inventory
                if not any(b['id'] == img_file.name for b in st.session_state.inventory):
                    st.session_state.score += final_xp
                    st.session_state.level = 1 + (st.session_state.score // 1000)
                    st.session_state.inventory.append({
                        "id": img_file.name,
                        "name": identified_name,
                        "rarity": final_rarity,
                        "xp": final_xp
                    })
                    time.sleep(4)
                    st.rerun()

# --- TAB 2: INVENTORY ---
with tab2:
    if not st.session_state.inventory:
        st.caption("Empty.")
    else:
        for bird in reversed(st.session_state.inventory):
            with st.container(border=True):
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.subheader(bird['name'])
                    if bird['rarity'] == "LEGENDARY":
                        st.markdown(f":orange[**{bird['rarity']}**]")
                    else:
                        st.caption(f"{bird['rarity']}")
                with col_b:
                    st.metric("XP", bird['xp'])
