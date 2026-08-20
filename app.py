import hashlib
import json
import os
from datetime import date, datetime
from io import BytesIO

import requests
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field
from streamlit_geolocation import streamlit_geolocation

# ============================================================
# WINGSNAP v3.1
# Location-aware bird ID + field guide + game loop
# ============================================================

st.set_page_config(
    page_title="WingSnap",
    page_icon="🦅",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ----------------------------
# SECRETS
# ----------------------------
try:
    EBIRD_API_KEY = st.secrets["EBIRD_API_KEY"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except Exception:
    st.error(
        "WingSnap is missing API secrets. Add EBIRD_API_KEY and "
        "GEMINI_API_KEY in Streamlit Community Cloud → App settings → Secrets."
    )
    st.stop()

client = genai.Client(api_key=GEMINI_API_KEY)

# ----------------------------
# CONSTANTS
# ----------------------------
DEFAULT_LAT = 40.7812
DEFAULT_LON = -73.9665
SEARCH_RADIUS_KM = 8
EBIRD_BACK_DAYS = 7
MODEL_NAME = "gemini-3.7-flash"

XP_PER_LEVEL = 500
DAILY_TARGET_BONUS = 50
NEW_SPECIES_BONUS = 25
HIGH_CONFIDENCE_BONUS = 10

# ----------------------------
# STYLE
# ----------------------------
st.markdown(
    """
    <style>
      .block-container {
          max-width: 780px;
          padding-top: .8rem;
          padding-bottom: 4rem;
      }
      #MainMenu, footer {visibility: hidden;}

      [data-testid="stMetric"] {
          background: rgba(248,249,250,.94);
          border: 1px solid rgba(0,0,0,.08);
          padding: .7rem;
          border-radius: 16px;
      }

      .ws-hero {
          border: 1px solid rgba(0,0,0,.08);
          border-radius: 22px;
          padding: 1rem 1.1rem;
          margin: .4rem 0 1rem 0;
          background: linear-gradient(135deg, rgba(226,248,255,.92), rgba(235,252,239,.94));
      }

      .ws-card {
          border: 1px solid rgba(0,0,0,.09);
          border-radius: 18px;
          padding: 1rem 1.1rem;
          margin: .65rem 0;
          background: rgba(255,255,255,.94);
      }

      .ws-success {
          border: 2px solid #54a96b;
          border-radius: 20px;
          padding: 1.1rem;
          text-align: center;
          background: rgba(225,247,232,.82);
      }

      .ws-target {
          border: 1px solid rgba(36,128,76,.30);
          border-radius: 18px;
          padding: .9rem 1rem;
          background: rgba(232,250,239,.84);
      }

      .ws-muted {opacity: .70; font-size: .9rem;}
      .ws-kicker {
          font-size: .75rem;
          font-weight: 800;
          letter-spacing: .08em;
          opacity: .68;
      }
      .stButton button {
          border-radius: 999px;
          font-weight: 700;
          width: 100%;
      }
      div[data-testid="stFileUploaderDropzone"] {
          border-radius: 18px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------
# STATE
# ----------------------------
DEFAULT_STATE = {
    "score": 0,
    "level": 1,
    "inventory": [],
    "nearby_birds": [],
    "location": None,
    "last_scan_key": None,
    "processed_photo_hashes": [],
    "last_result": None,
    "target_hits": 0,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value.copy() if isinstance(value, list) else value


# ----------------------------
# DATA MODELS
# ----------------------------
class BirdIdentification(BaseModel):
    is_bird: bool = Field(description="True only if a real living bird is visible.")
    common_name: str = Field(description="Common English bird name, or empty if unknown.")
    scientific_name: str = Field(description="Scientific name if reasonably known, otherwise empty.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Species identification confidence from 0 to 1.",
    )
    local_candidate: bool = Field(
        description="True when the final species appears in the supplied nearby eBird list."
    )
    reason: str = Field(
        description="One short explanation, especially when identification is uncertain."
    )


# ----------------------------
# GENERAL HELPERS
# ----------------------------
def normalize_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def photo_hash(photo_bytes: bytes) -> str:
    return hashlib.sha256(photo_bytes).hexdigest()


def level_for_xp(xp: int) -> int:
    return 1 + (xp // XP_PER_LEVEL)


def level_progress(xp: int) -> float:
    return (xp % XP_PER_LEVEL) / XP_PER_LEVEL


def unique_species_names() -> set[str]:
    return {
        normalize_name(item["name"])
        for item in st.session_state.inventory
        if item.get("name")
    }


def request_json(url: str, *, headers: dict, params: dict) -> list:
    response = requests.get(url, headers=headers, params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected eBird response.")
    return data


# ----------------------------
# EBIRD
# ----------------------------
@st.cache_data(ttl=900, show_spinner=False)
def fetch_local_birds(lat: float, lon: float) -> list[dict]:
    headers = {"X-eBirdApiToken": EBIRD_API_KEY}
    params = {
        "lat": lat,
        "lng": lon,
        "dist": SEARCH_RADIUS_KM,
        "back": EBIRD_BACK_DAYS,
    }

    recent_url = "https://api.ebird.org/v2/data/obs/geo/recent"
    notable_url = "https://api.ebird.org/v2/data/obs/geo/recent/notable"

    recent = request_json(recent_url, headers=headers, params=params)

    try:
        notable = request_json(notable_url, headers=headers, params=params)
        notable_codes = {row.get("speciesCode") for row in notable}
    except Exception:
        notable_codes = set()

    birds = []
    seen_codes = set()

    for row in recent:
        code = row.get("speciesCode")
        if not code or code in seen_codes:
            continue

        seen_codes.add(code)
        is_notable = code in notable_codes

        birds.append(
            {
                "name": row.get("comName") or "Unknown bird",
                "scientific_name": row.get("sciName") or "",
                "species_code": code,
                "notable": is_notable,
                "game_rarity": "LEGENDARY" if is_notable else "COMMON",
                "base_xp": 125 if is_notable else 20,
                "last_seen": row.get("obsDt") or "",
                "count": row.get("howMany"),
            }
        )

    birds.sort(key=lambda bird: (not bird["notable"], bird["name"]))
    return birds


def location_key(lat, lon):
    return (round(float(lat), 3), round(float(lon), 3))


def scan_if_needed(lat: float, lon: float):
    key = location_key(lat, lon)
    if st.session_state.last_scan_key == key and st.session_state.nearby_birds:
        return

    with st.spinner("Finding birds reported nearby…"):
        try:
            birds = fetch_local_birds(lat, lon)
            st.session_state.nearby_birds = birds
            st.session_state.last_scan_key = key
            if birds:
                st.toast(f"{len(birds)} nearby species found", icon="🐦")
            else:
                st.warning("No recent eBird observations were found nearby.")
        except requests.HTTPError as exc:
            st.session_state.nearby_birds = []
            st.error(f"eBird could not complete the scan: {exc}")
        except Exception as exc:
            st.session_state.nearby_birds = []
            st.error(f"Could not scan the area: {exc}")


# ----------------------------
# DAILY TARGET
# ----------------------------
def daily_target(nearby_birds: list[dict], lat: float, lon: float) -> dict | None:
    if not nearby_birds:
        return None

    # Prefer non-notable birds for an achievable daily target.
    pool = [bird for bird in nearby_birds if not bird.get("notable")] or nearby_birds

    seed_text = f"{date.today().isoformat()}|{round(lat,2)}|{round(lon,2)}"
    seed_value = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12], 16)
    return pool[seed_value % len(pool)]


# ----------------------------
# GEMINI IDENTIFICATION
# ----------------------------
def identify_bird(image: Image.Image, nearby_birds: list[dict]) -> BirdIdentification:
    candidates = [bird["name"] for bird in nearby_birds[:120]]
    candidate_text = ", ".join(candidates) if candidates else "No local candidate list available."

    prompt = f"""
You are the vision engine for WingSnap, a real-world bird collecting game.

Nearby species reported by eBird within roughly {SEARCH_RADIUS_KM} km during the last
{EBIRD_BACK_DAYS} days include:
{candidate_text}

Rules:
- First determine whether a REAL, LIVING BIRD is visibly present.
- Never identify rugs, carpets, fabric, toys, statues, drawings, screens, patterns,
  shadows, or household objects as birds.
- If a real bird is visible but the species cannot be identified reliably because it
  is tiny, blurry, distant, hidden, backlit, or partially obstructed, set is_bird=true
  and lower confidence instead of inventing a species.
- Prefer a nearby candidate only when the visual evidence supports it.
- A species outside the candidate list is allowed when the visual evidence is strong.
- Return the standard common English name.
- local_candidate must reflect whether the final common_name is actually in the supplied list.
"""

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[prompt, image],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BirdIdentification,
            max_output_tokens=350,
        ),
    )

    if getattr(response, "parsed", None):
        return response.parsed

    return BirdIdentification.model_validate_json(response.text)


def find_local_match(name: str, nearby_birds: list[dict]) -> dict | None:
    target = normalize_name(name)
    for bird in nearby_birds:
        if normalize_name(bird["name"]) == target:
            return bird
    return None


# ----------------------------
# GAME LOGIC
# ----------------------------
def calculate_award(
    result: BirdIdentification,
    local_match: dict | None,
    is_new_species: bool,
    is_daily_target: bool,
):
    if local_match and local_match.get("notable"):
        game_rarity = "LEGENDARY"
        xp = 125
    elif local_match:
        game_rarity = "COMMON"
        xp = 20
    else:
        game_rarity = "UNCOMMON"
        xp = 35

    bonuses = []

    if is_new_species:
        xp += NEW_SPECIES_BONUS
        bonuses.append(f"New species +{NEW_SPECIES_BONUS}")

    if result.confidence >= 0.90:
        xp += HIGH_CONFIDENCE_BONUS
        bonuses.append(f"Sharp ID +{HIGH_CONFIDENCE_BONUS}")

    if is_daily_target:
        xp += DAILY_TARGET_BONUS
        bonuses.append(f"Daily target +{DAILY_TARGET_BONUS}")

    return game_rarity, xp, bonuses


def save_catch(
    *,
    image_bytes: bytes,
    image_hash: str,
    name: str,
    scientific_name: str,
    rarity: str,
    xp: int,
    confidence: float,
    lat: float,
    lon: float,
    eBird_notable: bool,
    daily_target_hit: bool,
):
    caught_at = datetime.now().isoformat(timespec="seconds")

    st.session_state.inventory.append(
        {
            "id": f"{normalize_name(name)}|{caught_at}",
            "image_hash": image_hash,
            "image_bytes": image_bytes,
            "name": name,
            "scientific_name": scientific_name,
            "rarity": rarity,
            "xp": xp,
            "confidence": confidence,
            "caught_at": caught_at,
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "ebird_notable": bool(eBird_notable),
            "daily_target_hit": bool(daily_target_hit),
        }
    )

    st.session_state.processed_photo_hashes.append(image_hash)
    st.session_state.score += xp
    st.session_state.level = level_for_xp(st.session_state.score)

    if daily_target_hit:
        st.session_state.target_hits += 1


def badge_statuses() -> list[dict]:
    species = unique_species_names()
    catches = st.session_state.inventory

    return [
        {
            "icon": "🐣",
            "name": "First Flight",
            "description": "Discover your first species",
            "unlocked": len(species) >= 1,
        },
        {
            "icon": "🪶",
            "name": "Five Feathers",
            "description": "Discover 5 different species",
            "unlocked": len(species) >= 5,
        },
        {
            "icon": "🔟",
            "name": "Ten Club",
            "description": "Discover 10 different species",
            "unlocked": len(species) >= 10,
        },
        {
            "icon": "🎯",
            "name": "Target Tracker",
            "description": "Catch a daily target",
            "unlocked": any(x.get("daily_target_hit") for x in catches),
        },
        {
            "icon": "👁️",
            "name": "Eagle Eye",
            "description": "Get a 95%+ confidence ID",
            "unlocked": any(x.get("confidence", 0) >= 0.95 for x in catches),
        },
        {
            "icon": "⭐",
            "name": "Local Legend",
            "description": "Catch an eBird-notable species",
            "unlocked": any(x.get("ebird_notable") for x in catches),
        },
    ]


# ----------------------------
# EXPORT / IMPORT
# ----------------------------
def collection_export_json() -> str:
    clean_inventory = []
    for item in st.session_state.inventory:
        clean = {k: v for k, v in item.items() if k != "image_bytes"}
        clean_inventory.append(clean)

    payload = {
        "format": "wingsnap_collection_v1",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "score": st.session_state.score,
        "level": st.session_state.level,
        "inventory": clean_inventory,
    }
    return json.dumps(payload, indent=2)


def import_collection(uploaded):
    payload = json.loads(uploaded.getvalue().decode("utf-8"))
    if payload.get("format") != "wingsnap_collection_v1":
        raise ValueError("This is not a WingSnap collection export.")

    inventory = payload.get("inventory", [])
    if not isinstance(inventory, list):
        raise ValueError("Invalid WingSnap collection data.")

    # Imported catches don't include image bytes.
    for item in inventory:
        item["image_bytes"] = None

    st.session_state.inventory = inventory
    st.session_state.score = int(payload.get("score", 0))
    st.session_state.level = level_for_xp(st.session_state.score)
    st.session_state.processed_photo_hashes = [
        item.get("image_hash")
        for item in inventory
        if item.get("image_hash")
    ]


# ============================================================
# UI
# ============================================================

# ----------------------------
# HEADER
# ----------------------------
if os.path.exists("logo.png"):
    cols = st.columns([1, 2.15, 1])
    with cols[1]:
        st.image("logo.png", use_container_width=True)
else:
    st.title("🦅 WingSnap")

st.markdown(
    """
    <div class="ws-hero">
      <div class="ws-kicker">WINGSNAP FIELD MODE</div>
      <div style="font-size:1.05rem;font-weight:750;margin-top:.25rem;">
        See it. Snap it. Add it to your flock.
      </div>
      <div class="ws-muted" style="margin-top:.25rem;">
        Real nearby sightings help WingSnap identify the bird in your photo.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ----------------------------
# LOCATION
# ----------------------------
st.subheader("📍 Birding area")

geo = streamlit_geolocation()

if isinstance(geo, dict) and geo.get("latitude") is not None and geo.get("longitude") is not None:
    st.session_state.location = {
        "lat": float(geo["latitude"]),
        "lon": float(geo["longitude"]),
        "accuracy": geo.get("accuracy"),
    }

if st.session_state.location:
    lat = st.session_state.location["lat"]
    lon = st.session_state.location["lon"]
    accuracy = st.session_state.location.get("accuracy")
    if accuracy:
        st.caption(f"Using your device location · about {accuracy:.0f} m accuracy")
    else:
        st.caption("Using your device location")
else:
    lat, lon = DEFAULT_LAT, DEFAULT_LON
    st.info(
        "Tap **Get Location** above so WingSnap can load birds recently reported near you."
    )

with st.expander("⚙️ Settings, backup & fallback location"):
    use_manual = st.toggle("Use manual location", value=False)
    manual_lat = st.number_input("Latitude", value=float(lat), format="%.5f")
    manual_lon = st.number_input("Longitude", value=float(lon), format="%.5f")

    if use_manual:
        lat, lon = manual_lat, manual_lon

    left, right = st.columns(2)
    with left:
        if st.button("Refresh nearby birds"):
            st.session_state.last_scan_key = None
            fetch_local_birds.clear()
    with right:
        if st.button("Reset this session"):
            for key, value in DEFAULT_STATE.items():
                st.session_state[key] = value.copy() if isinstance(value, list) else value
            st.rerun()

    st.divider()
    st.caption("Collection backup")
    st.download_button(
        "Download collection backup",
        data=collection_export_json(),
        file_name=f"wingsnap_collection_{date.today().isoformat()}.json",
        mime="application/json",
    )
    restore_file = st.file_uploader(
        "Restore a WingSnap collection backup",
        type=["json"],
        key="restore_collection",
    )
    if restore_file and st.button("Restore backup"):
        try:
            import_collection(restore_file)
            st.success("Collection restored.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not restore this backup: {exc}")

if st.session_state.location or use_manual:
    scan_if_needed(lat, lon)

# ----------------------------
# SCOREBOARD + PROGRESS
# ----------------------------
nearby_count = len(st.session_state.nearby_birds)
caught_species = unique_species_names()

st.divider()
m1, m2, m3 = st.columns(3)
m1.metric("Level", st.session_state.level)
m2.metric("XP", st.session_state.score)
m3.metric("Species", len(caught_species))

st.progress(
    level_progress(st.session_state.score),
    text=f"{st.session_state.score % XP_PER_LEVEL} / {XP_PER_LEVEL} XP to next level",
)

if nearby_count:
    nearby_name_set = {normalize_name(bird["name"]) for bird in st.session_state.nearby_birds}
    caught_local = len(caught_species & nearby_name_set)
    local_progress = caught_local / nearby_count if nearby_count else 0
    st.progress(
        local_progress,
        text=f"Local field guide: {caught_local} of {nearby_count} nearby species discovered",
    )

# ----------------------------
# DAILY TARGET
# ----------------------------
target = None
if st.session_state.nearby_birds and (st.session_state.location or use_manual):
    target = daily_target(st.session_state.nearby_birds, lat, lon)

if target:
    target_caught = normalize_name(target["name"]) in caught_species
    st.markdown(
        f"""
        <div class="ws-target">
          <div class="ws-kicker">TODAY'S TARGET</div>
          <div style="font-size:1.12rem;font-weight:800;margin-top:.2rem;">
            {"✅ " if target_caught else "🎯 "}{target["name"]}
          </div>
          <div class="ws-muted">
            Catch it today for +{DAILY_TARGET_BONUS} bonus XP.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ----------------------------
# TABS
# ----------------------------
tab_snap, tab_guide, tab_collection, tab_badges = st.tabs(
    ["📸 Snap", "🪶 Field Guide", "🏆 My Birds", "🎖️ Badges"]
)

# ----------------------------
# SNAP
# ----------------------------
with tab_snap:
    if not (st.session_state.location or use_manual):
        st.info("Get your location first, then WingSnap will be ready.")
    else:
        st.subheader("Photograph a bird")

        camera_mode = st.radio(
            "Photo source",
            ["Camera", "Zoom / Gallery"],
            horizontal=True,
            label_visibility="collapsed",
        )

        photo = None

        if camera_mode == "Zoom / Gallery":
            photo = st.file_uploader(
                "Choose a photo or use your phone's native camera",
                type=["jpg", "jpeg", "png", "webp"],
                key="bird_photo_upload",
            )
        else:
            photo = st.camera_input(
                "Snap a bird",
                label_visibility="collapsed",
                resolution="1080p",
                key="bird_camera",
            )

        if photo:
            image_bytes = photo.getvalue()
            current_hash = photo_hash(image_bytes)
            duplicate_photo = current_hash in st.session_state.processed_photo_hashes

            try:
                image = Image.open(BytesIO(image_bytes)).convert("RGB")
            except Exception:
                st.error("WingSnap couldn't open that image. Try another photo.")
                st.stop()

            st.image(image, caption="Your photo", use_container_width=True)

            if duplicate_photo:
                st.info(
                    "You've already scored this exact photo. You can identify it again, "
                    "but it won't earn XP twice."
                )

            if st.button("🔎 Identify this bird", type="primary"):
                with st.spinner("Looking closely…"):
                    try:
                        result = identify_bird(image, st.session_state.nearby_birds)
                    except Exception as exc:
                        st.error(f"Bird identification failed: {exc}")
                        st.stop()

                if not result.is_bird:
                    st.warning("No real bird was confidently detected in this photo.")
                    if result.reason:
                        st.caption(result.reason)

                elif result.confidence < 0.55 or not result.common_name.strip():
                    st.warning("A bird may be present, but the species isn't clear enough yet.")
                    st.caption("Try a closer, brighter, or sharper photo.")
                    if result.reason:
                        st.caption(result.reason)

                else:
                    local_match = find_local_match(
                        result.common_name,
                        st.session_state.nearby_birds,
                    )

                    final_name = (
                        local_match["name"]
                        if local_match
                        else result.common_name.strip()
                    )

                    scientific_name = (
                        local_match.get("scientific_name", "")
                        if local_match
                        else result.scientific_name.strip()
                    )

                    is_new_species = normalize_name(final_name) not in unique_species_names()
                    is_target = bool(
                        target
                        and normalize_name(final_name) == normalize_name(target["name"])
                    )

                    rarity, xp, bonuses = calculate_award(
                        result=result,
                        local_match=local_match,
                        is_new_species=is_new_species,
                        is_daily_target=is_target,
                    )

                    # Exact same photo can be identified again, but doesn't score twice.
                    awarded_xp = 0 if duplicate_photo else xp

                    st.markdown(
                        f"""
                        <div class="ws-success">
                          <div class="ws-kicker">
                            {"NEW SPECIES!" if is_new_species else "BIRD IDENTIFIED"}
                          </div>
                          <h2 style="margin:.25rem 0;">{final_name}</h2>
                          <div><b>Game rarity: {rarity}</b></div>
                          <div style="font-size:1.1rem;font-weight:800;margin:.15rem 0;">
                            {"No new XP — photo already scored" if duplicate_photo else f"+{awarded_xp} XP"}
                          </div>
                          <div class="ws-muted">
                            Identification confidence: {result.confidence:.0%}
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    if scientific_name:
                        st.caption(scientific_name)

                    if local_match:
                        if local_match.get("notable"):
                            st.success("⭐ eBird currently lists this as a notable nearby observation.")
                        else:
                            st.success("Matches a species recently reported nearby by eBird.")
                    else:
                        st.info(
                            "This species wasn't in the recent nearby eBird list. "
                            "That does not automatically make the identification wrong."
                        )

                    if bonuses and not duplicate_photo:
                        st.caption("Bonuses: " + " · ".join(bonuses))

                    if not duplicate_photo:
                        save_catch(
                            image_bytes=image_bytes,
                            image_hash=current_hash,
                            name=final_name,
                            scientific_name=scientific_name,
                            rarity=rarity,
                            xp=awarded_xp,
                            confidence=result.confidence,
                            lat=lat,
                            lon=lon,
                            eBird_notable=bool(local_match and local_match.get("notable")),
                            daily_target_hit=is_target,
                        )
                        st.balloons()

# ----------------------------
# FIELD GUIDE
# ----------------------------
with tab_guide:
    if not st.session_state.nearby_birds:
        st.info("Share your location to build your local field guide.")
    else:
        caught_now = unique_species_names()
        nearby_birds = st.session_state.nearby_birds

        discovered = [
            bird for bird in nearby_birds
            if normalize_name(bird["name"]) in caught_now
        ]
        undiscovered = [
            bird for bird in nearby_birds
            if normalize_name(bird["name"]) not in caught_now
        ]

        st.subheader(f"{len(discovered)} / {len(nearby_birds)} local species discovered")

        show = st.radio(
            "Field guide filter",
            ["All", "Discovered", "Still to find"],
            horizontal=True,
            label_visibility="collapsed",
        )

        if show == "Discovered":
            guide_rows = discovered
        elif show == "Still to find":
            guide_rows = undiscovered
        else:
            guide_rows = nearby_birds

        for bird in guide_rows:
            is_caught = normalize_name(bird["name"]) in caught_now
            icon = "✅" if is_caught else "◯"
            notable = " · ⭐ Notable" if bird["notable"] else ""
            seen = f" · last report {bird['last_seen']}" if bird["last_seen"] else ""

            st.markdown(
                f"""
                <div class="ws-card">
                  <div style="font-weight:800;">{icon} {bird["name"]}</div>
                  <div class="ws-muted">{bird.get("scientific_name","")}{notable}{seen}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

# ----------------------------
# COLLECTION
# ----------------------------
with tab_collection:
    if not st.session_state.inventory:
        st.info("Your collection is empty. Your first bird will appear here.")
    else:
        # Show one card per species, newest catch wins.
        species_seen = {}
        for item in reversed(st.session_state.inventory):
            species_seen.setdefault(normalize_name(item["name"]), item)

        st.subheader(f"{len(species_seen)} species in your flock")

        for bird in species_seen.values():
            when = datetime.fromisoformat(bird["caught_at"]).strftime(
                "%b %d, %Y · %I:%M %p"
            )

            with st.container(border=True):
                image_col, text_col = st.columns([1.15, 2.5])

                with image_col:
                    if bird.get("image_bytes"):
                        st.image(bird["image_bytes"], use_container_width=True)
                    else:
                        st.markdown("### 🐦")

                with text_col:
                    st.markdown(f"### {bird['name']}")
                    if bird.get("scientific_name"):
                        st.caption(bird["scientific_name"])
                    st.write(f"**{bird['rarity']}** · +{bird['xp']} XP")
                    st.caption(
                        f"{when} · confidence {bird.get('confidence', 0):.0%}"
                    )

# ----------------------------
# BADGES
# ----------------------------
with tab_badges:
    badges = badge_statuses()
    unlocked = sum(1 for badge in badges if badge["unlocked"])

    st.subheader(f"{unlocked} / {len(badges)} badges unlocked")

    for badge in badges:
        status = "UNLOCKED" if badge["unlocked"] else "LOCKED"
        opacity = "1" if badge["unlocked"] else ".45"

        st.markdown(
            f"""
            <div class="ws-card" style="opacity:{opacity};">
              <div style="display:flex;gap:.8rem;align-items:center;">
                <div style="font-size:1.8rem;">{badge["icon"]}</div>
                <div>
                  <div style="font-weight:850;">{badge["name"]}</div>
                  <div class="ws-muted">{badge["description"]} · {status}</div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.divider()
st.caption(
    "WingSnap v3.1 · Nearby sightings from eBird · Photo identification with Gemini"
)
