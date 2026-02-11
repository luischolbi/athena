import sqlite3
import os
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "athena.db")


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

    # Migration: add previous_heat_score if missing (existing DBs)
    try:
        cursor.execute("SELECT previous_heat_score FROM companies LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE companies ADD COLUMN previous_heat_score INTEGER DEFAULT 1")

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

    conn.commit()
    conn.close()


# --- Companies ---

def insert_company(name, description=None, sector=None, geography=None,
                   city=None, website=None, stage=None, heat_score=1):
    conn = get_connection()
    today = date.today().isoformat()
    cursor = conn.execute(
        """INSERT INTO companies
           (name, description, sector, geography, city, website, stage,
            heat_score, first_detected, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, description, sector, geography, city, website, stage,
         heat_score, today, today)
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


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
