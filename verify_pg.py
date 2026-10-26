import psycopg2

def verify():
    try:
        conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:54322/postgres")
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;")
        tables = [t[0] for t in cur.fetchall()]
        print("Connected successfully to Local Supabase PostgreSQL!")
        print("Public tables:", tables)

        for tbl in ['assessment_settings', 'exams', 'questions', 'students', 'facilitators', 'submissions']:
            if tbl in tables:
                cur.execute(f"SELECT COUNT(*) FROM {tbl};")
                cnt = cur.fetchone()[0]
                print(f"  -> {tbl}: {cnt} records")
        cur.close()
        conn.close()
    except Exception as e:
        print("Connection failed:", e)

if __name__ == "__main__":
    verify()

