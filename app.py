import os
from datetime import datetime
from io import BytesIO

import requests
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field
from streamlit_geolocation import streamlit_geolocation

st.set_page_config(
    page_title="WingSnap",
    page_icon="🦅",
    layout="centered",
    initial_sidebar_state="collapsed",
)

try:
    EBIRD_API_KEY = st.secrets["EBIRD_API_KEY"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except Exception:
    st.error(
        "WingSnap is missing its API secrets. Add EBIRD_API_KEY and "
        "GEMINI_API_KEY in Streamlit Community Cloud → App settings → Secrets."
    )
    st.stop()

client = genai.Client(api_key=GEMINI_API_KEY)

DEFAULT_LAT = 40.7812
DEFAULT_LON = -73.9665
SEARCH_RADIUS_KM = 8
EBIRD_BACK_DAYS = 7
MODEL_NAME = "gemini-3.7-flash"

st.markdown(
    """
    <style>
      .block-container {max-width:760px;padding-top:1rem;padding-bottom:4rem;}
      #MainMenu, footer {visibility:hidden;}
      [data-testid="stMetric"] {
          background:rgba(248,249,250,.92);
          border:1px solid rgba(0,0,0,.08);
          padding:.75rem;border-radius:16px;
      }
      .ws-card {
          border:1px solid rgba(0,0,0,.09);border-radius:18px;
          padding:1rem 1.1rem;margin:.65rem 0;background:rgba(255,255,255,.92);
      }
      .ws-success {
          border:2px solid #54a96b;border-radius:20px;padding:1.1rem;
          text-align:center;background:rgba(225,247,232,.8);
      }
      .ws-muted {opacity:.72;font-size:.92rem;}
      .stButton button {border-radius:999px;font-weight:700;width:100%;}
    </style>
    """,
    unsafe_allow_html=True,
)

defaults = {
    "score": 0,
    "level": 1,
    "inventory": [],
    "nearby_birds": [],
    "location": None,
    "last_scan_key": None,
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


class BirdIdentification(BaseModel):
    is_bird: bool = Field(description="True only if a real living bird is visible.")
    common_name: str = Field(description="Common English name, or empty if unknown.")
    scientific_name: str = Field(description="Scientific name if reasonably known, otherwise empty.")
    confidence: float = Field(ge=0.0, le=1.0)
    local_candidate: bool
    reason: str = Field(description="Very short explanation, especially when confidence is low.")


def normalize_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def request_json(url: str, *, headers: dict, params: dict) -> list:
    response = requests.get(url, headers=headers, params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected eBird response.")
    return data


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
                "rarity": "LEGENDARY" if is_notable else "COMMON",
                "xp": 125 if is_notable else 20,
                "last_seen": row.get("obsDt") or "",
                "count": row.get("howMany"),
            }
        )

    birds.sort(key=lambda bird: (not bird["notable"], bird["name"]))
    return birds


def identify_bird(image: Image.Image, nearby_birds: list[dict]) -> BirdIdentification:
    candidates = [bird["name"] for bird in nearby_birds[:120]]
    candidate_text = ", ".join(candidates) if candidates else "No local candidate list available."

    prompt = f"""
You are identifying a bird for a bird-watching game.

Nearby species reported by eBird within roughly {SEARCH_RADIUS_KM} km during the last
{EBIRD_BACK_DAYS} days include:
{candidate_text}

Rules:
- First decide whether a REAL, LIVING BIRD is actually visible.
- Do not identify rugs, toys, drawings, patterns, statues, screens, or other objects as birds.
- If the bird is too small, obstructed, or blurry for reliable species identification,
  keep is_bird=true but lower confidence rather than guessing.
- Prefer a species in the nearby candidate list when the visual evidence supports it.
- A species outside the candidate list is allowed when visual evidence is strong.
- Use the common English bird name.
- local_candidate must reflect whether the final common_name is in the supplied list.
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


def award_for(result: BirdIdentification, match: dict | None, is_new_species: bool):
    if match and match.get("notable"):
        rarity, base_xp = "LEGENDARY", 125
    elif match:
        rarity, base_xp = "COMMON", 20
    else:
        rarity, base_xp = "UNCOMMON", 35

    if result.confidence >= 0.90:
        base_xp += 10
    if is_new_species:
        base_xp += 25
    return rarity, base_xp


def save_catch(name, scientific_name, rarity, xp, confidence, lat, lon):
    caught_at = datetime.now().isoformat(timespec="seconds")
    st.session_state.inventory.append(
        {
            "id": f"{normalize_name(name)}|{caught_at}",
            "name": name,
            "scientific_name": scientific_name,
            "rarity": rarity,
            "xp": xp,
            "confidence": confidence,
            "caught_at": caught_at,
            "lat": round(lat, 4) if lat is not None else None,
            "lon": round(lon, 4) if lon is not None else None,
        }
    )
    st.session_state.score += xp
    st.session_state.level = 1 + (st.session_state.score // 500)


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


if os.path.exists("logo.png"):
    cols = st.columns([1, 2.2, 1])
    with cols[1]:
        st.image("logo.png", use_container_width=True)
else:
    st.title("🦅 WingSnap")

st.caption("See it. Snap it. Add it to your flock.")

st.subheader("📍 Your birding area")
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
        st.caption(f"Using your device location (about {accuracy:.0f} m accuracy).")
    else:
        st.caption("Using your device location.")
else:
    st.info(
        "Tap **Get Location** above so WingSnap can find birds recently reported near you. "
        "If location permission is unavailable, use the fallback in Settings."
    )
    lat, lon = DEFAULT_LAT, DEFAULT_LON

with st.expander("⚙️ Settings & fallback location"):
    use_manual = st.toggle("Use manual location", value=False)
    manual_lat = st.number_input("Latitude", value=float(lat), format="%.5f")
    manual_lon = st.number_input("Longitude", value=float(lon), format="%.5f")

    if use_manual:
        lat, lon = manual_lat, manual_lon

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Refresh nearby birds"):
            st.session_state.last_scan_key = None
            fetch_local_birds.clear()
    with col_b:
        if st.button("Reset this session"):
            for key, value in defaults.items():
                st.session_state[key] = value
            st.rerun()

if st.session_state.location or use_manual:
    scan_if_needed(lat, lon)

st.divider()
a, b, c = st.columns(3)
a.metric("Level", st.session_state.level)
b.metric("XP", st.session_state.score)
unique_species = len({normalize_name(x["name"]) for x in st.session_state.inventory})
c.metric("Species", unique_species)

if st.session_state.nearby_birds:
    st.caption(f"🐦 {len(st.session_state.nearby_birds)} species reported nearby recently")

tab_snap, tab_collection, tab_nearby = st.tabs(["📸 Snap", "🏆 My Birds", "🗺️ Nearby"])

with tab_snap:
    if not (st.session_state.location or use_manual):
        st.info("Get your location first, then WingSnap will be ready to identify birds.")
    else:
        st.subheader("Take a bird photo")
        camera_mode = st.radio(
            "Photo source",
            ["Camera", "Zoom / Gallery"],
            horizontal=True,
            label_visibility="collapsed",
        )

        photo = None
        if camera_mode == "Zoom / Gallery":
            photo = st.file_uploader(
                "Choose a photo or use your phone's camera",
                type=["jpg", "jpeg", "png", "webp"],
            )
        else:
            photo = st.camera_input("Snap a bird", label_visibility="collapsed")

        if photo:
            try:
                image = Image.open(BytesIO(photo.getvalue())).convert("RGB")
            except Exception:
                st.error("WingSnap couldn't open that image. Try another photo.")
                st.stop()

            st.image(image, caption="Your photo", use_container_width=True)

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
                    match = find_local_match(result.common_name, st.session_state.nearby_birds)
                    final_name = match["name"] if match else result.common_name.strip()
                    scientific_name = match.get("scientific_name", "") if match else result.scientific_name.strip()

                    already_caught = normalize_name(final_name) in {
                        normalize_name(item["name"]) for item in st.session_state.inventory
                    }
                    is_new_species = not already_caught
                    rarity, xp = award_for(result, match, is_new_species)

                    st.markdown(
                        f"""
                        <div class="ws-success">
                          <div style="font-size:.8rem;font-weight:800;letter-spacing:.08em;">
                            {"NEW SPECIES!" if is_new_species else "BIRD IDENTIFIED"}
                          </div>
                          <h2 style="margin:.25rem 0;">{final_name}</h2>
                          <div><b>{rarity}</b> · +{xp} XP</div>
                          <div class="ws-muted">Identification confidence: {result.confidence:.0%}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    if scientific_name:
                        st.caption(scientific_name)

                    if match:
                        st.success("Matches a species reported nearby by eBird.")
                    else:
                        st.info(
                            "This species was not in the recent nearby eBird list. "
                            "That doesn't necessarily mean the identification is wrong."
                        )

                    save_catch(
                        final_name,
                        scientific_name,
                        rarity,
                        xp,
                        result.confidence,
                        lat,
                        lon,
                    )
                    st.balloons()

with tab_collection:
    if not st.session_state.inventory:
        st.info("Your collection is empty. Your first bird will appear here.")
    else:
        species_seen = {}
        for item in reversed(st.session_state.inventory):
            species_seen.setdefault(normalize_name(item["name"]), item)

        st.subheader(f"{len(species_seen)} species discovered")
        for bird in species_seen.values():
            when = datetime.fromisoformat(bird["caught_at"]).strftime("%b %d, %Y · %I:%M %p")
            st.markdown(
                f"""
                <div class="ws-card">
                  <div style="display:flex;justify-content:space-between;gap:1rem;">
                    <div>
                      <div style="font-size:1.05rem;font-weight:800;">{bird["name"]}</div>
                      <div class="ws-muted">{bird.get("scientific_name","")}</div>
                      <div class="ws-muted">{when}</div>
                    </div>
                    <div style="text-align:right;">
                      <b>{bird["rarity"]}</b><br>
                      +{bird["xp"]} XP
                    </div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

with tab_nearby:
    if not st.session_state.nearby_birds:
        st.info("Share your location to see recently reported birds nearby.")
    else:
        st.subheader("Recently reported nearby")
        for bird in st.session_state.nearby_birds[:50]:
            notable = " ⭐ Notable" if bird["notable"] else ""
            last_seen = f" · {bird['last_seen']}" if bird["last_seen"] else ""
            st.markdown(
                f"**{bird['name']}**{notable}  \n"
                f"<span class='ws-muted'>{bird.get('scientific_name','')}{last_seen}</span>",
                unsafe_allow_html=True,
            )

st.divider()
st.caption("WingSnap v3 prototype · eBird context + Gemini vision")
