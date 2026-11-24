import os
import csv
import random
import hashlib
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
if not API_KEY:
    try:
        API_KEY = st.secrets.get("OMDB_API_KEY")
    except Exception:
        API_KEY = None

DB_PATH = BASE_DIR / "users.db"
CSV_PATH = BASE_DIR / "movies.csv"

LANG_COLUMNS = {
    "Telugu": "telugu_url",
    "Hindi": "hindi_url",
    "Tamil": "tamil_url",
    "English": "english_url"
}

AVAILABLE_GENRES = [
    "Action", "Thriller", "Horror", "Drama",
    "Comedy", "Crime", "Sci-Fi", "Adventure", "Fantasy", "Romance"
]

AVAILABLE_LANGUAGES = ["Telugu", "Hindi", "Tamil", "English"]

MOOD_FILTERS = {
    "All Moods": [],
    "🔥 Mass & Action": ["Action", "Crime"],
    "😱 Goosebumps & Scares": ["Horror", "Thriller"],
    "🧠 Mind-Bending": ["Sci-Fi", "Adventure", "Fantasy"],
    "💖 Feel-Good & Drama": ["Drama", "Comedy", "Romance"]
}

st.set_page_config(
    page_title="MOVIEZZ FLIX",
    page_icon="🎬",
    layout="wide"
)

# ---------------- FAST DATABASE ----------------

def get_db_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_database():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                username TEXT PRIMARY KEY,
                fav_genres TEXT NOT NULL,
                fav_languages TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS continue_watching (
                username TEXT NOT NULL,
                movie_title TEXT NOT NULL,
                language TEXT NOT NULL,
                progress_mins INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (username, movie_title)
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                movie_title TEXT NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, movie_title)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_parties (
                room_code TEXT PRIMARY KEY,
                host_user TEXT NOT NULL,
                movie_title TEXT NOT NULL,
                language TEXT NOT NULL,
                timestamp_mins INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS party_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_code TEXT NOT NULL,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

def fast_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def register_user(username: str, password: str) -> bool:
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, fast_hash(password))
            )
        return True
    except sqlite3.IntegrityError:
        return False

def verify_user(username: str, password: str) -> bool:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()
    return bool(row and row[0] == fast_hash(password))

def reset_password_direct(username: str, new_password: str) -> bool:
    with get_db_connection() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (fast_hash(new_password), username)
        )
    return cur.rowcount > 0

# Preferences Helpers
def save_user_preferences(username: str, genres: list[str], languages: list[str]):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO user_preferences (username, fav_genres, fav_languages)
            VALUES (?, ?, ?)
            ON CONFLICT(username)
            DO UPDATE SET fav_genres=excluded.fav_genres, fav_languages=excluded.fav_languages
        """, (username, ",".join(genres), ",".join(languages)))

def get_user_preferences(username: str):
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT fav_genres, fav_languages FROM user_preferences WHERE username = ?",
            (username,)
        ).fetchone()
    if row:
        genres = [g.strip() for g in row[0].split(",") if g.strip()]
        langs = [l.strip() for l in row[1].split(",") if l.strip()]
        return genres, langs
    return None, None

# Continue Watching Helpers
def upsert_continue_watching(username: str, movie_title: str, language: str, progress_mins: int = 0):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO continue_watching (username, movie_title, language, progress_mins, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username, movie_title)
            DO UPDATE SET
                language=excluded.language,
                progress_mins=CASE WHEN excluded.progress_mins > 0 THEN excluded.progress_mins ELSE continue_watching.progress_mins END,
                updated_at=CURRENT_TIMESTAMP
        """, (username, movie_title, language, progress_mins))

def update_watch_minutes(username: str, movie_title: str, language: str, mins: int):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO continue_watching (username, movie_title, language, progress_mins, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username, movie_title)
            DO UPDATE SET language=excluded.language, progress_mins=excluded.progress_mins, updated_at=CURRENT_TIMESTAMP
        """, (username, movie_title, language, mins))

def get_continue_watching(username: str):
    with get_db_connection() as conn:
        return conn.execute("""
            SELECT movie_title, language, progress_mins
            FROM continue_watching
            WHERE username = ?
            ORDER BY updated_at DESC
        """, (username,)).fetchall()

def get_movie_progress(username: str, movie_title: str) -> int:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT progress_mins FROM continue_watching WHERE username = ? AND movie_title = ?",
            (username, movie_title)
        ).fetchone()
    return row[0] if row else 0

def remove_continue_watching(username: str, movie_title: str):
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM continue_watching WHERE username = ? AND movie_title = ?",
            (username, movie_title)
        )

# Watchlist Helpers
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

# Reviews & Top Rated Helpers
def save_review(username: str, movie_title: str, rating: int, comment: str):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO reviews (username, movie_title, rating, comment)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username, movie_title)
            DO UPDATE SET rating=excluded.rating, comment=excluded.comment, created_at=CURRENT_TIMESTAMP
        """, (username, movie_title, rating, comment.strip()))

def get_movie_reviews(movie_title: str):
    with get_db_connection() as conn:
        return conn.execute("""
            SELECT username, rating, comment
            FROM reviews
            WHERE movie_title = ?
            ORDER BY created_at DESC
        """, (movie_title,)).fetchall()

def get_average_rating(movie_title: str):
    with get_db_connection() as conn:
        res = conn.execute(
            "SELECT AVG(rating), COUNT(*) FROM reviews WHERE movie_title = ?",
            (movie_title,)
        ).fetchone()
    if res and res[1] > 0:
        return round(res[0], 1), res[1]
    return None, 0

def get_top_rated_movies(limit: int = 4):
    with get_db_connection() as conn:
        return conn.execute("""
            SELECT movie_title, ROUND(AVG(rating), 1) as avg_r, COUNT(*) as cnt
            FROM reviews
            GROUP BY movie_title
            ORDER BY avg_r DESC, cnt DESC
            LIMIT ?
        """, (limit,)).fetchall()

def get_user_review_count(username: str) -> int:
    with get_db_connection() as conn:
        res = conn.execute("SELECT COUNT(*) FROM reviews WHERE username = ?", (username,)).fetchone()
    return res[0] if res else 0

# Watch Party Helpers
def create_or_update_party(room_code: str, host_user: str, movie_title: str, language: str, mins: int = 0):
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO watch_parties (room_code, host_user, movie_title, language, timestamp_mins, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(room_code)
            DO UPDATE SET movie_title=excluded.movie_title, language=excluded.language, timestamp_mins=excluded.timestamp_mins, updated_at=CURRENT_TIMESTAMP
        """, (room_code.upper(), host_user, movie_title, language, mins))

def get_party(room_code: str):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT room_code, host_user, movie_title, language, timestamp_mins FROM watch_parties WHERE room_code = ?",
            (room_code.upper(),)
        ).fetchone()

def add_party_message(room_code: str, username: str, message: str):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO party_messages (room_code, username, message) VALUES (?, ?, ?)",
            (room_code.upper(), username, message.strip())
        )

def get_party_messages(room_code: str):
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT username, message FROM party_messages WHERE room_code = ? ORDER BY id DESC LIMIT 12",
            (room_code.upper(),)
        ).fetchall()

# Admin Analytics Helpers
def get_admin_analytics():
    with get_db_connection() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_watches = conn.execute("SELECT COUNT(*) FROM continue_watching").fetchone()[0]
        total_reviews = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        top_watched = conn.execute("""
            SELECT movie_title, COUNT(*) as views
            FROM continue_watching
            GROUP BY movie_title
            ORDER BY views DESC
            LIMIT 6
        """).fetchall()
        lang_stats = conn.execute("""
            SELECT language, COUNT(*) as cnt
            FROM continue_watching
            GROUP BY language
            ORDER BY cnt DESC
        """).fetchall()
        users_list = conn.execute("""
            SELECT
                u.id,
                u.username,
                COALESCE(p.fav_genres, 'Not Set') as genres,
                COALESCE(p.fav_languages, 'Not Set') as languages,
                (SELECT COUNT(*) FROM watchlist w WHERE w.username = u.username) as saved_count,
                (SELECT COUNT(*) FROM reviews r WHERE r.username = u.username) as review_count
            FROM users u
            LEFT JOIN user_preferences p ON u.username = p.username
            ORDER BY u.id ASC
        """).fetchall()
    return total_users, total_watches, total_reviews, top_watched, lang_stats, users_list

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

if "resume_seconds" not in st.session_state:
    st.session_state.resume_seconds = 0

if "onboarding_step" not in st.session_state:
    st.session_state.onboarding_step = 1

if "temp_genres" not in st.session_state:
    st.session_state.temp_genres = []

if "editing_prefs" not in st.session_state:
    st.session_state.editing_prefs = False

if "auth_page" not in st.session_state:
    st.session_state.auth_page = "login"

if "active_mood" not in st.session_state:
    st.session_state.active_mood = "All Moods"

if "party_room" not in st.session_state:
    st.session_state.party_room = None

if "show_admin_panel" not in st.session_state:
    st.session_state.show_admin_panel = False

# ---------------- CLASSIC CINEMATIC CSS ----------------

st.markdown("""
<style>
.stApp {
    background:
        radial-gradient(circle at 50% 20%, rgba(229, 9, 20, 0.22), rgba(10, 10, 15, 0.92) 55%, #050508 100%),
        linear-gradient(to bottom, rgba(5, 5, 10, 0.72), rgba(5, 5, 10, 0.95)),
        url("https://images.unsplash.com/photo-1536440136628-849c177e76a1?auto=format&fit=crop&w=1920&q=80");
    background-size: cover;
    background-position: center;
    background-attachment: fixed;
    color: white;
}
header[data-testid="stHeader"] {
    background: transparent;
}
.block-container {
    padding-top: 1.2rem;
    max-width: 1400px;
}
h1, h2, h3, h4, p, label, span {
    color: white !important;
}
.navbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: rgba(12, 12, 18, 0.85);
    backdrop-filter: blur(10px);
    padding: 15px 25px;
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.08);
}
.logo {
    color: #e50914;
    font-size: 32px;
    font-weight: 900;
    letter-spacing: 3px;
    text-shadow: 0 0 18px rgba(229, 9, 20, 0.55);
}
.hero {
    padding: 48px 42px;
    margin-top: 15px;
    border-radius: 14px;
    background:
        linear-gradient(90deg, rgba(8,8,12,0.95), rgba(8,8,12,0.65), rgba(229,9,20,0.18)),
        url("https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?auto=format&fit=crop&w=1600&q=80");
    background-size: cover;
    background-position: center;
    border: 1px solid rgba(255, 255, 255, 0.1);
}
.hero h1 {
    font-size: 42px;
    font-weight: 900;
}
.stButton > button {
    background: linear-gradient(135deg, #e50914, #b20710);
    color: white;
    border: none;
    border-radius: 8px;
    font-weight: bold;
    letter-spacing: 0.4px;
    box-shadow: 0 4px 14px rgba(229, 9, 20, 0.35);
}
.stButton > button:hover {
    background: linear-gradient(135deg, #ff1e27, #c90812);
    box-shadow: 0 6px 20px rgba(229, 9, 20, 0.55);
}
div[data-testid="stImage"] img {
    border-radius: 9px;
}
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: rgba(255, 255, 255, 0.14) !important;
    background: rgba(15, 15, 22, 0.82) !important;
    backdrop-filter: blur(14px);
    border-radius: 14px;
    box-shadow: 0 12px 35px rgba(0, 0, 0, 0.65);
}
hr {
    border-color: rgba(255, 255, 255, 0.12);
}
</style>
""", unsafe_allow_html=True)

# ---------------- DATA & API HELPERS ----------------

@st.cache_data(ttl=86400, show_spinner=False)
def get_movie_details(title: str):
    if not API_KEY:
        return None
    try:
        response = requests.get(
            "https://www.omdbapi.com/",
            params={"apikey": API_KEY, "t": title, "plot": "short"},
            timeout=5
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

def append_movie_to_csv(title: str, genre: str, te_url: str, hi_url: str, ta_url: str, en_url: str):
    file_exists = CSV_PATH.exists()
    with open(CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["title", "genre", "telugu_url", "hindi_url", "tamil_url", "english_url"])
        writer.writerow([title.strip(), genre.strip(), te_url.strip(), hi_url.strip(), ta_url.strip(), en_url.strip()])
    load_movies.clear()

# ---------------- LOGIN / SIGNUP / SEPARATE FORGOT PASSWORD PAGE ----------------

if st.session_state.logged_in_user is None:
    st.markdown("""
        <div style="text-align:center; margin-top:25px; margin-bottom:15px;">
            <span style="background:rgba(229,9,20,0.18); border:1px solid rgba(229,9,20,0.5); color:#ff4d56; padding:5px 14px; border-radius:20px; font-size:12px; letter-spacing:2px; font-weight:700;">
                PREMIUM STREAMING EXPERIENCE
            </span>
            <h1 style="color:#e50914 !important; font-size:54px; font-weight:900; letter-spacing:4px; margin:10px 0 4px 0; text-shadow: 0 4px 25px rgba(229,9,20,0.5);">
                MOVIEZZ FLIX
            </h1>
            <p style="color:#d0d0d8 !important; font-size:17px; letter-spacing:0.5px;">
                Blockbusters • Multi-Audio • Watch Party • Curated For You
            </p>
        </div>
    """, unsafe_allow_html=True)

    _, col_center, _ = st.columns([1, 1.35, 1])

    with col_center:
        with st.container(border=True):
            if st.session_state.auth_page == "login":
                login_tab, signup_tab = st.tabs(["🔐 Sign In", "✨ Create Account"])

                with login_tab:
                    with st.form("login_form"):
                        username = st.text_input("Username", placeholder="Enter your username")
                        password = st.text_input("Password", type="password", placeholder="Enter your password")
                        if st.form_submit_button("Sign In", use_container_width=True):
                            if username.strip() and password:
                                if verify_user(username.strip(), password):
                                    st.session_state.logged_in_user = username.strip()
                                    st.session_state.onboarding_step = 1
                                    if username.strip().lower() == "admin":
                                        st.session_state.show_admin_panel = True
                                    st.rerun()
                                else:
                                    st.error("Invalid username or password.")
                            else:
                                st.warning("Enter username and password.")

                    _, fp_col = st.columns([1.3, 1])
                    with fp_col:
                        if st.button("🔑 Forgot Password?", key="go_fp_page", use_container_width=True):
                            st.session_state.auth_page = "forgot"
                            st.rerun()

                with signup_tab:
                    with st.form("signup_form"):
                        new_username = st.text_input("Choose Username", placeholder="e.g. kowshik")
                        new_password = st.text_input("Create Password (min 4 chars)", type="password")
                        confirm_password = st.text_input("Confirm Password", type="password")
                        if st.form_submit_button("Start Watching 🚀", use_container_width=True):
                            if not new_username.strip():
                                st.warning("Enter a username.")
                            elif len(new_password) < 4:
                                st.warning("Password must be at least 4 characters.")
                            elif new_password != confirm_password:
                                st.error("Passwords do not match.")
                            elif register_user(new_username.strip(), new_password):
                                st.session_state.logged_in_user = new_username.strip()
                                st.session_state.onboarding_step = 1
                                if new_username.strip().lower() == "admin":
                                    st.session_state.show_admin_panel = True
                                st.rerun()
                            else:
                                st.error("Username already exists.")

            elif st.session_state.auth_page == "forgot":
                st.markdown("### 🔄 Reset Your Password")
                st.caption("Enter your username and set a new password to recover your account.")

                with st.form("reset_form"):
                    reset_user = st.text_input("Username", placeholder="Enter your registered username")
                    new_pass = st.text_input("New Password (min 4 chars)", type="password")
                    confirm_new_pass = st.text_input("Confirm New Password", type="password")
                    if st.form_submit_button("Update Password ✅", use_container_width=True):
                        if not reset_user.strip():
                            st.warning("Enter your username.")
                        elif len(new_pass) < 4:
                            st.warning("Password must be at least 4 characters.")
                        elif new_pass != confirm_new_pass:
                            st.error("Passwords do not match.")
                        elif reset_password_direct(reset_user.strip(), new_pass):
                            st.success("✅ Password updated successfully! Click 'Back to Sign In' below.")
                        else:
                            st.error("Username not found.")

                if st.button("⬅️ Back to Sign In", use_container_width=True):
                    st.session_state.auth_page = "login"
                    st.rerun()

    st.stop()

current_user = st.session_state.logged_in_user
saved_genres, saved_langs = get_user_preferences(current_user)

# Auto-skip onboarding wizard for 'admin' & open Admin Dashboard directly
if current_user.lower() == "admin" and (saved_genres is None or saved_langs is None):
    save_user_preferences(current_user, AVAILABLE_GENRES, AVAILABLE_LANGUAGES)
    saved_genres, saved_langs = AVAILABLE_GENRES, AVAILABLE_LANGUAGES
    st.session_state.show_admin_panel = True

# ---------------- 2-STEP PREFERENCE ONBOARDING WIZARD ----------------

if saved_genres is None or saved_langs is None or st.session_state.editing_prefs:
    st.markdown("""
        <h1 style="text-align:center; color:#e50914 !important; margin-top:10px;">
            MOVIEZZ FLIX
        </h1>
    """, unsafe_allow_html=True)

    _, center_box, _ = st.columns([1, 2, 1])

    with center_box:
        with st.container(border=True):
            if st.session_state.onboarding_step == 1:
                st.progress(50, text="Step 1 of 2: Choose Your Favourite Genres")
                st.markdown(f"### 👋 Welcome, {current_user}! What do you love watching?")

                genre_icons = {
                    "Action": "💥 Action",
                    "Thriller": "🔪 Thriller",
                    "Horror": "👻 Horror",
                    "Drama": "🎭 Drama",
                    "Comedy": "😂 Comedy",
                    "Crime": "🕵️ Crime",
                    "Sci-Fi": "🚀 Sci-Fi",
                    "Adventure": "🧭 Adventure",
                    "Fantasy": "🧙 Fantasy",
                    "Romance": "❤️ Romance"
                }

                default_g = saved_genres if saved_genres else ["Action", "Thriller"]
                chosen_genres = []

                g_cols = st.columns(2)
                for i, g in enumerate(AVAILABLE_GENRES):
                    with g_cols[i % 2]:
                        if st.checkbox(genre_icons.get(g, g), value=(g in default_g), key=f"onb_g_{g}"):
                            chosen_genres.append(g)

                st.write("")
                if st.button("Next: Choose Languages ➡️", use_container_width=True):
                    if not chosen_genres:
                        st.warning("Select at least one genre!")
                    else:
                        st.session_state.temp_genres = chosen_genres
                        st.session_state.onboarding_step = 2
                        st.rerun()

            elif st.session_state.onboarding_step == 2:
                st.progress(100, text="Step 2 of 2: Choose Your Preferred Languages")
                st.markdown("### 🌐 Which languages do you prefer?")

                lang_icons = {
                    "Telugu": "🎬 Telugu (తెలుగు)",
                    "Hindi": "🎬 Hindi (हिन्दी)",
                    "Tamil": "🎬 Tamil (தமிழ்)",
                    "English": "🎬 English"
                }

                default_l = saved_langs if saved_langs else ["Telugu", "English"]
                chosen_langs = []

                l_cols = st.columns(2)
                for i, l in enumerate(AVAILABLE_LANGUAGES):
                    with l_cols[i % 2]:
                        if st.checkbox(lang_icons.get(l, l), value=(l in default_l), key=f"onb_l_{l}"):
                            chosen_langs.append(l)

                st.write("")
                nav_col1, nav_col2 = st.columns(2)
                with nav_col1:
                    if st.button("⬅️ Back", use_container_width=True):
                        st.session_state.onboarding_step = 1
                        st.rerun()
                with nav_col2:
                    if st.button("✨ Finish", use_container_width=True):
                        if not chosen_langs:
                            st.warning("Select at least one language!")
                        else:
                            save_user_preferences(current_user, st.session_state.temp_genres, chosen_langs)
                            st.session_state.selected_lang = chosen_langs[0]
                            st.session_state.editing_prefs = False
                            st.session_state.onboarding_step = 1
                            st.rerun()

    st.stop()

if "prefs_initialized" not in st.session_state:
    st.session_state.selected_lang = saved_langs[0]
    st.session_state.prefs_initialized = True

try:
    movies = load_movies()
except FileNotFoundError:
    st.error("`movies.csv` file not found!")
    st.stop()

# ---------------- SIDEBAR (PROFILE, WATCH PARTY, WATCHLIST, ADMIN & ADD MOVIE) ----------------

user_watchlist = get_watchlist(current_user)
cw_list = get_continue_watching(current_user)
rev_count = get_user_review_count(current_user)

with st.sidebar:
    st.markdown(f"### 👤 {current_user}")
    if current_user.lower() != "admin":
        st.caption(f"🎭 {', '.join(saved_genres)}")
        st.caption(f"🌐 {', '.join(saved_langs)}")

    s_col1, s_col2, s_col3 = st.columns(3)
    s_col1.metric("❤️ Saved", len(user_watchlist))
    s_col2.metric("⏯️ Active", len(cw_list))
    s_col3.metric("⭐ Rated", rev_count)

    # Admin Dashboard Toggle (Visible for 'admin' user)
    if current_user.lower() == "admin":
        btn_lbl = "🎬 Browse Movies" if st.session_state.show_admin_panel else "📊 Admin Analytics"
        if st.button(btn_lbl, use_container_width=True):
            st.session_state.show_admin_panel = not st.session_state.show_admin_panel
            st.rerun()
    else:
        if st.button("⚙️ Edit Preferences", use_container_width=True):
            st.session_state.editing_prefs = True
            st.session_state.onboarding_step = 1
            st.rerun()

    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.logged_in_user = None
        st.session_state.selected_movie = None
        st.session_state.is_playing = False
        st.session_state.resume_seconds = 0
        st.session_state.party_room = None
        st.session_state.show_admin_panel = False
        st.session_state.auth_page = "login"
        if "prefs_initialized" in st.session_state:
            del st.session_state.prefs_initialized
        st.rerun()

    st.divider()
    with st.expander("👥 Watch Party (Sync with Friends)", expanded=bool(st.session_state.party_room)):
        if st.session_state.party_room:
            p_info = get_party(st.session_state.party_room)
            st.success(f"🟢 Active Room: **{st.session_state.party_room}**")
            if p_info:
                st.caption(f"🎬 Watching: {p_info[2]} ({p_info[3]}) @ {p_info[4]}m")
                if st.button("🔄 Sync with Host", use_container_width=True):
                    st.session_state.selected_movie = p_info[2]
                    st.session_state.selected_lang = p_info[3]
                    st.session_state.resume_seconds = p_info[4] * 60
                    st.session_state.is_playing = True
                    st.session_state.show_admin_panel = False
                    st.rerun()
            if st.button("🚪 Leave Party", use_container_width=True):
                st.session_state.party_room = None
                st.rerun()
        else:
            if st.button("🎉 Create New Watch Party", use_container_width=True):
                code = f"FLIX-{random.randint(100, 999)}"
                host_movie = st.session_state.selected_movie or movies["title"].iloc[0]
                create_or_update_party(code, current_user, host_movie, st.session_state.selected_lang, 0)
                st.session_state.party_room = code
                st.session_state.selected_movie = host_movie
                st.session_state.is_playing = True
                st.session_state.show_admin_panel = False
                st.rerun()

            join_code = st.text_input("Enter Room Code", placeholder="e.g. FLIX-482")
            if st.button("🔗 Join Room", use_container_width=True):
                party = get_party(join_code.strip())
                if party:
                    st.session_state.party_room = party[0]
                    st.session_state.selected_movie = party[2]
                    st.session_state.selected_lang = party[3]
                    st.session_state.resume_seconds = party[4] * 60
                    st.session_state.is_playing = True
                    st.session_state.show_admin_panel = False
                    st.rerun()
                else:
                    st.error("Room code not found!")

    st.divider()
    st.markdown("### ❤️ My Watchlist")

    if user_watchlist:
        for item in user_watchlist:
            w_col1, w_col2 = st.columns([4, 1])
            with w_col1:
                if st.button(f"🎬 {item}", key=f"wl_view_{item}", use_container_width=True):
                    saved_m = get_movie_progress(current_user, item)
                    st.session_state.selected_movie = item
                    st.session_state.resume_seconds = saved_m * 60
                    st.session_state.is_playing = True
                    st.session_state.show_admin_panel = False
                    st.rerun()
            with w_col2:
                if st.button("❌", key=f"wl_del_{item}"):
                    remove_from_watchlist(current_user, item)
                    st.rerun()
    else:
        st.caption("Watchlist is empty.")

    st.divider()
    with st.expander("➕ Add New Movie"):
        with st.form("add_movie_form", clear_on_submit=True):
            m_name = st.text_input("Movie Title *")
            m_genre = st.text_input("Genres (e.g. Horror, Thriller) *")
            m_te = st.text_input("Telugu Video URL")
            m_hi = st.text_input("Hindi Video URL")
            m_ta = st.text_input("Tamil Video URL")
            m_en = st.text_input("English Video URL")
            if st.form_submit_button("Add Movie", use_container_width=True):
                if not m_name.strip() or not m_genre.strip():
                    st.warning("Title and Genre are required.")
                elif m_name.strip().lower() in movies["title"].str.lower().values:
                    st.error("Movie already exists!")
                else:
                    append_movie_to_csv(m_name, m_genre, m_te, m_hi, m_ta, m_en)
                    st.success(f"Added '{m_name}'!")
                    st.rerun()

# ---------------- ADMIN ANALYTICS VIEW (DEFAULT FOR ADMIN) ----------------

if st.session_state.show_admin_panel and current_user.lower() == "admin":
    st.markdown("## 📊 MOVIEZZ FLIX — Admin Analytics Dashboard")
    t_users, t_watches, t_revs, top_w, lang_s, users_list = get_admin_analytics()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("👥 Registered Users", t_users)
    m2.metric("🎬 Total Movies", len(movies))
    m3.metric("▶️ Active Streams", t_watches)
    m4.metric("⭐ Total Reviews", t_revs)

    st.divider()
    st.markdown("### 👥 Registered Users Directory")
    if users_list:
        df_users = pd.DataFrame(
            users_list,
            columns=["User ID", "Username", "Favourite Genres", "Preferred Languages", "Watchlist Movies", "Reviews Given"]
        )
        st.dataframe(df_users, use_container_width=True, hide_index=True)
    else:
        st.info("No registered users yet.")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 🔥 Most Watched Movies")
        if top_w:
            df_w = pd.DataFrame(top_w, columns=["Movie", "Views"]).set_index("Movie")
            st.bar_chart(df_w)
        else:
            st.info("No watch history data yet.")

    with c2:
        st.markdown("### 🌐 Preferred Audio Languages")
        if lang_s:
            df_l = pd.DataFrame(lang_s, columns=["Language", "Streams"]).set_index("Language")
            st.bar_chart(df_l)
        else:
            st.info("No language stream data yet.")

    st.stop()

# ---------------- HELPERS ----------------

def get_available_languages(movie_row: pd.Series) -> dict[str, str]:
    available = {}
    for lang_name, col_name in LANG_COLUMNS.items():
        url = str(movie_row.get(col_name, "")).strip()
        if url and url.lower() != "nan":
            available[lang_name] = url
    return available

def get_personalized_movies(df: pd.DataFrame, pref_genres: list[str], pref_langs: list[str]):
    def score_movie(row):
        m_genres = [g.strip().lower() for g in str(row["genre"]).split(",")]
        genre_hits = sum(1 for pg in pref_genres if pg.lower() in m_genres)
        avail_langs = get_available_languages(row)
        lang_hits = sum(1 for pl in pref_langs if pl in avail_langs)

        if genre_hits > 0 and lang_hits > 0:
            return (genre_hits * 2) + lang_hits
        return 0

    temp_df = df.copy()
    temp_df["match_score"] = temp_df.apply(score_movie, axis=1)
    recommended = temp_df[temp_df["match_score"] > 0].sort_values(by="match_score", ascending=False)
    others = temp_df[temp_df["match_score"] == 0]
    return recommended, others

def get_similar_movies(selected_title: str, df: pd.DataFrame, limit: int = 6) -> pd.DataFrame:
    row = df[df["title"] == selected_title]
    if row.empty:
        return pd.DataFrame()

    genres = [g.strip().lower() for g in row["genre"].iloc[0].split(",") if g.strip()]
    if not genres:
        return pd.DataFrame()

    def genre_score(movie_genres: str) -> int:
        m_list = [g.strip().lower() for g in movie_genres.split(",")]
        return len(set(genres) & set(m_list))

    candidates = df[df["title"] != selected_title].copy()
    candidates["score"] = candidates["genre"].apply(genre_score)
    return candidates[candidates["score"] > 0].sort_values(by="score", ascending=False).head(limit)

recommended_df, remaining_df = get_personalized_movies(movies, saved_genres, saved_langs)

# ---------------- MAIN UI: NAVBAR, HERO & MOOD BAR ----------------

st.markdown(f"""
<div class="navbar">
    <div class="logo">MOVIEZZ FLIX</div>
    <div>FOR {current_user.upper()} &nbsp;&nbsp; • &nbsp;&nbsp; {', '.join(saved_langs).upper()}</div>
</div>

<div class="hero">
    <h1>Picked For You, {current_user}.</h1>
    <p style="font-size:18px; color:#ddd !important;">
        Showing your favourite <b>{', '.join(saved_genres)}</b> picks in <b>{', '.join(saved_langs)}</b>.
    </p>
</div>
""", unsafe_allow_html=True)

st.write("")
mood_cols = st.columns([1.3, 1, 1, 1, 1, 1])
with mood_cols[0]:
    if st.button("🎲 Surprise Me!", use_container_width=True):
        pool = recommended_df if not recommended_df.empty else movies
        pick_row = pool.sample(1).iloc[0]
        pick_title = pick_row["title"]
        avail = list(get_available_languages(pick_row).keys())
        matching = [l for l in saved_langs if l in avail]
        st.session_state.selected_movie = pick_title
        st.session_state.selected_lang = matching[0] if matching else saved_langs[0]
        st.session_state.resume_seconds = get_movie_progress(current_user, pick_title) * 60
        st.session_state.is_playing = True
        st.rerun()

for idx, mood_name in enumerate(MOOD_FILTERS.keys()):
    with mood_cols[idx + 1]:
        if st.button(mood_name, key=f"mood_btn_{mood_name}", use_container_width=True):
            st.session_state.active_mood = mood_name
            st.rerun()

# ---------------- ACTIVE MOVIE PLAYER (WITH RESUME, WATCH PARTY CHAT & SHARE) ----------------

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
            st.session_state.resume_seconds = 0
            st.rerun()

    movie_df_match = movies[movies["title"] == active_movie]
    saved_movie_langs = get_available_languages(movie_df_match.iloc[0]) if not movie_df_match.empty else {}

    default_idx = AVAILABLE_LANGUAGES.index(st.session_state.selected_lang) if st.session_state.selected_lang in AVAILABLE_LANGUAGES else 0
    chosen_lang = st.radio(
        "🌐 Choose Language:",
        options=AVAILABLE_LANGUAGES,
        index=default_idx,
        horizontal=True,
        key=f"lang_selector_{active_movie}"
    )
    st.session_state.selected_lang = chosen_lang

    video_url = saved_movie_langs.get(chosen_lang) or (next(iter(saved_movie_langs.values())) if saved_movie_langs else "")
    yt_full_search = f"https://www.youtube.com/results?search_query={quote_plus(f'{active_movie} {chosen_lang} full movie')}"

    current_saved_mins = get_movie_progress(current_user, active_movie)

    if st.session_state.is_playing:
        upsert_continue_watching(current_user, active_movie, chosen_lang, current_saved_mins)

        if st.session_state.party_room:
            create_or_update_party(st.session_state.party_room, current_user, active_movie, chosen_lang, current_saved_mins)

        if st.session_state.party_room:
            vid_col, chat_col = st.columns([2.4, 1])
        else:
            vid_col = st.container()
            chat_col = None

        with vid_col:
            if video_url:
                start_sec = st.session_state.resume_seconds if st.session_state.resume_seconds > 0 else (current_saved_mins * 60)
                if start_sec > 0:
                    st.caption(f"⏩ Resuming **{active_movie}** in **{chosen_lang}** from **{start_sec // 60} mins**")
                else:
                    st.caption(f"🔊 Playing in **{chosen_lang}**")
                st.video(video_url, start_time=start_sec, autoplay=True)
            else:
                st.info(f"Direct video not in catalog for {active_movie} ({chosen_lang}).")
                st.link_button(f"🔎 Watch {active_movie} ({chosen_lang}) on YouTube", yt_full_search)

        if chat_col:
            with chat_col:
                with st.container(border=True):
                    st.markdown(f"### 💬 Party Chat (`{st.session_state.party_room}`)")
                    msgs = get_party_messages(st.session_state.party_room)
                    if msgs:
                        for m_user, m_txt in reversed(msgs):
                            st.markdown(f"**@{m_user}:** {m_txt}")
                    else:
                        st.caption("Say hi to your friends!")

                    with st.form(f"party_chat_{st.session_state.party_room}", clear_on_submit=True):
                        chat_in = st.text_input("Message", placeholder="Mass entry scene 🔥", label_visibility="collapsed")
                        c_b1, c_b2 = st.columns(2)
                        with c_b1:
                            if st.form_submit_button("Send", use_container_width=True) and chat_in.strip():
                                add_party_message(st.session_state.party_room, current_user, chat_in)
                                st.rerun()
                        with c_b2:
                            if st.form_submit_button("Refresh", use_container_width=True):
                                st.rerun()

        with st.container(border=True):
            p_col1, p_col2, p_col3 = st.columns([2.5, 1, 1])
            with p_col1:
                stopped_at_mins = st.slider(
                    "⏱️ Where did you pause? (Save minute to resume later)",
                    min_value=0,
                    max_value=180,
                    value=current_saved_mins,
                    step=1,
                    key=f"prog_slider_{active_movie}"
                )
            with p_col2:
                st.write("")
                if st.button("💾 Save Progress", use_container_width=True):
                    update_watch_minutes(current_user, active_movie, chosen_lang, stopped_at_mins)
                    if st.session_state.party_room:
                        create_or_update_party(st.session_state.party_room, current_user, active_movie, chosen_lang, stopped_at_mins)
                    st.session_state.resume_seconds = stopped_at_mins * 60
                    st.toast(f"Saved at {stopped_at_mins} mins!")
                    st.rerun()
            with p_col3:
                st.write("")
                if st.button("✅ Mark Finished", use_container_width=True):
                    remove_continue_watching(current_user, active_movie)
                    st.session_state.resume_seconds = 0
                    st.toast(f"Removed '{active_movie}' from Continue Watching!")
                    st.rerun()

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
            play_label = f"⏩ Resume ({current_saved_mins}m)" if current_saved_mins > 0 else f"▶ Play ({chosen_lang})"
            if st.button(play_label, key="play_main_btn", use_container_width=True):
                st.session_state.resume_seconds = current_saved_mins * 60
                st.session_state.is_playing = True
                st.rerun()

        with btn_col2:
            in_watchlist = active_movie in user_watchlist
            if in_watchlist:
                if st.button("💔 Remove Watchlist", use_container_width=True):
                    remove_from_watchlist(current_user, active_movie)
                    st.rerun()
            else:
                if st.button("❤️ + Watchlist", use_container_width=True):
                    add_to_watchlist(current_user, active_movie)
                    st.toast(f"Added '{active_movie}'!")
                    st.rerun()

        with btn_col3:
            st.link_button("📺 Full Movie on YouTube", yt_full_search, use_container_width=True)

        avg_rating, rating_count = get_average_rating(active_movie)
        user_rating_text = f"⭐ {avg_rating}/5 ({rating_count} reviews)" if avg_rating else "No user ratings yet"
        st.write(f"**👥 MOVIEZZ FLIX Rating:** {user_rating_text}")

        if details:
            st.write(f"**📅 Year:** {details.get('Year', 'N/A')}  |  **⭐ IMDb:** {details.get('imdbRating', 'N/A')}  |  **⏱️ Runtime:** {details.get('Runtime', 'N/A')}")
            st.write(f"**🎭 Genre:** {details.get('Genre', 'N/A')}")
            st.write(f"**🎬 Director:** {details.get('Director', 'N/A')}")
            st.write(f"**🌟 Cast:** {details.get('Actors', 'N/A')}")
            st.write("**📝 Plot:**")
            st.write(details.get("Plot", "N/A"))
        elif not movie_df_match.empty:
            st.write(f"**🎭 Genre:** {movie_df_match['genre'].iloc[0]}")

        with st.expander("📤 Share This Movie with Friends"):
            share_link = video_url if video_url else yt_full_search
            st.code(f"🍿 Watch '{active_movie}' ({chosen_lang}) on MOVIEZZ FLIX! 👉 {share_link}", language=None)

    # Ratings & Reviews
    st.markdown("### ⭐ Rate & Review")
    rev_col1, rev_col2 = st.columns([1, 1.5])

    with rev_col1:
        with st.form(f"review_form_{active_movie}"):
            star_rating = st.slider("Your Rating", 1, 5, 5)
            review_text = st.text_area("Review (optional)", placeholder="Must watch...")
            if st.form_submit_button("Submit Review", use_container_width=True):
                save_review(current_user, active_movie, star_rating, review_text)
                st.success("Review saved!")
                st.rerun()

    with rev_col2:
        all_reviews = get_movie_reviews(active_movie)
        if all_reviews:
            for r_user, r_stars, r_comment in all_reviews[:4]:
                with st.container(border=True):
                    st.markdown(f"**@{r_user}** — {'⭐' * r_stars} ({r_stars}/5)")
                    if r_comment:
                        st.write(r_comment)
        else:
            st.info("No reviews yet.")

    # Similar Movies
    st.markdown("### 🍿 More Like This")
    recs = get_similar_movies(active_movie, movies, limit=6)
    if not recs.empty:
        rec_cols = st.columns(3)
        for i, (_, row) in enumerate(recs.iterrows()):
            rec_title = row["title"]
            rec_data = get_movie_details(rec_title)
            with rec_cols[i % 3]:
                with st.container(border=True):
                    if rec_data and rec_data.get("Poster") != "N/A":
                        st.image(rec_data["Poster"], use_container_width=True)
                    st.markdown(f"#### {rec_title}")
                    st.caption(f"🎭 {row['genre']}")
                    if st.button("▶ Open & Play", key=f"rec_btn_{rec_title}", use_container_width=True):
                        st.session_state.selected_movie = rec_title
                        st.session_state.resume_seconds = get_movie_progress(current_user, rec_title) * 60
                        st.session_state.is_playing = True
                        st.rerun()

# ---------------- MOOD FILTER RESULTS (IF SELECTED) ----------------

if st.session_state.active_mood != "All Moods":
    st.divider()
    m_hdr1, m_hdr2 = st.columns([5, 1])
    with m_hdr1:
        st.markdown(f"## {st.session_state.active_mood} Picks")
    with m_hdr2:
        if st.button("Clear Mood", use_container_width=True):
            st.session_state.active_mood = "All Moods"
            st.rerun()

    target_genres = MOOD_FILTERS[st.session_state.active_mood]
    pattern = "|".join(target_genres)
    mood_df = movies[movies["genre"].str.contains(pattern, case=False, na=False)]

    m_cols = st.columns(4)
    for idx, (_, row) in enumerate(mood_df.iterrows()):
        m_title = row["title"]
        m_data = get_movie_details(m_title)
        with m_cols[idx % 4]:
            with st.container(border=True):
                if m_data and m_data.get("Poster") != "N/A":
                    st.image(m_data["Poster"], use_container_width=True)
                st.markdown(f"**{m_title}**")
                st.caption(f"🎭 {row['genre']}")
                if st.button("▶ Watch Now", key=f"mood_card_{m_title}_{idx}", use_container_width=True):
                    st.session_state.selected_movie = m_title
                    st.session_state.selected_lang = saved_langs[0]
                    st.session_state.resume_seconds = get_movie_progress(current_user, m_title) * 60
                    st.session_state.is_playing = True
                    st.rerun()

# ---------------- SECTION 0: CONTINUE WATCHING ----------------

if cw_list:
    st.divider()
    st.markdown(f"## ⏯️ Continue Watching for {current_user}")

    cw_cols = st.columns(4)
    for idx, (cw_title, cw_lang, cw_mins) in enumerate(cw_list[:4]):
        cw_data = get_movie_details(cw_title)
        with cw_cols[idx % 4]:
            with st.container(border=True):
                if cw_data and cw_data.get("Poster") != "N/A":
                    st.image(cw_data["Poster"], use_container_width=True)
                st.markdown(f"**{cw_title}**")
                if cw_mins > 0:
                    st.caption(f"🌐 {cw_lang} • Paused at {cw_mins} mins")
                    st.progress(min(cw_mins / 150.0, 1.0))
                else:
                    st.caption(f"🌐 {cw_lang} • Started watching")
                    st.progress(0.1)

                b1, b2 = st.columns([3, 1])
                with b1:
                    btn_txt = f"⏩ Resume ({cw_mins}m)" if cw_mins > 0 else f"▶ Resume ({cw_lang})"
                    if st.button(btn_txt, key=f"cw_resume_{cw_title}", use_container_width=True):
                        st.session_state.selected_movie = cw_title
                        st.session_state.selected_lang = cw_lang
                        st.session_state.resume_seconds = cw_mins * 60
                        st.session_state.is_playing = True
                        st.rerun()
                with b2:
                    if st.button("✖", key=f"cw_rem_{cw_title}", help="Remove from Continue Watching"):
                        remove_continue_watching(current_user, cw_title)
                        st.rerun()

# ---------------- SECTION 1: PERSONALIZED RECOMMENDATIONS ----------------

st.divider()
st.markdown(f"## 🎯 Recommended For You ({', '.join(saved_genres)} • {', '.join(saved_langs)})")

def render_movie_grid(df_to_render: pd.DataFrame, section_prefix: str, default_play_lang: str):
    if df_to_render.empty:
        st.info("No movies found in this section.")
        return

    grid_cols = st.columns(4)
    for idx, (_, row) in enumerate(df_to_render.iterrows()):
        m_title = row["title"]
        m_data = get_movie_details(m_title)
        m_langs = list(get_available_languages(row).keys())
        avg_r, _ = get_average_rating(m_title)

        matching_langs = [l for l in saved_langs if l in m_langs]
        play_lang = matching_langs[0] if matching_langs else default_play_lang

        with grid_cols[idx % 4]:
            with st.container(border=True):
                if m_data and m_data.get("Poster") != "N/A":
                    st.image(m_data["Poster"], use_container_width=True)
                st.markdown(f"**{m_title}**")
                st.caption(f"🎭 {row['genre']}")
                if m_langs:
                    st.caption(f"🌐 {' • '.join(m_langs)}")
                if avg_r:
                    st.caption(f"⭐ {avg_r}/5")
                if st.button(f"▶ Watch ({play_lang})", key=f"{section_prefix}_{m_title}_{idx}", use_container_width=True):
                    st.session_state.selected_movie = m_title
                    st.session_state.selected_lang = play_lang
                    st.session_state.resume_seconds = get_movie_progress(current_user, m_title) * 60
                    st.session_state.is_playing = True
                    st.rerun()

if not recommended_df.empty:
    render_movie_grid(recommended_df, "rec_feed", saved_langs[0])
else:
    st.info("No exact matches yet. Edit preferences in sidebar or explore below!")

# ---------------- SECTION 2: TOP RATED BY COMMUNITY ----------------

top_rated_rows = get_top_rated_movies(limit=4)
if top_rated_rows:
    st.divider()
    st.markdown("## 🏆 Top Rated on MOVIEZZ FLIX")
    tr_cols = st.columns(4)
    for idx, (tr_title, tr_avg, tr_cnt) in enumerate(top_rated_rows):
        tr_data = get_movie_details(tr_title)
        with tr_cols[idx % 4]:
            with st.container(border=True):
                if tr_data and tr_data.get("Poster") != "N/A":
                    st.image(tr_data["Poster"], use_container_width=True)
                st.markdown(f"**#{idx + 1} {tr_title}**")
                st.caption(f"⭐ {tr_avg}/5 ({tr_cnt} user ratings)")
                if st.button("▶ Watch Top Pick", key=f"tr_btn_{tr_title}", use_container_width=True):
                    st.session_state.selected_movie = tr_title
                    st.session_state.selected_lang = saved_langs[0]
                    st.session_state.resume_seconds = get_movie_progress(current_user, tr_title) * 60
                    st.session_state.is_playing = True
                    st.rerun()

# ---------------- SECTION 3: SMART VIBE SEARCH & EXPLORE MORE ----------------

st.divider()
st.markdown("## 🎬 Smart Search & Explore More Movies")

search_col, vibe_col, genre_col = st.columns([1.5, 1.5, 1])

with search_col:
    all_titles = sorted(movies["title"].unique().tolist())
    quick_pick = st.selectbox(
        "Quick Title Open",
        options=[""] + all_titles,
        format_func=lambda x: "🎯 Jump directly to a movie..." if x == "" else x,
        label_visibility="collapsed"
    )
    if quick_pick:
        if st.button(f"▶ Open '{quick_pick}'", use_container_width=True):
            st.session_state.selected_movie = quick_pick
            st.session_state.selected_lang = saved_langs[0]
            st.session_state.resume_seconds = get_movie_progress(current_user, quick_pick) * 60
            st.session_state.is_playing = True
            st.rerun()

with vibe_col:
    vibe_query = st.text_input(
        "Smart Vibe Search",
        placeholder="🔍 Type mood or genre (e.g. horror thriller, sci-fi)...",
        label_visibility="collapsed"
    )

with genre_col:
    extra_genre = st.selectbox(
        "Filter Genre",
        options=["All Other Movies"] + AVAILABLE_GENRES,
        label_visibility="collapsed"
    )

display_others = remaining_df if not remaining_df.empty else movies
if extra_genre != "All Other Movies":
    display_others = movies[movies["genre"].str.contains(extra_genre, case=False, na=False)]

if vibe_query.strip():
    words = [w.strip() for w in vibe_query.lower().split() if w.strip()]
    mask = movies.apply(
        lambda r: any(w in r["title"].lower() or w in r["genre"].lower() for w in words),
        axis=1
    )
    display_others = movies[mask]

render_movie_grid(display_others, "other_feed", saved_langs[0])

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
