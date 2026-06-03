import sqlite3
import json
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "autotube.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            niche TEXT NOT NULL,
            title TEXT,
            tone TEXT DEFAULT 'neutral',
            channel_id TEXT,
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            current_step TEXT,
            script TEXT,
            audio_path TEXT,
            video_path TEXT,
            youtube_url TEXT,
            cost_total REAL DEFAULT 0,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS job_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            step TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            message TEXT,
            cost REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS cost_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            service TEXT NOT NULL,
            units REAL,
            unit_cost REAL,
            total_cost REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            youtube_channel_id TEXT,
            niche TEXT,
            access_token TEXT,
            refresh_token TEXT,
            connected INTEGER DEFAULT 0,
            subscribers INTEGER DEFAULT 0,
            videos_count INTEGER DEFAULT 0,
            total_views INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS channel_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            date TEXT NOT NULL,
            subscribers INTEGER DEFAULT 0,
            total_views INTEGER DEFAULT 0,
            estimated_revenue REAL DEFAULT 0,
            videos_count INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()

def create_job(job_id: str, niche: str, title: str, tone: str, channel_id: str = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO jobs (id, niche, title, tone, channel_id) VALUES (?, ?, ?, ?, ?)",
        (job_id, niche, title, tone, channel_id)
    )
    conn.commit()
    conn.close()

def update_job(job_id: str, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    conn = get_conn()
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()

def add_step(job_id: str, step: str, status: str, message: str = None, cost: float = 0):
    conn = get_conn()
    conn.execute(
        "INSERT INTO job_steps (job_id, step, status, message, cost) VALUES (?, ?, ?, ?, ?)",
        (job_id, step, status, message, cost)
    )
    conn.commit()
    conn.close()

def add_cost_event(job_id: str, service: str, units: float, unit_cost: float, total: float):
    conn = get_conn()
    conn.execute(
        "INSERT INTO cost_events (job_id, service, units, unit_cost, total_cost) VALUES (?, ?, ?, ?, ?)",
        (job_id, service, units, unit_cost, total)
    )
    conn.execute("UPDATE jobs SET cost_total = COALESCE(cost_total, 0) + ? WHERE id = ?", (total, job_id))
    conn.commit()
    conn.close()

def get_job(job_id: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_jobs(limit=50):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_finance_summary():
    conn = get_conn()
    month = datetime.now().strftime("%Y-%m")

    total_cost = conn.execute(
        "SELECT COALESCE(SUM(cost_total), 0) FROM jobs WHERE created_at LIKE ? AND status='done'",
        (f"{month}%",)
    ).fetchone()[0]
    total_videos = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE created_at LIKE ? AND status='done'",
        (f"{month}%",)
    ).fetchone()[0]
    avg_cost = (total_cost / total_videos) if total_videos > 0 else 0

    # Desglose granular por servicio (todos los eventos del mes, incluyendo jobs en curso)
    by_service = conn.execute(
        "SELECT service, ROUND(SUM(total_cost),5) as total, SUM(units) as units "
        "FROM cost_events WHERE created_at LIKE ? GROUP BY service ORDER BY total DESC",
        (f"{month}%",)
    ).fetchall()

    # Agrupar por proveedor para vista resumida
    provider_map = {
        # FAL.AI
        "fal_flux_schnell":    "fal.ai",
        "fal_flux_hook":       "fal.ai",
        "fal_flux_thumbnail":  "fal.ai",
        "flux_schnell":        "fal.ai",
        "flux_schnell_thumb":  "fal.ai",
        "ideogram_thumbnail":  "fal.ai",
        # Anthropic
        "claude_haiku":        "anthropic",
        "claude_haiku_ext":    "anthropic",
        "claude_haiku_prompts":"anthropic",
        "claude_sonnet":       "anthropic",
        "claude_sonnet_ext":   "anthropic",
        # OpenAI
        "openai_tts":          "openai",
        # Gratis
        "edge_tts":            "gratis",
        "kokoro_tts":          "gratis",
        "pollinations":        "gratis",
    }
    by_provider: dict[str, float] = {}
    for row in by_service:
        svc = row["service"]
        prov = provider_map.get(svc, svc)
        by_provider[prov] = round(by_provider.get(prov, 0) + row["total"], 5)

    # Últimos 10 vídeos con su coste desglosado
    jobs_detail = conn.execute(
        "SELECT id, title, niche, status, cost_total, created_at, "
        "youtube_url, finished_at FROM jobs WHERE created_at LIKE ? "
        "ORDER BY created_at DESC LIMIT 10",
        (f"{month}%",)
    ).fetchall()

    jobs_with_breakdown = []
    for j in jobs_detail:
        jd = dict(j)
        svc_rows = conn.execute(
            "SELECT service, ROUND(SUM(total_cost),5) as total "
            "FROM cost_events WHERE job_id=? GROUP BY service ORDER BY total DESC",
            (j["id"],)
        ).fetchall()
        jd["breakdown"] = [dict(r) for r in svc_rows]
        jobs_with_breakdown.append(jd)

    conn.close()
    return {
        "month": month,
        "total_cost":        round(total_cost, 4),
        "total_videos":      total_videos,
        "avg_cost_per_video": round(avg_cost, 4),
        "by_service": [dict(r) for r in by_service],
        "by_provider": [{"provider": k, "total": v}
                        for k, v in sorted(by_provider.items(), key=lambda x: -x[1])],
        "jobs": jobs_with_breakdown,
    }

def get_channels():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM channels ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def upsert_channel(channel_id: str, name: str, niche: str = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO channels (id, name, niche) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
        (channel_id, name, niche)
    )
    conn.commit()
    conn.close()
