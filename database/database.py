import sqlite3
import os
from datetime import datetime, date

DB_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "athena.db"),
)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            sector TEXT,
            geography TEXT,
            city TEXT,
            website TEXT,
            stage TEXT,
            heat_score INTEGER DEFAULT 1,
            previous_heat_score INTEGER DEFAULT 1,
            first_detected DATE,
            last_updated DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            source_type TEXT,
            source_name TEXT,
            source_url TEXT,
            signal_layer TEXT,
            title TEXT,
            metadata TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS universities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            country TEXT,
            city TEXT,
            rank INTEGER,
            score INTEGER,
            num_spinouts INTEGER,
            total_funding TEXT,
            sourcing_url TEXT,
            secondary_urls TEXT,
            page_type TEXT,
            scraping_score REAL,
            update_frequency TEXT,
            scout_priority_score REAL,
            ai_labs TEXT,
            clubs TEXT,
            primary_contact_email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            program_name TEXT,
            program_type TEXT,
            program_country TEXT,
            cohort TEXT,
            funding_amount TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS founders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            title TEXT,
            linkedin_url TEXT,
            email TEXT,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pipeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'new',
            added_by TEXT DEFAULT 'scout',
            added_at TEXT NOT NULL,
            moved_at TEXT,
            position INTEGER DEFAULT 0,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            author TEXT NOT NULL DEFAULT 'scout',
            author_role TEXT NOT NULL DEFAULT 'scout',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    """)

    # Migration: add previous_heat_score if missing (existing DBs)
    try:
        cursor.execute("SELECT previous_heat_score FROM companies LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE companies ADD COLUMN previous_heat_score INTEGER DEFAULT 1")

    # Migration: add stage tracking columns
    for col in ("stage_source", "stage_detected_date"):
        try:
            cursor.execute(f"SELECT {col} FROM companies LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute(f"ALTER TABLE companies ADD COLUMN {col} TEXT")

    # Migration: add athena_score columns
    for col, coltype in [("athena_score", "REAL"), ("athena_score_breakdown", "TEXT")]:
        try:
            cursor.execute(f"SELECT {col} FROM companies LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute(f"ALTER TABLE companies ADD COLUMN {col} {coltype}")

    # Migration: add thesis_override columns
    for col, coltype in [("thesis_override", "REAL"), ("thesis_override_reason", "TEXT")]:
        try:
            cursor.execute(f"SELECT {col} FROM companies LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute(f"ALTER TABLE companies ADD COLUMN {col} {coltype}")

    # Migration: add newness tracking columns
    for col, coltype in [
        ("ssl_first_seen", "TEXT"),
        ("newness_status", "TEXT"),
        ("newness_checked_at", "TEXT"),
    ]:
        try:
            cursor.execute(f"SELECT {col} FROM companies LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute(f"ALTER TABLE companies ADD COLUMN {col} {coltype}")

    # Migration: create scrape_snapshots table for future snapshot diffing
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scrape_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            company_names TEXT NOT NULL
        )
    """)

    # Backfill stage_detected_date and stage_source for existing data
    cursor.execute("""
        UPDATE companies SET
            stage_detected_date = (
                SELECT MIN(DATE(detected_at)) FROM signals
                WHERE signals.company_id = companies.id
            ),
            stage_source = (
                SELECT COALESCE(
                    (SELECT program_name FROM programs
                     WHERE programs.company_id = companies.id LIMIT 1),
                    (SELECT source_name FROM signals
                     WHERE signals.company_id = companies.id
                     ORDER BY detected_at ASC LIMIT 1)
                )
            )
        WHERE stage IS NOT NULL AND stage_detected_date IS NULL
    """)

    conn.commit()
    conn.close()


# --- Companies ---

def insert_company(name, description=None, sector=None, geography=None,
                   city=None, website=None, stage=None, heat_score=1,
                   stage_source=None, stage_detected_date=None):
    conn = get_connection()
    today = date.today().isoformat()
    cursor = conn.execute(
        """INSERT INTO companies
           (name, description, sector, geography, city, website, stage,
            heat_score, first_detected, last_updated,
            stage_source, stage_detected_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, description, sector, geography, city, website, stage,
         heat_score, today, today, stage_source, stage_detected_date)
    )
    company_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return company_id


def get_company(company_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_companies():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM companies ORDER BY heat_score DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_company_by_name(name):
    conn = get_connection()
    row = conn.execute("SELECT * FROM companies WHERE name = ?", (name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_company(company_id, **fields):
    if not fields:
        return
    fields["last_updated"] = date.today().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [company_id]
    conn = get_connection()
    conn.execute(f"UPDATE companies SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def update_company_stage(company_id, stage, stage_source, stage_detected_date):
    """Update stage only if the new source is more recent than existing.
    Returns True if updated."""
    conn = get_connection()
    row = conn.execute(
        "SELECT stage_detected_date FROM companies WHERE id = ?",
        (company_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    existing_date = row["stage_detected_date"]
    if existing_date and stage_detected_date and existing_date >= stage_detected_date:
        conn.close()
        return False
    conn.execute(
        """UPDATE companies
           SET stage = ?, stage_source = ?, stage_detected_date = ?,
               last_updated = ?
           WHERE id = ?""",
        (stage, stage_source, stage_detected_date,
         date.today().isoformat(), company_id)
    )
    conn.commit()
    conn.close()
    return True


# --- Signals ---

def insert_signal(company_id, source_type=None, source_name=None,
                  source_url=None, signal_layer=None, title=None,
                  metadata=None):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO signals
           (company_id, source_type, source_name, source_url,
            signal_layer, title, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, source_type, source_name, source_url,
         signal_layer, title, metadata)
    )
    signal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return signal_id


def get_signals_for_company(company_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM signals WHERE company_id = ? ORDER BY detected_at DESC",
        (company_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Programs ---

def insert_program(company_id, program_name=None, program_type=None,
                   program_country=None, cohort=None, funding_amount=None):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO programs
           (company_id, program_name, program_type, program_country,
            cohort, funding_amount)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (company_id, program_name, program_type, program_country,
         cohort, funding_amount)
    )
    program_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return program_id


def get_programs_for_company(company_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM programs WHERE company_id = ? ORDER BY detected_at DESC",
        (company_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Founders ---

def insert_founder(company_id, name, title=None, linkedin_url=None,
                   email=None, source=None):
    conn = get_connection()
    # Skip if this exact founder already exists for this company
    existing = conn.execute(
        "SELECT id FROM founders WHERE company_id = ? AND LOWER(name) = LOWER(?)",
        (company_id, name)
    ).fetchone()
    if existing:
        conn.close()
        return existing["id"]
    cursor = conn.execute(
        """INSERT INTO founders
           (company_id, name, title, linkedin_url, email, source)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (company_id, name, title, linkedin_url, email, source)
    )
    founder_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return founder_id


def get_founders_for_company(company_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM founders WHERE company_id = ? ORDER BY id",
        (company_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
