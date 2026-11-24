import os
import hmac
import hashlib
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# ---------------- CONFIGURATION ----------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
API_KEY = os.getenv("OMDB_API_KEY")
DB_PATH = BASE_DIR / "users.db"
CSV_PATH = BASE_DIR / "movies.csv"

LANG_COLUMNS = {
    "Telugu": "telugu_url",
    "Hindi": "hindi_url",
    "Tamil": "tamil_url",
    "English": "english_url"
}

st.set_page_config(
    page_title="MOVIEZZ FLIX",
    page_icon="🎬",
    layout="wide"
)

# ---------------- DATABASE & AUTH ----------------

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def init_database():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                movie_title TEXT NOT NULL,
                UNIQUE(username, movie_title)
            )
        """)

def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        600000
    ).hex()

def register_user(username: str, password: str) -> bool:
    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                (username, salt, password_hash)
            )
        return True
    except sqlite3.IntegrityError:
        return False

def verify_user(username: str, password: str) -> bool:
    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT salt, password_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()

    if user is None:
        return False

    salt, stored_hash = user
    entered_hash = hash_password(password, salt)
    return hmac.compare_digest(entered_hash, stored_hash)

def add_to_watchlist(username: str, movie_title: str):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (username, movie_title) VALUES (?, ?)",
            (username, movie_title)
        )

def get_watchlist(username: str) -> list[str]:
    with get_db_connection() as conn:
        data = conn.execute(
            "SELECT movie_title FROM watchlist WHERE username = ? ORDER BY id DESC",
            (username,)
        ).fetchall()
    return [row[0] for row in data]

def remove_from_watchlist(username: str, movie_title: str):
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE username = ? AND movie_title = ?",
            (username, movie_title)
        )

init_database()

# ---------------- SESSION STATE ----------------

if "logged_in_user" not in st.session_state:
    st.session_state.logged_in_user = None

if "selected_movie" not in st.session_state:
    st.session_state.selected_movie = None

if "is_playing" not in st.session_state:
    st.session_state.is_playing = False

if "selected_lang" not in st.session_state:
    st.session_state.selected_lang = "Telugu"

# ---------------- NETFLIX STYLE CSS ----------------

st.markdown("""
<style>
.stApp {
    background: #080808;
    color: white;
}
header[data-testid="stHeader"] {
    background: transparent;
}
.block-container {
    padding-top: 1rem;
    max-width: 1400px;
}
h1, h2, h3, h4, p, label, span {
    color: white !important;
}
.navbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #080808;
    padding: 15px 25px;
    border-bottom: 1px solid #333;
}
.logo {
    color: #e50914;
    font-size: 32px;
    font-weight: 900;
    letter-spacing: 2px;
}
.hero {
    padding: 70px 45px;
    margin-top: 15px;
    border-radius: 12px;
    background:
        linear-gradient(90deg, rgba(0,0,0,0.95), rgba(0,0,0,0.6), rgba(0,0,0,0.2)),
        url("https://images.unsplash.com/photo-1489599849927-2ee91cede3ba");
    background-size: cover;
    background-position: center;
}
.hero h1 {
    font-size: 46px;
    font-weight: 900;
}
.stButton > button {
    background: #e50914;
    color: white;
    border: none;
    border-radius: 6px;
    font-weight: bold;
    transition: 0.2s ease-in-out;
}
.stButton > button:hover {
    background: #b20710;
    transform: scale(1.02);
}
div[data-testid="stImage"] img {
    border-radius: 9px;
}
hr {
    border-color: #333;
}
</style>
""", unsafe_allow_html=True)

# ---------------- LOGIN / SIGNUP GATE ----------------

if st.session_state.logged_in_user is None:
    st.markdown("""
        <h1 style="text-align:center; color:#e50914 !important; font-size:48px; margin-top:30px;">
            MOVIEZZ FLIX
        </h1>
        <p style="text-align:center; color:#ccc !important;">
            Unlimited Movies. Endless Entertainment.
        </p>
    """, unsafe_allow_html=True)

    col_left, col_center, col_right = st.columns([1, 1.5, 1])

    with col_center:
        login_tab, signup_tab = st.tabs(["🔐 Login", "📝 Sign Up"])

        with login_tab:
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Login", use_container_width=True):
                    if username.strip() and password:
                        if verify_user(username.strip(), password):
                            st.session_state.logged_in_user = username.strip()
                            st.rerun()
                        else:
                            st.error("Invalid username or password.")
                    else:
                        st.warning("Please enter username and password.")

        with signup_tab:
            with st.form("signup_form"):
                new_username = st.text_input("Choose Username")
                new_password = st.text_input("Create Password (min 8 chars)", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                if st.form_submit_button("Create Account", use_container_width=True):
                    if not new_username.strip():
                        st.warning("Please enter a username.")
                    elif len(new_password) < 8:
                        st.warning("Password must contain at least 8 characters.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    elif register_user(new_username.strip(), new_password):
                        st.success("Account created! Switch to Login tab.")
                    else:
                        st.error("Username already exists.")

    st.stop()

# ---------------- SIDEBAR (USER & WATCHLIST) ----------------

current_user = st.session_state.logged_in_user

with st.sidebar:
    st.markdown(f"### 👤 {current_user}")
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.logged_in_user = None
        st.session_state.selected_movie = None
        st.session_state.is_playing = False
        st.rerun()

    st.divider()
    st.markdown("### ❤️ My Watchlist")

    user_watchlist = get_watchlist(current_user)
    if user_watchlist:
        for item in user_watchlist:
            w_col1, w_col2 = st.columns([4, 1])
            with w_col1:
                if st.button(f"🎬 {item}", key=f"wl_view_{item}", use_container_width=True):
                    st.session_state.selected_movie = item
                    st.session_state.is_playing = True
                    st.rerun()
            with w_col2:
                if st.button("❌", key=f"wl_del_{item}"):
                    remove_from_watchlist(current_user, item)
                    st.rerun()
    else:
        st.caption("Your watchlist is empty. Add movies to watch later!")

# ---------------- DATA & API HELPERS ----------------

@st.cache_data(ttl=3600)
def get_movie_details(title: str):
    if not API_KEY:
        return None
    try:
        response = requests.get(
            "https://www.omdbapi.com/",
            params={"apikey": API_KEY, "t": title, "plot": "short"},
            timeout=8
        )
        data = response.json()
        return data if data.get("Response") == "True" else None
    except requests.RequestException:
        return None

@st.cache_data
def load_movies():
    df = pd.read_csv(CSV_PATH)
    df["title"] = df["title"].astype(str).str.strip()
    df["genre"] = df["genre"].fillna("").astype(str)
    for col in LANG_COLUMNS.values():
        if col not in df.columns:
            df[col] = ""
        else:
            df[col] = df[col].fillna("").astype(str).str.strip()
    return df

try:
    movies = load_movies()
except FileNotFoundError:
    st.error("`movies.csv` file not found!")
    st.stop()
except Exception as e:
    st.error(f"Dataset error: {e}")
    st.stop()

def get_available_languages(movie_row: pd.Series) -> dict[str, str]:
    available = {}
    for lang_name, col_name in LANG_COLUMNS.items():
        url = str(movie_row.get(col_name, "")).strip()
        if url and url.lower() != "nan":
            available[lang_name] = url
    return available

def get_recommendations(selected_title: str, df: pd.DataFrame, limit: int = 6) -> pd.DataFrame:
    row = df[df["title"] == selected_title]
    if row.empty:
        return pd.DataFrame()

    raw_genres = row["genre"].iloc[0]
    genres = [g.strip().lower() for g in raw_genres.split(",") if g.strip()]

    if not genres:
        return pd.DataFrame()

    def genre_score(movie_genres: str) -> int:
        m_list = [g.strip().lower() for g in movie_genres.split(",")]
        return len(set(genres) & set(m_list))

    candidates = df[df["title"] != selected_title].copy()
    candidates["score"] = candidates["genre"].apply(genre_score)
    matched = candidates[candidates["score"] > 0].sort_values(by="score", ascending=False)
    return matched.head(limit)

# ---------------- MAIN UI: NAVBAR & HERO ----------------

st.markdown("""
<div class="navbar">
    <div class="logo">MOVIEZZ FLIX</div>
    <div>HOME &nbsp;&nbsp; MOVIES &nbsp;&nbsp; MULTI-AUDIO</div>
</div>

<div class="hero">
    <h1>Unlimited Movies.<br>In Your Favourite Language.</h1>
    <p style="font-size:18px; color:#ddd !important;">
        Switch seamlessly between Telugu, Hindi, Tamil & English audio streams.
    </p>
</div>
""", unsafe_allow_html=True)

# ---------------- ACTIVE MOVIE PLAYER & LANGUAGE SWITCHER ----------------

active_movie = st.session_state.selected_movie

if active_movie:
    st.divider()
    top_bar1, top_bar2 = st.columns([5, 1])
    with top_bar1:
        st.markdown(f"## 🎥 {active_movie}")
    with top_bar2:
        if st.button("✖ Close", use_container_width=True):
            st.session_state.selected_movie = None
            st.session_state.is_playing = False
            st.rerun()

    movie_df_match = movies[movies["title"] == active_movie]
    available_langs = get_available_languages(movie_df_match.iloc[0]) if not movie_df_match.empty else {}

    # Language Selector Bar
    if available_langs:
        lang_options = list(available_langs.keys())
        # Default to previously chosen language if available for this movie, else first available
        default_idx = lang_options.index(st.session_state.selected_lang) if st.session_state.selected_lang in lang_options else 0

        chosen_lang = st.radio(
            "🌐 Choose Language:",
            options=lang_options,
            index=default_idx,
            horizontal=True,
            key=f"lang_selector_{active_movie}"
        )
        st.session_state.selected_lang = chosen_lang
        active_video_url = available_langs[chosen_lang]
    else:
        chosen_lang = st.session_state.selected_lang
        active_video_url = ""

    # Video Player Section
    if st.session_state.is_playing:
        if active_video_url:
            st.caption(f"🔊 Now Playing in **{chosen_lang}**")
            st.video(active_video_url, autoplay=True)
        else:
            st.warning(f"Video link not found in `movies.csv` for {active_movie}.")
            yt_search = f"https://www.youtube.com/results?search_query={quote_plus(f'{active_movie} {chosen_lang} trailer')}"
            st.link_button(f"🔎 Search {chosen_lang} on YouTube", yt_search)

    details = get_movie_details(active_movie)
    col1, col2 = st.columns([1, 2.5])

    with col1:
        if details and details.get("Poster") != "N/A":
            st.image(details["Poster"], use_container_width=True)
        else:
            st.info("Poster unavailable")

    with col2:
        btn_col1, btn_col2, btn_col3 = st.columns(3)

        with btn_col1:
            if st.button(f"▶ Play ({chosen_lang})", key="play_main_btn", use_container_width=True):
                st.session_state.is_playing = True
                st.rerun()

        with btn_col2:
            in_watchlist = active_movie in get_watchlist(current_user)
            if in_watchlist:
                if st.button("💔 Remove Watchlist", use_container_width=True):
                    remove_from_watchlist(current_user, active_movie)
                    st.rerun()
            else:
                if st.button("❤️ + Watchlist", use_container_width=True):
                    add_to_watchlist(current_user, active_movie)
                    st.toast(f"Added '{active_movie}' to your watchlist!")
                    st.rerun()

        with btn_col3:
            yt_fallback = active_video_url if active_video_url else f"https://www.youtube.com/results?search_query={quote_plus(f'{active_movie} {chosen_lang}')}"
            st.link_button("📺 Open YouTube", yt_fallback, use_container_width=True)

        if available_langs:
            st.write(f"**🌐 Available Languages:** {', '.join(available_langs.keys())}")

        if details:
            st.write(f"**📅 Year:** {details.get('Year', 'N/A')}  |  **⭐ IMDb:** {details.get('imdbRating', 'N/A')}  |  **⏱️ Runtime:** {details.get('Runtime', 'N/A')}")
            st.write(f"**🎭 Genre:** {details.get('Genre', 'N/A')}")
            st.write(f"**🎬 Director:** {details.get('Director', 'N/A')}")
            st.write(f"**🌟 Cast:** {details.get('Actors', 'N/A')}")
            st.write("**📝 Plot:**")
            st.write(details.get("Plot", "N/A"))
        elif not movie_df_match.empty:
            st.write(f"**🎭 Genre:** {movie_df_match['genre'].iloc[0]}")

    # Recommendations Section
    st.markdown("### 🍿 Similar Movies You Might Like")
    recs = get_recommendations(active_movie, movies, limit=6)

    if not recs.empty:
        rec_cols = st.columns(3)
        for i, (_, row) in enumerate(recs.iterrows()):
            rec_title = row["title"]
            rec_data = get_movie_details(rec_title)
            rec_langs = list(get_available_languages(row).keys())

            with rec_cols[i % 3]:
                with st.container(border=True):
                    if rec_data and rec_data.get("Poster") != "N/A":
                        st.image(rec_data["Poster"], use_container_width=True)
                    st.markdown(f"#### {rec_title}")
                    st.caption(f"🎭 {row['genre']}")
                    if rec_langs:
                        st.caption(f"🌐 {', '.join(rec_langs)}")
                    if st.button("▶ Open & Play", key=f"rec_btn_{rec_title}", use_container_width=True):
                        st.session_state.selected_movie = rec_title
                        st.session_state.is_playing = True
                        st.rerun()

# ---------------- TRENDING MOVIES ----------------

st.divider()
st.markdown("## 🔥 Trending Pan-India Blockbusters")

trending = [
    "RRR",
    "Pushpa: The Rise",
    "Kalki 2898 AD",
    "KGF: Chapter 2",
    "Salaar"
]

cols = st.columns(5)
for col, name in zip(cols, trending):
    with col:
        data = get_movie_details(name)
        if data and data.get("Poster") != "N/A":
            st.image(data["Poster"], use_container_width=True)
        st.markdown(f"**{name}**")
        rating = data.get("imdbRating", "N/A") if data else "N/A"
        st.caption(f"⭐ IMDb {rating}")
        if st.button("▶ Watch", key=f"trend_{name}", use_container_width=True):
            st.session_state.selected_movie = name
            st.session_state.is_playing = True
            st.rerun()

# ---------------- SEARCH & BROWSE ALL MOVIES ----------------

st.divider()
st.markdown("## 🎬 Browse & Search All Movies")

filter_col1, filter_col2, filter_col3 = st.columns([2, 1, 1])

with filter_col1:
    movie_list = sorted(movies["title"].unique().tolist())
    selected_input = st.selectbox(
        "Quick Search",
        options=[""] + movie_list,
        format_func=lambda x: "🔍 Type any movie name to open..." if x == "" else x,
        label_visibility="collapsed"
    )
    if selected_input:
        if st.button(f"▶ Open '{selected_input}'", use_container_width=True):
            st.session_state.selected_movie = selected_input
            st.session_state.is_playing = True
            st.rerun()

with filter_col2:
    selected_genre = st.selectbox(
        "Filter by Genre",
        options=["All Genres", "Action", "Drama", "Comedy", "Crime", "Thriller", "Sci-Fi", "Adventure", "Fantasy"],
        label_visibility="collapsed"
    )

with filter_col3:
    selected_lang_filter = st.selectbox(
        "Filter by Language",
        options=["All Languages", "Telugu", "Hindi", "Tamil", "English"],
        label_visibility="collapsed"
    )

# Apply Genre & Language Filters
filtered_movies = movies.copy()

if selected_genre != "All Genres":
    filtered_movies = filtered_movies[filtered_movies["genre"].str.contains(selected_genre, case=False, na=False)]

if selected_lang_filter != "All Languages":
    lang_col = LANG_COLUMNS[selected_lang_filter]
    filtered_movies = filtered_movies[filtered_movies[lang_col].str.len() > 0]

st.caption(f"Showing {len(filtered_movies)} movies")

grid_cols = st.columns(4)
for idx, (_, row) in enumerate(filtered_movies.iterrows()):
    m_title = row["title"]
    m_data = get_movie_details(m_title)
    m_langs = list(get_available_languages(row).keys())

    with grid_cols[idx % 4]:
        with st.container(border=True):
            if m_data and m_data.get("Poster") != "N/A":
                st.image(m_data["Poster"], use_container_width=True)
            st.markdown(f"**{m_title}**")
            st.caption(f"🎭 {row['genre']}")
            if m_langs:
                st.caption(f"🌐 {' • '.join(m_langs)}")
            if st.button("▶ Watch Movie", key=f"browse_{m_title}_{idx}", use_container_width=True):
                st.session_state.selected_movie = m_title
                st.session_state.is_playing = True
                if selected_lang_filter != "All Languages":
                    st.session_state.selected_lang = selected_lang_filter
                st.rerun()

# ---------------- FOOTER ----------------

st.divider()
st.markdown("""
<div style="text-align:center; padding:20px; color:#888;">
    <h3 style="color:#e50914 !important;">MOVIEZZ FLIX</h3>
    Your Entertainment. Your World. 🍿
    <br><br>
    © 2026 MOVIEZZ FLIX
</div>
""", unsafe_allow_html=True)