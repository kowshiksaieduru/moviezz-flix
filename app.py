import os
import csv
import random
import hashlib
import sqlite3
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus

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

AVAILABLE_CATEGORIES = ["Movie", "Web Series", "Serial", "Jabardasth"]
AVAILABLE_LANGUAGES = ["Telugu", "Hindi", "Tamil", "English"]

st.set_page_config(
    page_title="MOVIEZZ FLIX",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed"
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

def delete_user_account(username: str):
    if username.lower() == "admin":
        return
    with get_db_connection() as conn:
        conn.execute("DELETE FROM users WHERE username = ? COLLATE NOCASE", (username,))
        conn.execute("DELETE FROM user_preferences WHERE username = ? COLLATE NOCASE", (username,))
        conn.execute("DELETE FROM continue_watching WHERE username = ? COLLATE NOCASE", (username,))
        conn.execute("DELETE FROM watchlist WHERE username = ? COLLATE NOCASE", (username,))
        conn.execute("DELETE FROM reviews WHERE username = ? COLLATE NOCASE", (username,))

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

def get_user_review_count(username: str) -> int:
    with get_db_connection() as conn:
        res = conn.execute("SELECT COUNT(*) FROM reviews WHERE username = ?", (username,)).fetchone()
    return res[0] if res else 0

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

for key, default in [
    ("logged_in_user", None),
    ("selected_movie", None),
    ("is_playing", False),
    ("selected_lang", "Telugu"),
    ("resume_seconds", 0),
    ("onboarding_step", 1),
    ("temp_genres", []),
    ("editing_prefs", False),
    ("auth_page", "login"),
    ("active_tab", "All"),
    ("party_room", None),
    ("show_admin_panel", False),
    ("confirm_delete_account", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------- JIOHOTSTAR DEEP NAVY & ROYAL BLUE OTT THEME ----------------

st.markdown("""
<style>
/* Deep JioHotstar Midnight Navy Background */
.stApp {
    background: radial-gradient(circle at 50% 0%, #141c33 0%, #0b0e17 55%, #07090f 100%);
    color: #ffffff;
}
header[data-testid="stHeader"] {
    background: rgba(11, 14, 23, 0.75);
    backdrop-filter: blur(10px);
}
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 3rem !important;
    max-width: 1440px;
}
h1, h2, h3, h4, p, label, span {
    color: #ffffff !important;
}

/* JioHotstar Top Bar */
.hotstar-nav {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 20px;
    background: linear-gradient(90deg, rgba(16, 22, 40, 0.95), rgba(20, 30, 58, 0.85));
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    margin-bottom: 14px;
}
.hotstar-brand {
    font-size: 26px;
    font-weight: 900;
    letter-spacing: 2px;
    background: linear-gradient(90deg, #ffffff 0%, #3b82f6 60%, #e50914 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.hotstar-badge {
    background: rgba(59, 130, 246, 0.18);
    border: 1px solid rgba(59, 130, 246, 0.45);
    color: #93c5fd !important;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 700;
}

/* JioHotstar Spotlight Banner */
.spotlight-card {
    position: relative;
    border-radius: 18px;
    overflow: hidden;
    background: linear-gradient(90deg, #0b0e17 0%, rgba(11, 14, 23, 0.88) 48%, rgba(11, 14, 23, 0.25) 100%),
                url("https://images.unsplash.com/photo-1536440136628-849c177e76a1?auto=format&fit=crop&w=1600&q=80");
    background-size: cover;
    background-position: center;
    border: 1px solid rgba(255, 255, 255, 0.12);
    padding: 36px 32px;
    margin-bottom: 16px;
    box-shadow: 0 18px 40px rgba(0, 0, 0, 0.65);
}
.spotlight-tag {
    display: inline-block;
    background: linear-gradient(90deg, #2563eb, #1d4ed8);
    color: #fff !important;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.5px;
    padding: 4px 12px;
    border-radius: 6px;
    margin-bottom: 10px;
}
.spotlight-title {
    font-size: 36px;
    font-weight: 900;
    margin: 0 0 8px 0;
    line-height: 1.15;
}
.spotlight-meta {
    color: #cbd5e1 !important;
    font-size: 14px;
    margin-bottom: 18px;
}

/* TRUE JIOHOTSTAR HORIZONTAL TOUCH-SWIPE POSTER RAIL */
.rail-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: 18px 0 10px 2px;
}
.rail-title {
    font-size: 20px;
    font-weight: 800;
    color: #f8fafc !important;
    letter-spacing: 0.3px;
}
.rail-hint {
    font-size: 12px;
    color: #94a3b8 !important;
}
.poster-rail {
    display: flex;
    flex-direction: row;
    flex-wrap: nowrap;
    overflow-x: auto;
    overflow-y: hidden;
    gap: 14px;
    padding: 6px 4px 16px 4px;
    scroll-snap-type: x mandatory;
    -webkit-overflow-scrolling: touch;
}
.poster-rail::-webkit-scrollbar {
    height: 6px;
}
.poster-rail::-webkit-scrollbar-track {
    background: rgba(255, 255, 255, 0.05);
    border-radius: 10px;
}
.poster-rail::-webkit-scrollbar-thumb {
    background: rgba(59, 130, 246, 0.7);
    border-radius: 10px;
}
.poster-tile {
    flex: 0 0 155px;
    width: 155px;
    scroll-snap-align: start;
    text-decoration: none !important;
    position: relative;
    border-radius: 12px;
    overflow: hidden;
    background: #13192b;
    border: 1px solid rgba(255, 255, 255, 0.1);
    transition: transform 0.22s ease, box-shadow 0.22s ease, border-color 0.22s ease;
    display: block;
}
@media (min-width: 768px) {
    .poster-tile {
        flex: 0 0 190px;
        width: 190px;
    }
}
.poster-tile:hover {
    transform: scale(1.05);
    border-color: #3b82f6;
    box-shadow: 0 10px 26px rgba(37, 99, 235, 0.45);
    z-index: 5;
}
.poster-img-wrap {
    position: relative;
    width: 100%;
    height: 225px;
    overflow: hidden;
}
@media (min-width: 768px) {
    .poster-img-wrap {
        height: 270px;
    }
}
.poster-img-wrap img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
.poster-gradient {
    position: absolute;
    inset: 0;
    background: linear-gradient(to top, rgba(11, 14, 23, 0.98) 0%, rgba(11, 14, 23, 0.35) 45%, transparent 100%);
}
.lang-pill {
    position: absolute;
    top: 8px;
    left: 8px;
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(6px);
    color: #93c5fd !important;
    font-size: 10px;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 6px;
    border: 1px solid rgba(59, 130, 246, 0.4);
}
.play-badge {
    position: absolute;
    bottom: 10px;
    right: 10px;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    background: linear-gradient(135deg, #2563eb, #1d4ed8);
    color: white !important;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.6);
}
.poster-info {
    padding: 10px 10px 12px 10px;
}
.poster-name {
    font-size: 14px;
    font-weight: 800;
    color: #ffffff !important;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 3px;
}
.poster-sub {
    font-size: 11px;
    color: #94a3b8 !important;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.progress-bar-bg {
    width: 100%;
    height: 4px;
    background: rgba(255, 255, 255, 0.15);
    margin-top: 6px;
    border-radius: 4px;
    overflow: hidden;
}
.progress-bar-fill {
    height: 100%;
    background: #3b82f6;
}

/* Buttons */
.stButton > button {
    background: linear-gradient(135deg, #2563eb, #1d4ed8);
    color: white;
    border: none;
    border-radius: 10px;
    font-weight: 700;
    letter-spacing: 0.3px;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35);
}
.stButton > button:hover {
    background: linear-gradient(135deg, #3b82f6, #2563eb);
    box-shadow: 0 6px 20px rgba(59, 130, 246, 0.55);
}
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: rgba(255, 255, 255, 0.1) !important;
    background: rgba(17, 23, 40, 0.85) !important;
    backdrop-filter: blur(12px);
    border-radius: 14px;
}
</style>
""", unsafe_allow_html=True)

# ---------------- DATA & API HELPERS ----------------

FALLBACK_BANNERS = {
    "Serial": "https://images.unsplash.com/photo-1522869635100-9f4c5e86aa37?auto=format&fit=crop&w=600&q=80",
    "Jabardasth": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?auto=format&fit=crop&w=600&q=80",
    "Web Series": "https://images.unsplash.com/photo-1593784991095-a205069470b6?auto=format&fit=crop&w=600&q=80",
    "Movie": "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?auto=format&fit=crop&w=600&q=80"
}

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
    df = pd.read_csv(CSV_PATH, on_bad_lines="skip")
    df["title"] = df["title"].astype(str).str.strip()
    if "category" not in df.columns:
        df["category"] = "Movie"
    else:
        df["category"] = df["category"].fillna("Movie").astype(str).str.strip()
    df["genre"] = df["genre"].fillna("").astype(str)
    for col in LANG_COLUMNS.values():
        if col not in df.columns:
            df[col] = ""
        else:
            df[col] = df[col].fillna("").astype(str).str.strip()
    return df

def append_movie_to_csv(title: str, category: str, genre: str, te_url: str, hi_url: str, ta_url: str, en_url: str):
    file_exists = CSV_PATH.exists()
    with open(CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["title", "category", "genre", "telugu_url", "hindi_url", "tamil_url", "english_url"])
        writer.writerow([title.strip(), category.strip(), genre.strip(), te_url.strip(), hi_url.strip(), ta_url.strip(), en_url.strip()])
    load_movies.clear()

# ---------------- LOGIN / SIGNUP / FORGOT PASSWORD ----------------

if st.session_state.logged_in_user is None:
    st.markdown("""
        <div style="text-align:center; margin-top:25px; margin-bottom:15px;">
            <span class="hotstar-badge">JIOHOTSTAR STYLE STREAMING</span>
            <h1 style="font-size:48px; font-weight:900; letter-spacing:3px; margin:12px 0 4px 0;">
                MOVIEZZ FLIX
            </h1>
            <p style="color:#94a3b8 !important; font-size:16px;">
                Blockbuster Movies • Web Series • Daily Serials • Jabardasth
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
                            st.success("✅ Password updated successfully!")
                        else:
                            st.error("Username not found.")

                if st.button("⬅️ Back to Sign In", use_container_width=True):
                    st.session_state.auth_page = "login"
                    st.rerun()

    st.stop()

current_user = st.session_state.logged_in_user
saved_genres, saved_langs = get_user_preferences(current_user)

if current_user.lower() == "admin" and (saved_genres is None or saved_langs is None):
    save_user_preferences(current_user, AVAILABLE_GENRES, AVAILABLE_LANGUAGES)
    saved_genres, saved_langs = AVAILABLE_GENRES, AVAILABLE_LANGUAGES
    st.session_state.show_admin_panel = True

# ---------------- 2-STEP PREFERENCE ONBOARDING WIZARD ----------------

if saved_genres is None or saved_langs is None or st.session_state.editing_prefs:
    st.markdown("<h1 style='text-align:center;'>MOVIEZZ FLIX</h1>", unsafe_allow_html=True)
    _, center_box, _ = st.columns([1, 2, 1])

    with center_box:
        with st.container(border=True):
            if st.session_state.onboarding_step == 1:
                st.progress(50, text="Step 1 of 2: Choose Your Favourite Genres")
                st.markdown(f"### 👋 Welcome, {current_user}! What do you love watching?")
                default_g = saved_genres if saved_genres else ["Action", "Comedy", "Drama"]
                chosen_genres = []
                g_cols = st.columns(2)
                for i, g in enumerate(AVAILABLE_GENRES):
                    with g_cols[i % 2]:
                        if st.checkbox(g, value=(g in default_g), key=f"onb_g_{g}"):
                            chosen_genres.append(g)

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
                default_l = saved_langs if saved_langs else ["Telugu", "English"]
                chosen_langs = []
                l_cols = st.columns(2)
                for i, l in enumerate(AVAILABLE_LANGUAGES):
                    with l_cols[i % 2]:
                        if st.checkbox(l, value=(l in default_l), key=f"onb_l_{l}"):
                            chosen_langs.append(l)

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

# ---------------- HANDLE POSTER TAP VIA URL QUERY PARAMS ----------------

if "watch" in st.query_params:
    tapped_title = unquote_plus(st.query_params["watch"])
    tapped_lang = st.query_params.get("lang", saved_langs[0])
    del st.query_params["watch"]
    if "lang" in st.query_params:
        del st.query_params["lang"]

    if tapped_title in movies["title"].values:
        st.session_state.selected_movie = tapped_title
        st.session_state.selected_lang = tapped_lang
        st.session_state.resume_seconds = get_movie_progress(current_user, tapped_title) * 60
        st.session_state.is_playing = True
        st.session_state.show_admin_panel = False
        st.rerun()

# ---------------- SIDEBAR ----------------

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

    if current_user.lower() == "admin":
        btn_lbl = "🎬 Browse Content" if st.session_state.show_admin_panel else "📊 Admin Analytics"
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
        st.session_state.confirm_delete_account = False
        st.session_state.auth_page = "login"
        if "prefs_initialized" in st.session_state:
            del st.session_state.prefs_initialized
        st.rerun()

    if current_user.lower() != "admin":
        if st.button("🗑️ Delete Account", use_container_width=True):
            st.session_state.confirm_delete_account = not st.session_state.confirm_delete_account
            st.rerun()

        if st.session_state.confirm_delete_account:
            st.warning("Are you sure? Account & history will be permanently deleted.")
            del_c1, del_c2 = st.columns(2)
            with del_c1:
                if st.button("✅ Yes, Delete", key="confirm_del_yes", use_container_width=True):
                    delete_user_account(current_user)
                    st.session_state.logged_in_user = None
                    st.session_state.selected_movie = None
                    st.session_state.is_playing = False
                    st.session_state.resume_seconds = 0
                    st.session_state.party_room = None
                    st.session_state.confirm_delete_account = False
                    st.session_state.auth_page = "login"
                    if "prefs_initialized" in st.session_state:
                        del st.session_state.prefs_initialized
                    st.rerun()
            with del_c2:
                if st.button("❌ Cancel", key="confirm_del_no", use_container_width=True):
                    st.session_state.confirm_delete_account = False
                    st.rerun()

    st.divider()
    with st.expander("👥 Watch Party", expanded=bool(st.session_state.party_room)):
        if st.session_state.party_room:
            p_info = get_party(st.session_state.party_room)
            st.success(f"🟢 Room: **{st.session_state.party_room}**")
            if p_info:
                st.caption(f"🎬 {p_info[2]} ({p_info[3]}) @ {p_info[4]}m")
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
            if st.button("🎉 Create Watch Party", use_container_width=True):
                code = f"FLIX-{random.randint(100, 999)}"
                host_movie = st.session_state.selected_movie or movies["title"].iloc[0]
                create_or_update_party(code, current_user, host_movie, st.session_state.selected_lang, 0)
                st.session_state.party_room = code
                st.session_state.selected_movie = host_movie
                st.session_state.is_playing = True
                st.session_state.show_admin_panel = False
                st.rerun()

            join_code = st.text_input("Room Code", placeholder="e.g. FLIX-482")
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
                    st.error("Room not found!")

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
    with st.expander("➕ Add Movie / Series / Serial"):
        with st.form("add_movie_form", clear_on_submit=True):
            m_name = st.text_input("Title *")
            m_cat = st.selectbox("Category *", options=AVAILABLE_CATEGORIES)
            m_genre = st.text_input("Genres (e.g. Drama, Comedy) *")
            m_te = st.text_input("Telugu Video / YouTube URL")
            m_hi = st.text_input("Hindi Video / YouTube URL")
            m_ta = st.text_input("Tamil Video / YouTube URL")
            m_en = st.text_input("English Video / YouTube URL")
            if st.form_submit_button("Add & Open 🚀", use_container_width=True):
                clean_title = m_name.strip()
                clean_genre = m_genre.strip()
                if not clean_title or not clean_genre:
                    st.warning("Title and Genre are required.")
                elif clean_title.lower() in movies["title"].str.lower().values:
                    st.error("Title already exists!")
                else:
                    append_movie_to_csv(clean_title, m_cat, clean_genre, m_te, m_hi, m_ta, m_en)
                    st.session_state.selected_movie = clean_title
                    if m_te.strip():
                        st.session_state.selected_lang = "Telugu"
                    elif m_hi.strip():
                        st.session_state.selected_lang = "Hindi"
                    elif m_ta.strip():
                        st.session_state.selected_lang = "Tamil"
                    elif m_en.strip():
                        st.session_state.selected_lang = "English"
                    st.session_state.resume_seconds = 0
                    st.session_state.is_playing = True
                    st.session_state.show_admin_panel = False
                    st.rerun()

# ---------------- ADMIN ANALYTICS VIEW ----------------

if st.session_state.show_admin_panel and current_user.lower() == "admin":
    st.markdown("## 📊 MOVIEZZ FLIX — Admin Analytics Dashboard")
    t_users, t_watches, t_revs, top_w, lang_s, users_list = get_admin_analytics()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("👥 Registered Users", t_users)
    m2.metric("🎬 Total Catalog", len(movies))
    m3.metric("▶️ Active Streams", t_watches)
    m4.metric("⭐ Total Reviews", t_revs)

    st.divider()
    st.markdown("### 👥 Registered Users Directory")
    if users_list:
        df_users = pd.DataFrame(
            users_list,
            columns=["User ID", "Username", "Favourite Genres", "Preferred Languages", "Watchlist Items", "Reviews Given"]
        )
        st.dataframe(df_users, use_container_width=True, hide_index=True)
    else:
        st.info("No registered users yet.")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 🔥 Most Watched Titles")
        if top_w:
            df_w = pd.DataFrame(top_w, columns=["Title", "Views"]).set_index("Title")
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

def get_poster_url(title: str, category: str) -> str:
    details = get_movie_details(title)
    if details and details.get("Poster") and details.get("Poster") != "N/A":
        return details["Poster"]
    return FALLBACK_BANNERS.get(category, FALLBACK_BANNERS["Movie"])

def sort_by_user_prefs(df: pd.DataFrame, pref_genres: list[str], pref_langs: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    def score_row(row):
        m_genres = [g.strip().lower() for g in str(row["genre"]).split(",")]
        genre_hits = sum(1 for pg in pref_genres if pg.lower() in m_genres)
        avail_langs = get_available_languages(row)
        lang_hits = sum(1 for pl in pref_langs if pl in avail_langs)
        return (genre_hits * 2) + lang_hits

    temp = df.copy()
    temp["pref_score"] = temp.apply(score_row, axis=1)
    return temp.sort_values(by="pref_score", ascending=False)

def render_hotstar_rail(title_text: str, df_to_render: pd.DataFrame, default_play_lang: str, progress_map: dict = None):
    """Renders a true JioHotstar-style horizontal touch-swipe poster rail."""
    if df_to_render.empty:
        return

    cards_html = []
    for _, row in df_to_render.iterrows():
        m_title = row["title"]
        m_cat = row.get("category", "Movie")
        m_genre = row.get("genre", "")
        poster_url = get_poster_url(m_title, m_cat)
        m_langs = list(get_available_languages(row).keys())

        matching_langs = [l for l in saved_langs if l in m_langs]
        play_lang = matching_langs[0] if matching_langs else default_play_lang

        href = f"?watch={quote_plus(m_title)}&lang={quote_plus(play_lang)}"
        lang_badge = f"{play_lang}" if not m_langs else f"{len(m_langs)} Langs" if len(m_langs) > 1 else m_langs[0]

        prog_bar = ""
        if progress_map and m_title in progress_map:
            mins = progress_map[m_title]
            pct = min(int((mins / 150.0) * 100), 100) if mins > 0 else 12
            prog_bar = f'<div class="progress-bar-bg"><div class="progress-bar-fill" style="width:{pct}%;"></div></div>'

        card = f"""
        <a class="poster-tile" href="{href}" target="_self">
            <div class="poster-img-wrap">
                <img src="{poster_url}" alt="{m_title}" loading="lazy" />
                <div class="poster-gradient"></div>
                <span class="lang-pill">{lang_badge}</span>
                <span class="play-badge">▶</span>
            </div>
            <div class="poster-info">
                <div class="poster-name">{m_title}</div>
                <div class="poster-sub">{m_genre}</div>
                {prog_bar}
            </div>
        </a>
        """
        cards_html.append(card)

    rail_block = f"""
    <div class="rail-header">
        <div class="rail-title">{title_text}</div>
        <div class="rail-hint">Swipe ➡️</div>
    </div>
    <div class="poster-rail">
        {''.join(cards_html)}
    </div>
    """
    st.markdown(rail_block, unsafe_allow_html=True)

# ---------------- JIOHOTSTAR TOP NAV & SPOTLIGHT BANNER ----------------

st.markdown(f"""
<div class="hotstar-nav">
    <div class="hotstar-brand">MOVIEZZ FLIX</div>
    <div class="hotstar-badge">👤 {current_user.upper()} • {', '.join(saved_langs).upper()}</div>
</div>
""", unsafe_allow_html=True)

# Featured Spotlight Banner (Top Personalized Pick)
movies_only_df = sort_by_user_prefs(movies[movies["category"].str.lower() == "movie"], saved_genres, saved_langs)
spotlight_row = movies_only_df.iloc[0] if not movies_only_df.empty else movies.iloc[0]
spot_title = spotlight_row["title"]
spot_genre = spotlight_row["genre"]
spot_cat = spotlight_row["category"]
spot_langs = list(get_available_languages(spotlight_row).keys()) or saved_langs

st.markdown(f"""
<div class="spotlight-card">
    <span class="spotlight-tag">🔥 #1 SPOTLIGHT FOR {current_user.upper()}</span>
    <div class="spotlight-title">{spot_title}</div>
    <div class="spotlight-meta">
        {spot_cat} &nbsp;•&nbsp; {spot_genre} &nbsp;•&nbsp; {' • '.join(spot_langs)}
    </div>
</div>
""", unsafe_allow_html=True)

sp_c1, sp_c2, sp_c3 = st.columns([1.4, 1.4, 2.2])
with sp_c1:
    if st.button(f"▶ Watch {spot_title}", use_container_width=True):
        st.session_state.selected_movie = spot_title
        st.session_state.selected_lang = saved_langs[0]
        st.session_state.resume_seconds = get_movie_progress(current_user, spot_title) * 60
        st.session_state.is_playing = True
        st.rerun()
with sp_c2:
    if spot_title in user_watchlist:
        if st.button("✓ In Watchlist", use_container_width=True):
            remove_from_watchlist(current_user, spot_title)
            st.rerun()
    else:
        if st.button("+ My Watchlist", use_container_width=True):
            add_to_watchlist(current_user, spot_title)
            st.toast(f"Added '{spot_title}'!")
            st.rerun()
with sp_c3:
    all_titles = sorted(movies["title"].unique().tolist())
    quick_pick = st.selectbox(
        "Search Catalog",
        options=[""] + all_titles,
        format_func=lambda x: "🔍 Search Movies, Series, Serials, Jabardasth..." if x == "" else x,
        label_visibility="collapsed"
    )
    if quick_pick:
        st.session_state.selected_movie = quick_pick
        st.session_state.selected_lang = saved_langs[0]
        st.session_state.resume_seconds = get_movie_progress(current_user, quick_pick) * 60
        st.session_state.is_playing = True
        st.rerun()

# JioHotstar Category Navigation Pills
st.write("")
tabs = ["All", "🎬 Movies", "📺 Web Series", "🎭 TV Serials", "😂 Jabardasth"]
t_cols = st.columns(len(tabs))
for i, t_name in enumerate(tabs):
    with t_cols[i]:
        if st.button(t_name, key=f"cat_tab_{t_name}", use_container_width=True):
            st.session_state.active_tab = t_name
            st.rerun()

# ---------------- ACTIVE VIDEO / EPISODE PLAYER ----------------

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
    active_cat = movie_df_match["category"].iloc[0] if not movie_df_match.empty else "Movie"
    saved_movie_langs = get_available_languages(movie_df_match.iloc[0]) if not movie_df_match.empty else {}

    default_idx = AVAILABLE_LANGUAGES.index(st.session_state.selected_lang) if st.session_state.selected_lang in AVAILABLE_LANGUAGES else 0
    chosen_lang = st.radio(
        "🌐 Audio Language:",
        options=AVAILABLE_LANGUAGES,
        index=default_idx,
        horizontal=True,
        key=f"lang_selector_{active_movie}"
    )
    st.session_state.selected_lang = chosen_lang

    raw_url = saved_movie_langs.get(chosen_lang) or (next(iter(saved_movie_langs.values())) if saved_movie_langs else "")
    yt_full_search = f"https://www.youtube.com/results?search_query={quote_plus(f'{active_movie} {chosen_lang} full')}"
    is_direct_video = bool(raw_url) and ("youtube.com/results" not in raw_url)

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
            if is_direct_video:
                start_sec = st.session_state.resume_seconds if st.session_state.resume_seconds > 0 else (current_saved_mins * 60)
                if start_sec > 0:
                    st.caption(f"⏩ Resuming **{active_movie}** in **{chosen_lang}** from **{start_sec // 60} mins**")
                else:
                    st.caption(f"🔊 Playing in **{chosen_lang}**")
                st.video(raw_url, start_time=start_sec, autoplay=True)
            else:
                target_link = raw_url if raw_url else yt_full_search
                st.info(f"📺 Watch **{active_movie}** latest full episodes directly on YouTube:")
                st.link_button(f"▶️ Watch {active_movie} Full Episodes on YouTube", target_link, use_container_width=True)

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
                        chat_in = st.text_input("Message", placeholder="Mass scene 🔥", label_visibility="collapsed")
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
                    "⏱️ Pause Progress (Save minute to resume later)",
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
                    st.session_state.resume_seconds = stopped_at_mins * 60
                    st.toast(f"Saved at {stopped_at_mins} mins!")
                    st.rerun()
            with p_col3:
                st.write("")
                if st.button("✅ Mark Finished", use_container_width=True):
                    remove_continue_watching(current_user, active_movie)
                    st.session_state.resume_seconds = 0
                    st.rerun()

    details = get_movie_details(active_movie)
    col1, col2 = st.columns([1, 2.5])

    with col1:
        st.image(get_poster_url(active_movie, active_cat), use_container_width=True)

    with col2:
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        with btn_col1:
            play_label = f"⏩ Resume ({current_saved_mins}m)" if current_saved_mins > 0 else f"▶ Play ({chosen_lang})"
            if st.button(play_label, key="play_main_btn", use_container_width=True):
                st.session_state.resume_seconds = current_saved_mins * 60
                st.session_state.is_playing = True
                st.rerun()
        with btn_col2:
            if active_movie in user_watchlist:
                if st.button("💔 Remove Watchlist", use_container_width=True):
                    remove_from_watchlist(current_user, active_movie)
                    st.rerun()
            else:
                if st.button("❤️ + Watchlist", use_container_width=True):
                    add_to_watchlist(current_user, active_movie)
                    st.rerun()
        with btn_col3:
            yt_btn_target = raw_url if (raw_url and "youtube.com/results" in raw_url) else yt_full_search
            st.link_button("📺 Open on YouTube", yt_btn_target, use_container_width=True)

        avg_rating, rating_count = get_average_rating(active_movie)
        user_rating_text = f"⭐ {avg_rating}/5 ({rating_count} reviews)" if avg_rating else "No ratings yet"
        st.write(f"**📂 Category:** {active_cat}  |  **👥 Rating:** {user_rating_text}")

        if details:
            st.write(f"**📅 Year:** {details.get('Year', 'N/A')}  |  **⭐ IMDb:** {details.get('imdbRating', 'N/A')}  |  **⏱️ Runtime:** {details.get('Runtime', 'N/A')}")
            st.write(f"**🎭 Genre:** {details.get('Genre', 'N/A')}")
            st.write(f"**🌟 Cast:** {details.get('Actors', 'N/A')}")
            st.write(details.get("Plot", "N/A"))
        elif not movie_df_match.empty:
            st.write(f"**🎭 Genre:** {movie_df_match['genre'].iloc[0]}")

    # Rate & Review
    st.markdown("### ⭐ Rate & Review")
    rev_col1, rev_col2 = st.columns([1, 1.5])
    with rev_col1:
        with st.form(f"review_form_{active_movie}"):
            star_rating = st.slider("Your Rating", 1, 5, 5)
            review_text = st.text_area("Review (optional)", placeholder="Super entertainment...")
            if st.form_submit_button("Submit Review", use_container_width=True):
                save_review(current_user, active_movie, star_rating, review_text)
                st.success("Review saved!")
                st.rerun()
    with rev_col2:
        all_reviews = get_movie_reviews(active_movie)
        if all_reviews:
            for r_user, r_stars, r_comment in all_reviews[:3]:
                with st.container(border=True):
                    st.markdown(f"**@{r_user}** — {'⭐' * r_stars} ({r_stars}/5)")
                    if r_comment:
                        st.write(r_comment)
        else:
            st.info("No reviews yet.")

# ---------------- JIOHOTSTAR HORIZONTAL POSTER RAILS ----------------

active_tab = st.session_state.active_tab

# 1. Continue Watching Rail
if cw_list and active_tab == "All":
    cw_titles = [item[0] for item in cw_list]
    cw_prog_map = {item[0]: item[2] for item in cw_list}
    cw_df = movies[movies["title"].isin(cw_titles)].copy()
    # Preserve recent order
    cw_df["order"] = cw_df["title"].apply(lambda t: cw_titles.index(t))
    cw_df = cw_df.sort_values("order")
    render_hotstar_rail(f"⏯️ Continue Watching for {current_user}", cw_df, saved_langs[0], progress_map=cw_prog_map)

# 2. Movies Rail
if active_tab in ("All", "🎬 Movies"):
    render_hotstar_rail("🎬 Recommended Movies", movies_only_df, saved_langs[0])

# 3. Web Series Rail
if active_tab in ("All", "📺 Web Series"):
    series_only_df = sort_by_user_prefs(movies[movies["category"].str.lower() == "web series"], saved_genres, saved_langs)
    render_hotstar_rail("📺 Popular Web Series", series_only_df, saved_langs[0])

# 4. Daily TV Serials Rail
if active_tab in ("All", "🎭 TV Serials"):
    serials_only_df = movies[movies["category"].str.lower() == "serial"]
    render_hotstar_rail("🎭 Daily TV Serials", serials_only_df, saved_langs[0])

# 5. Jabardasth & Comedy Shows Rail
if active_tab in ("All", "😂 Jabardasth"):
    jabardasth_only_df = movies[movies["category"].str.lower() == "jabardasth"]
    render_hotstar_rail("😂 Jabardasth & TV Comedy Shows", jabardasth_only_df, "Telugu")

# ---------------- FOOTER ----------------

st.divider()
st.markdown("""
<div style="text-align:center; padding:18px; color:#64748b;">
    <h3 style="color:#3b82f6 !important; margin-bottom:4px;">MOVIEZZ FLIX</h3>
    Movies • Web Series • Serials • Jabardasth 🍿<br>
    © 2026 MOVIEZZ FLIX
</div>
""", unsafe_allow_html=True)
