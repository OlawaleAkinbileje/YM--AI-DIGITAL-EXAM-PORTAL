"""
YMO CBT Portal - Supabase Local PostgreSQL Adapter
Connects to local Supabase PostgreSQL engine running on Docker:
postgresql://postgres:postgres@127.0.0.1:54322/postgres
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

SUPABASE_PG_URL = os.environ.get(
    "SUPABASE_DB_URL", 
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)

def is_supabase_running():
    """Checks if the local Supabase PostgreSQL engine is reachable."""
    try:
        conn = psycopg2.connect(SUPABASE_PG_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False

def get_supabase_db():
    """Returns a PostgreSQL connection with dictionary cursor."""
    conn = psycopg2.connect(SUPABASE_PG_URL)
    return conn

def get_supabase_cursor(conn):
    """Returns a cursor that produces dict-like rows matching SQLite Row."""
    return conn.cursor(cursor_factory=RealDictCursor)

if __name__ == "__main__":
    running = is_supabase_running()
    print("Local Supabase Engine Running on 54322?:", running)

