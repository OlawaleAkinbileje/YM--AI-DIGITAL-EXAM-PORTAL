import sqlite3
import json
import csv
import io
import re
import urllib.request
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = Path(__file__).parent / "ymo_cbt.db"
QUESTIONS_JSON_PATH = Path(__file__).parent / "questions.json"

CANONICAL_COURSES = [
    {
        "id": 1,
        "code": "AIGA",
        "title": "Artificial Intelligence and Generative AI",
        "cohort": "YMO CBT Assessment — Artificial Intelligence & Generative AI",
        "instructions": "Choose the single best answer (A, B, C, or D). Each question carries one mark.",
        "duration_minutes": 60,
        "passing_cutoff": 85.0
    },
    {
        "id": 2,
        "code": "DL",
        "title": "Digital Literacy",
        "cohort": "YMO CBT Assessment — Digital Literacy",
        "instructions": "Choose the single best answer (A, B, C, or D). Each question carries one mark.",
        "duration_minutes": 60,
        "passing_cutoff": 85.0
    },
    {
        "id": 3,
        "code": "GDCC",
        "title": "Graphic Design and Content Creation",
        "cohort": "YMO CBT Assessment — Graphic Design & Content Creation",
        "instructions": "Choose the single best answer (A, B, C, or D). Each question carries one mark.",
        "duration_minutes": 60,
        "passing_cutoff": 85.0
    },
    {
        "id": 4,
        "code": "CP",
        "title": "Coding and Programming",
        "cohort": "YMO CBT Assessment — Coding & Programming",
        "instructions": "Choose the single best answer (A, B, C, or D). Each question carries one mark.",
        "duration_minutes": 60,
        "passing_cutoff": 85.0
    },
    {
        "id": 5,
        "code": "DM",
        "title": "Digital Marketing and Social Media",
        "cohort": "YMO CBT Assessment — Digital Marketing & Social Media",
        "instructions": "Choose the single best answer (A, B, C, or D). Each question carries one mark.",
        "duration_minutes": 60,
        "passing_cutoff": 85.0
    }
]

OFFICIAL_COURSES = [
    {
        "code": "AIGA",
        "title": "Artificial Intelligence and Generative AI",
        "patterns": ["aiga", "artificial intelligence", "artificial inteligence", "generative ai", "ai and generative", "ai & generative"]
    },
    {
        "code": "DL",
        "title": "Digital Literacy",
        "patterns": ["dl", "digital literacy", "literacy"]
    },
    {
        "code": "GDCC",
        "title": "Graphic Design and Content Creation",
        "patterns": ["gdcc", "graphic design", "graphics design", "content creation"]
    },
    {
        "code": "CP",
        "title": "Coding and Programming",
        "patterns": ["cp", "coding and programming", "coding & programming", "programming", "coding"]
    },
    {
        "code": "DM",
        "title": "Digital Marketing and Social Media",
        "patterns": ["dm", "dmsm", "digital marketing", "marketing and social", "marketing & social", "social media"]
    }
]

DEFAULT_SETTINGS = {
    "title": "YEYE MOREMI OODUA AI & DIGITAL TRAINING HUB",
    "hub_name": "YEYE MOREMI OODUA AI & DIGITAL TRAINING HUB",
    "cohort": "Comprehensive CBT Assessment Portal",
    "instructions": "Choose the single best answer (A, B, C, or D). Each question carries one mark.",
    "duration_minutes": "60",
    "passing_cutoff": "85.0",
    "gemini_api_key": "",
    "require_registration": "true"
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def normalize_reg_number(raw_reg):
    """
    Standardizes registration numbers to the official format: YMO-{CODE}-{NUM:03d}
    e.g. YMO-DL-0001 -> YMO-DL-001, YMO-DMSM-0001 -> YMO-DM-001
    """
    if not raw_reg:
        return ""
    clean = str(raw_reg).strip().upper().replace('/', '-').replace('_', '-')
    m = re.match(r'^(?:YMO-)?([A-Z]+)-(\d+)$', clean)
    if m:
        track, num_str = m.group(1), m.group(2)
        if track == "DMSM":
            track = "DM"
        num = int(num_str)
        return f"YMO-{track}-{num:03d}"
    return clean

def get_reg_lookup_keys(raw_reg):
    """
    Generates all possible equivalent keys for flexible registration number matching.
    Supports 3-digit vs 4-digit numbers, DMSM vs DM, slash vs dash, and leading zeroes.
    """
    if not raw_reg:
        return set()
    norm = normalize_reg_number(raw_reg)
    keys = {str(raw_reg).strip().upper(), norm}
    clean = str(raw_reg).strip().upper().replace('/', '-').replace('_', '-')
    m = re.match(r'^(?:YMO-)?([A-Z]+)-(\d+)$', clean)
    if m:
        track, num_str = m.group(1), m.group(2)
        if track == "DMSM":
            track = "DM"
        num = int(num_str)
        keys.add(f"YMO-{track}-{num:03d}")
        keys.add(f"YMO-{track}-{num:04d}")
        keys.add(f"YMO-{track}-{num}")
        keys.add(f"YMO/{track}/{num:03d}")
        keys.add(f"YMO/{track}/{num:04d}")
        if track == "DM":
            keys.add(f"YMO-DMSM-{num:03d}")
            keys.add(f"YMO-DMSM-{num:04d}")
            keys.add(f"YMO-DMSM-{num}")
            keys.add(f"YMO/DMSM/{num:03d}")
    return {k.upper() for k in keys}

def extract_drive_file_id(url):
    """Extracts the Google Drive file ID from any Google Drive URL variant."""
    if not url:
        return None
    url = str(url).strip()
    m = re.search(r'[#&?]id=([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1)
    m2 = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if m2:
        return m2.group(1)
    m3 = re.search(r'googleusercontent\.com/d/([a-zA-Z0-9_-]+)', url)
    if m3:
        return m3.group(1)
    m4 = re.search(r'drive\.google\.com/[^/]+/([a-zA-Z0-9_-]{25,})', url)
    if m4:
        return m4.group(1)
    return None

def format_passport_image_url(raw_url):
    """
    Standardizes passport image URLs to direct Google CDN image endpoints:
    https://lh3.googleusercontent.com/d/{file_id}
    which bypasses Google Drive web restrictions and renders smoothly in <img> tags.
    """
    if not raw_url:
        return ""
    file_id = extract_drive_file_id(raw_url)
    if file_id:
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    return str(raw_url).strip()


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 1. Exams table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            cohort TEXT,
            instructions TEXT,
            duration_minutes INTEGER DEFAULT 60,
            passing_cutoff REAL DEFAULT 85.0,
            is_active INTEGER DEFAULT 1,
            course_code TEXT DEFAULT '',
            course_id INTEGER DEFAULT NULL,
            created_by INTEGER DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(exams)")
    exam_cols = [col[1] for col in cursor.fetchall()]
    if 'course_code' not in exam_cols:
        cursor.execute("ALTER TABLE exams ADD COLUMN course_code TEXT DEFAULT ''")
    if 'course_id' not in exam_cols:
        cursor.execute("ALTER TABLE exams ADD COLUMN course_id INTEGER DEFAULT NULL")
    if 'created_by' not in exam_cols:
        cursor.execute("ALTER TABLE exams ADD COLUMN created_by INTEGER DEFAULT NULL")

    # Seed the 5 Canonical Courses if exams table is empty
    cursor.execute("SELECT COUNT(*) FROM exams")
    if cursor.fetchone()[0] == 0:
        for c in CANONICAL_COURSES:
            cursor.execute("""
                INSERT INTO exams (id, title, cohort, instructions, duration_minutes, passing_cutoff, is_active, course_code, course_id)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (
                c["id"],
                c["title"],
                c["cohort"],
                c["instructions"],
                c["duration_minutes"],
                c["passing_cutoff"],
                c["code"],
                c["id"]
            ))
    else:
        # Backfill existing canonical courses with their official codes and course_ids
        canonical_map = {
            1: ("AIGA", "Artificial Intelligence and Generative AI"),
            2: ("DL", "Digital Literacy"),
            3: ("GDCC", "Graphic Design and Content Creation"),
            4: ("CP", "Coding and Programming"),
            5: ("DM", "Digital Marketing and Social Media")
        }
        for cid, (code, title) in canonical_map.items():
            cursor.execute("UPDATE exams SET course_code = ?, course_id = ? WHERE id = ?", (code, cid, cid))

    # 2. Submissions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER DEFAULT 1,
            first_name TEXT,
            last_name TEXT,
            attempt_number INTEGER DEFAULT 1,
            score INTEGER,
            total INTEGER,
            percentage REAL,
            passed INTEGER,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("PRAGMA table_info(submissions)")
    sub_cols = [col[1] for col in cursor.fetchall()]
    if 'attempt_number' not in sub_cols:
        cursor.execute("ALTER TABLE submissions ADD COLUMN attempt_number INTEGER DEFAULT 1")
    if 'exam_id' not in sub_cols:
        cursor.execute("ALTER TABLE submissions ADD COLUMN exam_id INTEGER DEFAULT 1")
    if 'reg_number' not in sub_cols:
        cursor.execute("ALTER TABLE submissions ADD COLUMN reg_number TEXT")
    if 'is_archived' not in sub_cols:
        cursor.execute("ALTER TABLE submissions ADD COLUMN is_archived INTEGER DEFAULT 0")

    # 3. Facilitators table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facilitators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'facilitator',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3b. Facilitator Course Assignment Table (for scoped course visibility)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facilitator_courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            facilitator_id INTEGER NOT NULL,
            exam_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(facilitator_id, exam_id),
            FOREIGN KEY (facilitator_id) REFERENCES facilitators(id) ON DELETE CASCADE,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
        )
    """)

    # Ensure instructor sparrow (Olawale Emmanuel) exists and has scheduled courses
    cursor.execute("SELECT id FROM facilitators WHERE username = 'instructor sparrow'")
    sparrow_row = cursor.fetchone()
    if not sparrow_row:
        sparrow_hash = generate_password_hash("sparrow2026")
        cursor.execute("""
            INSERT INTO facilitators (username, name, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, ("instructor sparrow", "Olawale Emmanuel", sparrow_hash, "facilitator"))
        sparrow_id = cursor.lastrowid
    else:
        sparrow_id = sparrow_row[0]

    for cid in [3, 5]:
        cursor.execute("INSERT OR IGNORE INTO facilitator_courses (facilitator_id, exam_id) VALUES (?, ?)", (sparrow_id, cid))

    # Purge any legacy dummy facilitator accounts
    cursor.execute("DELETE FROM facilitator_courses WHERE facilitator_id IN (SELECT id FROM facilitators WHERE username = 'facilitator')")
    cursor.execute("DELETE FROM facilitators WHERE username = 'facilitator'")

    # Seed admin: username: admin / password: admin123 (Role: admin)
    cursor.execute("SELECT COUNT(*) FROM facilitators WHERE username = 'admin'")
    if cursor.fetchone()[0] == 0:
        admin_hash = generate_password_hash("admin123")
        cursor.execute("""
            INSERT INTO facilitators (username, name, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, ("admin", "Administrator", admin_hash, "admin"))
    else:
        cursor.execute("UPDATE facilitators SET role = 'admin' WHERE username = 'admin'")

    # 4. Assessment settings table (global settings like hub name and API keys)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assessment_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    for k, v in DEFAULT_SETTINGS.items():
        cursor.execute("INSERT OR IGNORE INTO assessment_settings (key, value) VALUES (?, ?)", (k, v))

    # 5. Questions table (NO dummy questions seeded)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            exam_id INTEGER DEFAULT 1,
            q_num INTEGER NOT NULL,
            question TEXT NOT NULL,
            opt_a TEXT NOT NULL,
            opt_b TEXT NOT NULL,
            opt_c TEXT NOT NULL,
            opt_d TEXT NOT NULL,
            answer TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("PRAGMA table_info(questions)")
    q_cols = [col[1] for col in cursor.fetchall()]
    if 'exam_id' not in q_cols:
        cursor.execute("ALTER TABLE questions ADD COLUMN exam_id INTEGER DEFAULT 1")
        cursor.execute("UPDATE questions SET exam_id = 1 WHERE exam_id IS NULL")

    # 6. Students registration table (NO dummy students seeded)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reg_number TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            passport_url TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("PRAGMA table_info(students)")
    stud_cols = [col[1] for col in cursor.fetchall()]
    if 'passport_url' not in stud_cols:
        cursor.execute("ALTER TABLE students ADD COLUMN passport_url TEXT")
    if 'primary_track' not in stud_cols:
        cursor.execute("ALTER TABLE students ADD COLUMN primary_track TEXT DEFAULT ''")

    # Backfill primary_track from reg_number if empty
    try:
        cursor.execute("SELECT id, reg_number, primary_track FROM students")
        for s_id, s_reg, s_trk in cursor.fetchall():
            if not s_trk:
                m = re.match(r'^(?:YMO-)?([A-Z]+)-\d+$', (s_reg or "").upper().replace('/', '-'))
                c_code = m.group(1) if m else ""
                if c_code == "DMSM":
                    c_code = "DM"
                if c_code:
                    cursor.execute("UPDATE students SET primary_track = ? WHERE id = ?", (c_code, s_id))
    except Exception:
        pass


    # 7. Student-Exam Course Access Mapping (Whitelist)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_exam_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            exam_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, exam_id),
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
        )
    """)

    # 8. Upgrade existing passport URLs to direct Google CDN endpoints
    try:
        cursor.execute("SELECT id, passport_url FROM students WHERE passport_url IS NOT NULL AND passport_url != ''")
        for s_row in cursor.fetchall():
            orig_url = s_row[1]
            fid = extract_drive_file_id(orig_url)
            if fid and not orig_url.startswith("https://lh3.googleusercontent.com/d/"):
                new_url = f"https://lh3.googleusercontent.com/d/{fid}"
                cursor.execute("UPDATE students SET passport_url = ? WHERE id = ?", (new_url, s_row[0]))
    except Exception:
        pass

    conn.commit()
    conn.close()

def ensure_canonical_courses():
    """
    Enforces that only the 5 official courses exist in the system and purges obsolete tracks.
    """
    conn = get_db()
    cursor = conn.cursor()

    # 1. Ensure all 5 canonical courses exist
    for c in CANONICAL_COURSES:
        cursor.execute("SELECT id FROM exams WHERE id = ?", (c["id"],))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("""
                UPDATE exams 
                SET title = ?, cohort = ?, duration_minutes = ?, passing_cutoff = ?,
                    course_code = COALESCE(NULLIF(course_code, ''), ?),
                    is_active = 1
                WHERE id = ?
            """, (c["title"], c["cohort"], c["duration_minutes"], c["passing_cutoff"], c["code"], c["id"]))
        else:
            cursor.execute("""
                INSERT INTO exams (id, title, cohort, instructions, duration_minutes, passing_cutoff, is_active, course_code, course_id)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (c["id"], c["title"], c["cohort"], c["instructions"], c["duration_minutes"], c["passing_cutoff"], c["code"], c["id"]))

    # 2. Purge any known dummy/seed student records
    dummy_patterns = ['YMO/2026/%', 'YMO/DMS/%', 'kehinde@ymo.edu.ng', 'folashade@ymo.edu.ng', 'babatunde@ymo.edu.ng']
    for p in dummy_patterns:
        cursor.execute("SELECT id FROM students WHERE reg_number LIKE ? OR email LIKE ?", (p, p))
        dummy_ids = [r[0] for r in cursor.fetchall()]
        for did in dummy_ids:
            cursor.execute("DELETE FROM student_exam_access WHERE student_id = ?", (did,))
            cursor.execute("DELETE FROM students WHERE id = ?", (did,))

    # 4. Wipe out dummy questions
    cursor.execute("DELETE FROM questions WHERE question LIKE '%campus food-delivery%' OR question LIKE '%social platforms%'")

    conn.commit()
    conn.close()

# ----------------- Exam Management -----------------

def get_all_exams(active_only=False):
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT e.*, COALESCE(q_counts.q_count, 0) as question_count
        FROM exams e
        LEFT JOIN (
            SELECT exam_id, COUNT(*) as q_count 
            FROM questions 
            GROUP BY exam_id
        ) q_counts ON e.id = q_counts.exam_id
    """
    if active_only:
        query += " WHERE e.is_active = 1 ORDER BY e.id ASC"
    else:
        query += " ORDER BY e.id ASC"

    rows = cursor.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_exam(exam_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.*, COALESCE(q_counts.q_count, 0) as question_count
        FROM exams e
        LEFT JOIN (
            SELECT exam_id, COUNT(*) as q_count 
            FROM questions 
            GROUP BY exam_id
        ) q_counts ON e.id = q_counts.exam_id
        WHERE e.id = ?
    """, (exam_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def create_exam(title, cohort="", duration_minutes=60, passing_cutoff=85.0, instructions="", is_active=1, course_code="", course_id=None, created_by=None, auto_assign_students=True):
    title = title.strip()
    cohort = cohort.strip()
    course_code = (course_code or "").strip().upper()

    canonical_codes = {
        "AIGA": 1,
        "DL": 2,
        "GDCC": 3,
        "CP": 4,
        "DM": 5
    }
    if course_code in canonical_codes and not course_id:
        course_id = canonical_codes[course_code]
    elif course_id and not course_code:
        reverse_map = {1: "AIGA", 2: "DL", 3: "GDCC", 4: "CP", 5: "DM"}
        course_code = reverse_map.get(course_id, "")
    elif not course_code and not course_id:
        for c in OFFICIAL_COURSES:
            for p in c["patterns"]:
                if p in title.lower() or p in cohort.lower():
                    course_code = c["code"]
                    course_id = canonical_codes.get(course_code)
                    break
            if course_code:
                break

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO exams (title, cohort, duration_minutes, passing_cutoff, instructions, is_active, course_code, course_id, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        cohort,
        int(duration_minutes),
        float(passing_cutoff),
        instructions.strip() or "Choose the single best answer (A, B, C, or D). Each question carries one mark.",
        int(is_active),
        course_code,
        course_id,
        created_by
    ))
    new_id = cursor.lastrowid

    # If created by a facilitator, give them immediate access in facilitator_courses
    if created_by:
        cursor.execute("INSERT OR IGNORE INTO facilitator_courses (facilitator_id, exam_id) VALUES (?, ?)", (created_by, new_id))

    # Give all facilitators who manage this course track access as well
    if course_id:
        cursor.execute("""
            INSERT OR IGNORE INTO facilitator_courses (facilitator_id, exam_id)
            SELECT DISTINCT facilitator_id, ? 
            FROM facilitator_courses 
            WHERE exam_id = ?
        """, (new_id, course_id))

    # If auto_assign_students, grant access to all students enrolled in this course track
    if auto_assign_students and (course_code or course_id):
        if course_code:
            cursor.execute("""
                INSERT OR IGNORE INTO student_exam_access (student_id, exam_id)
                SELECT id, ? FROM students 
                WHERE UPPER(reg_number) LIKE ? OR UPPER(reg_number) LIKE ?
            """, (new_id, f"YMO-{course_code}-%", f"%{course_code}%"))
        if course_id:
            cursor.execute("""
                INSERT OR IGNORE INTO student_exam_access (student_id, exam_id)
                SELECT DISTINCT student_id, ? 
                FROM student_exam_access 
                WHERE exam_id = ?
            """, (new_id, course_id))

    conn.commit()
    conn.close()
    return new_id

def update_exam(exam_id, title, cohort="", duration_minutes=60, passing_cutoff=85.0, instructions="", is_active=1, course_code=""):
    conn = get_db()
    cursor = conn.cursor()
    course_code = (course_code or "").strip().upper()
    canonical_codes = {"AIGA": 1, "DL": 2, "GDCC": 3, "CP": 4, "DM": 5}
    course_id = canonical_codes.get(course_code)

    if course_code:
        cursor.execute("""
            UPDATE exams
            SET title = ?, cohort = ?, duration_minutes = ?, passing_cutoff = ?, instructions = ?, is_active = ?, course_code = ?, course_id = ?
            WHERE id = ?
        """, (
            title.strip(),
            cohort.strip(),
            int(duration_minutes),
            float(passing_cutoff),
            instructions.strip(),
            int(is_active),
            course_code,
            course_id,
            exam_id
        ))
    else:
        cursor.execute("""
            UPDATE exams
            SET title = ?, cohort = ?, duration_minutes = ?, passing_cutoff = ?, instructions = ?, is_active = ?
            WHERE id = ?
        """, (
            title.strip(),
            cohort.strip(),
            int(duration_minutes),
            float(passing_cutoff),
            instructions.strip(),
            int(is_active),
            exam_id
        ))
    conn.commit()
    conn.close()

def delete_exam(exam_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM questions WHERE exam_id = ?", (exam_id,))
    cursor.execute("DELETE FROM submissions WHERE exam_id = ?", (exam_id,))
    cursor.execute("DELETE FROM facilitator_courses WHERE exam_id = ?", (exam_id,))
    cursor.execute("DELETE FROM student_exam_access WHERE exam_id = ?", (exam_id,))
    cursor.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
    conn.close()

# ----------------- Facilitator Authentication -----------------

def create_facilitator(username, name, password, role="facilitator"):
    username = username.strip().lower()
    name = name.strip()
    if not username or not password or not name:
        return False, "Username, name, and password are required."
    
    password_hash = generate_password_hash(password)
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO facilitators (username, name, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, (username, name, password_hash, role))
        conn.commit()
        return True, "Facilitator account created successfully."
    except sqlite3.IntegrityError:
        return False, f"Username '{username}' already exists."
    finally:
        conn.close()

def authenticate_facilitator(username, password):
    username = username.strip().lower()
    password = password.strip()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM facilitators WHERE LOWER(username) = ?", (username,))
    users = cursor.fetchall()
    conn.close()

    for user in users:
        if check_password_hash(user["password_hash"], password):
            return dict(user)
        # Fallback credentials for seeded default accounts
        if user["username"].lower() == "admin" and password in ("admin123", "admin"):
            return dict(user)
        if user["username"].lower() == "facilitator" and password in ("ymo@admin2026", "admin123"):
            return dict(user)

    return None

def get_all_facilitators():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, name, role, created_at FROM facilitators ORDER BY id ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_facilitator_courses(facilitator_id):
    """Returns list of exams assigned to this facilitator."""
    conn = get_db()
    cursor = conn.cursor()
    rows = cursor.execute("""
        SELECT e.* 
        FROM facilitator_courses fc
        JOIN exams e ON fc.exam_id = e.id
        WHERE fc.facilitator_id = ?
        ORDER BY e.id ASC
    """, (facilitator_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def assign_facilitator_courses(facilitator_id, exam_ids):
    """Assigns/updates the specific courses a facilitator manages."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM facilitator_courses WHERE facilitator_id = ?", (facilitator_id,))
    if exam_ids:
        for eid in exam_ids:
            try:
                cursor.execute("INSERT OR IGNORE INTO facilitator_courses (facilitator_id, exam_id) VALUES (?, ?)", (facilitator_id, int(eid)))
            except Exception:
                pass
    conn.commit()
    conn.close()
    return True

def get_facilitator_accessible_exams(facilitator_id, role='facilitator'):
    """Admins see all exams; Facilitators see only their assigned courses."""
    if role == 'admin':
        return get_all_exams()
    assigned = get_facilitator_courses(facilitator_id)
    if not assigned:
        # If no specific courses assigned yet, return all active exams so they can select
        return get_all_exams(active_only=True)
    return assigned

def delete_facilitator(facilitator_id):
    """Deletes a facilitator and their course assignments, protecting the master admin."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role FROM facilitators WHERE id = ?", (facilitator_id,))
    fac = cursor.fetchone()
    if not fac:
        conn.close()
        return False, "Facilitator account not found."
    if fac["username"].lower() == "admin" or fac["role"] == "admin":
        conn.close()
        return False, "Cannot delete master administrator account."

    cursor.execute("DELETE FROM facilitator_courses WHERE facilitator_id = ?", (facilitator_id,))
    cursor.execute("DELETE FROM facilitators WHERE id = ?", (facilitator_id,))
    conn.commit()
    conn.close()
    return True, f"Facilitator '{fac['username']}' successfully deleted from database."

def create_course_track(code, title, cohort="", duration_minutes=60, passing_cutoff=85.0, instructions=""):
    """Creates a new course track in the database."""
    code = code.strip().upper()
    title = title.strip()
    if not code or not title:
        return False, "Course code and title are required."

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM exams WHERE UPPER(course_code) = ? OR LOWER(title) = LOWER(?)", (code, title))
    if cursor.fetchone():
        conn.close()
        return False, f"Course with code '{code}' or title '{title}' already exists."

    cursor.execute("""
        INSERT INTO exams (title, cohort, instructions, duration_minutes, passing_cutoff, is_active, course_code)
        VALUES (?, ?, ?, ?, ?, 1, ?)
    """, (title, cohort or f"YMO CBT Assessment - {title}", instructions or "Choose the single best answer. Each question carries one mark.", duration_minutes, passing_cutoff, code))
    new_id = cursor.lastrowid
    cursor.execute("UPDATE exams SET course_id = ? WHERE id = ?", (new_id, new_id))
    conn.commit()
    conn.close()
    return True, f"Course track '{title}' ({code}) created successfully."

# ----------------- Settings Management -----------------

def get_settings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM assessment_settings")
    settings = {row["key"]: row["value"] for row in cursor.fetchall()}
    conn.close()
    for k, v in DEFAULT_SETTINGS.items():
        if k not in settings:
            settings[k] = v
    return settings

def update_settings(new_settings: dict):
    conn = get_db()
    cursor = conn.cursor()
    for k, v in new_settings.items():
        cursor.execute("""
            INSERT INTO assessment_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (k, str(v).strip()))
    conn.commit()
    conn.close()

# ----------------- Questions Management -----------------

def get_questions_by_exam(exam_id=1):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions WHERE exam_id = ? ORDER BY q_num ASC", (exam_id,))
    rows = cursor.fetchall()
    conn.close()

    questions = []
    for r in rows:
        questions.append({
            "id": r["id"],
            "exam_id": r["exam_id"],
            "number": r["q_num"],
            "question": r["question"],
            "options": {
                "A": r["opt_a"],
                "B": r["opt_b"],
                "C": r["opt_c"],
                "D": r["opt_d"]
            },
            "answer": r["answer"]
        })
    return questions

def get_all_questions():
    """Convenience alias for Exam 1 or all questions."""
    return get_questions_by_exam(1)

def get_question(q_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM questions WHERE id = ?", (q_id,))
    r = cursor.fetchone()
    conn.close()
    if not r:
        return None
    return {
        "id": r["id"],
        "exam_id": r["exam_id"],
        "number": r["q_num"],
        "question": r["question"],
        "options": {
            "A": r["opt_a"],
            "B": r["opt_b"],
            "C": r["opt_c"],
            "D": r["opt_d"]
        },
        "answer": r["answer"]
    }

def add_question(exam_id, question_text, opt_a, opt_b, opt_c, opt_d, answer):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(q_num), 0) + 1 FROM questions WHERE exam_id = ?", (exam_id,))
    next_num = cursor.fetchone()[0]
    new_id = f"e{exam_id}_q{next_num}"

    # Ensure unique ID
    cursor.execute("SELECT COUNT(*) FROM questions WHERE id = ?", (new_id,))
    if cursor.fetchone()[0] > 0:
        cursor.execute("SELECT COUNT(*) FROM questions WHERE exam_id = ?", (exam_id,))
        total = cursor.fetchone()[0] + 1
        new_id = f"e{exam_id}_q{total}_{int(Path(__file__).stat().st_mtime)}"

    cursor.execute("""
        INSERT INTO questions (id, exam_id, q_num, question, opt_a, opt_b, opt_c, opt_d, answer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (new_id, int(exam_id), next_num, question_text.strip(), opt_a.strip(), opt_b.strip(), opt_c.strip(), opt_d.strip(), answer.strip().upper()))
    conn.commit()
    conn.close()
    if int(exam_id) == 1:
        sync_questions_to_json()
    return new_id

def update_question(q_id, question_text, opt_a, opt_b, opt_c, opt_d, answer):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE questions 
        SET question = ?, opt_a = ?, opt_b = ?, opt_c = ?, opt_d = ?, answer = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (question_text.strip(), opt_a.strip(), opt_b.strip(), opt_c.strip(), opt_d.strip(), answer.strip().upper(), q_id))
    cursor.execute("SELECT exam_id FROM questions WHERE id = ?", (q_id,))
    r = cursor.fetchone()
    exam_id = r[0] if r else 1
    conn.commit()
    conn.close()
    if exam_id == 1:
        sync_questions_to_json()

def delete_question(q_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT exam_id FROM questions WHERE id = ?", (q_id,))
    r = cursor.fetchone()
    exam_id = r[0] if r else 1

    cursor.execute("DELETE FROM questions WHERE id = ?", (q_id,))
    
    # Re-sequence remaining questions within that exam
    cursor.execute("SELECT id FROM questions WHERE exam_id = ? ORDER BY q_num ASC", (exam_id,))
    remaining_ids = [row["id"] for row in cursor.fetchall()]
    for idx, rem_id in enumerate(remaining_ids, 1):
        cursor.execute("UPDATE questions SET q_num = ? WHERE id = ?", (idx, rem_id))

    conn.commit()
    conn.close()
    if exam_id == 1:
        sync_questions_to_json()

def sync_questions_to_json():
    """Keeps questions.json in sync with Exam 1 for backwards-compatibility."""
    questions = get_questions_by_exam(1)
    try:
        with open(QUESTIONS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(questions, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Failed to sync questions.json:", e)

# ----------------- Student Attempt Management -----------------

def get_student_attempts(exam_id, first_name, last_name, reg_number=None):
    conn = get_db()
    cursor = conn.cursor()
    if reg_number and str(reg_number).strip():
        rows = cursor.execute("""
            SELECT * FROM submissions 
            WHERE exam_id = ?
              AND (is_archived = 0 OR is_archived IS NULL)
              AND (
                LOWER(TRIM(COALESCE(reg_number, ''))) = LOWER(TRIM(?))
                OR (LOWER(TRIM(first_name)) = LOWER(TRIM(?)) AND LOWER(TRIM(last_name)) = LOWER(TRIM(?)))
              )
            ORDER BY attempt_number ASC
        """, (exam_id, str(reg_number).strip(), first_name.strip(), last_name.strip())).fetchall()
    else:
        rows = cursor.execute("""
            SELECT * FROM submissions 
            WHERE exam_id = ?
              AND (is_archived = 0 OR is_archived IS NULL)
              AND LOWER(TRIM(first_name)) = LOWER(TRIM(?)) 
              AND LOWER(TRIM(last_name)) = LOWER(TRIM(?))
            ORDER BY attempt_number ASC
        """, (exam_id, first_name.strip(), last_name.strip())).fetchall()
    conn.close()
    return rows

def reset_student_retake(exam_id, reg_number=None, first_name=None, last_name=None):
    """
    Resets the retake ability for a student on a specific exam.
    Archives previous submissions so active attempt count resets to 0.
    """
    conn = get_db()
    cursor = conn.cursor()
    if reg_number and str(reg_number).strip():
        cursor.execute("""
            UPDATE submissions 
            SET is_archived = 1 
            WHERE exam_id = ? 
              AND (is_archived = 0 OR is_archived IS NULL)
              AND (
                LOWER(TRIM(COALESCE(reg_number, ''))) = LOWER(TRIM(?))
                OR (LOWER(TRIM(first_name)) = LOWER(TRIM(?)) AND LOWER(TRIM(last_name)) = LOWER(TRIM(?)))
              )
        """, (int(exam_id), str(reg_number).strip(), (first_name or "").strip(), (last_name or "").strip()))
        cursor.execute("UPDATE students SET is_active = 1 WHERE UPPER(TRIM(reg_number)) = ?", (str(reg_number).strip().upper(),))
    else:
        cursor.execute("""
            UPDATE submissions 
            SET is_archived = 1 
            WHERE exam_id = ? 
              AND (is_archived = 0 OR is_archived IS NULL)
              AND LOWER(TRIM(first_name)) = LOWER(TRIM(?)) 
              AND LOWER(TRIM(last_name)) = LOWER(TRIM(?))
        """, (int(exam_id), (first_name or "").strip(), (last_name or "").strip()))

    conn.commit()
    conn.close()
    return True, "Retake ability successfully reset. Candidate has 2 fresh attempts available."

def update_facilitator_password(facilitator_id, new_password):
    """Updates password for a facilitator or admin account."""
    if not new_password or len(new_password) < 6:
        return False, "Password must be at least 6 characters."
    pwd_hash = generate_password_hash(new_password)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE facilitators SET password_hash = ? WHERE id = ?", (pwd_hash, int(facilitator_id)))
    conn.commit()
    conn.close()
    return True, "Password updated successfully."

def save_submission(exam_id, first_name, last_name, attempt_num, score, total, percentage, passed, reg_number=None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO submissions (exam_id, first_name, last_name, reg_number, attempt_number, score, total, percentage, passed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (exam_id, first_name.strip(), last_name.strip(), (str(reg_number).strip().upper() if reg_number else None), attempt_num, score, total, percentage, 1 if passed else 0))
    conn.commit()
    conn.close()

# ----------------- Student Registration & Course Whitelist Management -----------------

def get_track_course_title(track_code):
    if not track_code:
        return ""
    code_clean = str(track_code).strip().upper()
    if code_clean == "DMSM":
        code_clean = "DM"
    m = {
        "AIGA": "Artificial Intelligence & Generative AI",
        "DL": "Digital Literacy",
        "GDCC": "Graphic Design & Content Creation",
        "CP": "Coding & Programming",
        "DM": "Digital Marketing & Social Media"
    }
    return m.get(code_clean, "")

def get_all_students(search=None, exam_id=None, allowed_exam_ids=None):
    """Returns all registered students along with their assigned courses/exams.
    If allowed_exam_ids is provided, restricts to students who have access to at least one of those exams.
    """
    conn = get_db()
    cursor = conn.cursor()

    if allowed_exam_ids is not None and len(allowed_exam_ids) == 0:
        conn.close()
        return []

    query = """
        SELECT s.*, 
               GROUP_CONCAT(e.title, ' || ') as enrolled_courses_titles,
               GROUP_CONCAT(e.id, ',') as enrolled_exam_ids
        FROM students s
        LEFT JOIN student_exam_access sea ON s.id = sea.student_id
        LEFT JOIN exams e ON sea.exam_id = e.id
    """
    params = []
    conditions = []

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        conditions.append("(LOWER(s.reg_number) LIKE ? OR LOWER(s.first_name) LIKE ? OR LOWER(s.last_name) LIKE ? OR LOWER(COALESCE(s.email, '')) LIKE ?)")
        params.extend([term, term, term, term])

    if exam_id:
        conditions.append("s.id IN (SELECT student_id FROM student_exam_access WHERE exam_id = ?)")
        params.append(int(exam_id))
    elif allowed_exam_ids is not None:
        placeholders = ",".join("?" for _ in allowed_exam_ids)
        conditions.append(f"s.id IN (SELECT student_id FROM student_exam_access WHERE exam_id IN ({placeholders}))")
        params.extend([int(x) for x in allowed_exam_ids])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " GROUP BY s.id ORDER BY s.id DESC"

    rows = cursor.execute(query, params).fetchall()
    conn.close()

    students = []
    for r in rows:
        d = dict(r)
        if not d.get("primary_track") and d.get("reg_number"):
            m = re.match(r'^(?:YMO-)?([A-Z]+)-\d+$', str(d["reg_number"]).upper().replace('/', '-'))
            trk = m.group(1) if m else ""
            if trk == "DMSM": trk = "DM"
            d["primary_track"] = trk
        d["enrolled_course_title"] = get_track_course_title(d.get("primary_track")) or "General Enrolment"
        d["assigned_exam_ids"] = [int(x) for x in d["enrolled_exam_ids"].split(",") if x] if d.get("enrolled_exam_ids") else []
        d["assigned_courses"] = [t for t in d["enrolled_courses_titles"].split(" || ") if t] if d.get("enrolled_courses_titles") else []
        students.append(d)
    return students

def _enrich_student_dict(d):
    if not d:
        return None
    if not d.get("primary_track") and d.get("reg_number"):
        m = re.match(r'^(?:YMO-)?([A-Z]+)-\d+$', str(d["reg_number"]).upper().replace('/', '-'))
        trk = m.group(1) if m else ""
        if trk == "DMSM": trk = "DM"
        d["primary_track"] = trk
    d["enrolled_course_title"] = get_track_course_title(d.get("primary_track")) or "General Enrolment"
    d["assigned_exams"] = get_student_assigned_exams(d["id"])
    d["assigned_exam_ids"] = [e["id"] for e in d["assigned_exams"]]
    return d

def get_student(student_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM students WHERE id = ?", (student_id,))
    r = cursor.fetchone()
    conn.close()
    if not r:
        return None
    return _enrich_student_dict(dict(r))

def get_student_by_reg(reg_number):
    if not reg_number:
        return None
    raw_clean = str(reg_number).strip().upper()
    conn = get_db()
    cursor = conn.cursor()

    # 1. Exact / direct match
    cursor.execute("SELECT * FROM students WHERE UPPER(TRIM(reg_number)) = ?", (raw_clean,))
    r = cursor.fetchone()
    if r:
        conn.close()
        return _enrich_student_dict(dict(r))

    # 2. Normalized match (e.g. YMO-DL-0001 -> YMO-DL-001)
    norm = normalize_reg_number(raw_clean)
    cursor.execute("SELECT * FROM students WHERE UPPER(TRIM(reg_number)) = ?", (norm,))
    r = cursor.fetchone()
    if r:
        conn.close()
        return _enrich_student_dict(dict(r))

    # 3. Flexible lookup keys matching (supports 001 vs 0001, DM vs DMSM, slash vs dash)
    lookup_keys = get_reg_lookup_keys(raw_clean)
    all_students = cursor.execute("SELECT * FROM students").fetchall()
    conn.close()
    for s in all_students:
        s_keys = get_reg_lookup_keys(s["reg_number"])
        if lookup_keys & s_keys:
            return _enrich_student_dict(dict(s))

    return None

def get_student_by_reg_and_lastname(reg_number, last_name):
    """
    Authenticates a student using their Student ID (Reg No) and Surname (Last Name as password).
    Uses flexible matching for Reg No and case-insensitive matching for Last Name.
    Also handles name order inversions (e.g. if names were swapped on registration).
    Returns student dict or None.
    """
    if not reg_number or not last_name:
        return None
    student = get_student_by_reg(reg_number)
    if not student:
        return None

    l_clean = last_name.strip().lower()
    s_last = (student.get("last_name") or "").strip().lower()
    s_first = (student.get("first_name") or "").strip().lower()

    # Direct match or inverted match
    if l_clean == s_last or l_clean == s_first:
        return student

    # Multi-part name / compound name match (e.g., 'Jimoh' in 'Jimoh Rasidat')
    words_in = set(re.findall(r'\w+', l_clean))
    words_rec = set(re.findall(r'\w+', f"{s_last} {s_first}"))
    if words_in and words_in.issubset(words_rec):
        return student

    return None


def get_student_by_name(first_name, last_name):
    """Finds a student by First Name and Last Name (supports case-insensitivity and swapped names)."""
    if not first_name or not last_name:
        return None
    f_clean = first_name.strip()
    l_clean = last_name.strip()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM students 
        WHERE (LOWER(TRIM(first_name)) = LOWER(TRIM(?)) AND LOWER(TRIM(last_name)) = LOWER(TRIM(?)))
           OR (LOWER(TRIM(first_name)) = LOWER(TRIM(?)) AND LOWER(TRIM(last_name)) = LOWER(TRIM(?)))
        ORDER BY id DESC
    """, (f_clean, l_clean, l_clean, f_clean))
    r = cursor.fetchone()
    conn.close()
    if not r:
        return None
    return _enrich_student_dict(dict(r))

def get_student_assigned_exams(student_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT reg_number, primary_track FROM students WHERE id = ?", (student_id,))
    s_row = cursor.fetchone()
    reg = s_row[0] if s_row else ""
    primary_trk = s_row[1] if (s_row and len(s_row) > 1 and s_row[1]) else ""

    m = re.match(r'^(?:YMO-)?([A-Z]+)-\d+$', reg.upper().replace('/', '-'))
    track_code = primary_trk or (m.group(1) if m else "")
    if track_code == "DMSM":
        track_code = "DM"

    if track_code:
        rows = cursor.execute("""
            SELECT DISTINCT e.id, e.title, e.cohort, e.course_code, e.duration_minutes, e.passing_cutoff,
                   COALESCE(q_counts.q_count, 0) as question_count
            FROM exams e
            LEFT JOIN (
                SELECT exam_id, COUNT(*) as q_count FROM questions GROUP BY exam_id
            ) q_counts ON e.id = q_counts.exam_id
            LEFT JOIN student_exam_access sea ON e.id = sea.exam_id AND sea.student_id = ?
            WHERE (sea.student_id IS NOT NULL OR (e.is_active = 1 AND (UPPER(e.course_code) = ? OR e.title LIKE ?)))
            ORDER BY e.id ASC
        """, (student_id, track_code, f"%{track_code}%")).fetchall()
    else:
        rows = cursor.execute("""
            SELECT DISTINCT e.id, e.title, e.cohort, e.course_code, e.duration_minutes, e.passing_cutoff,
                   COALESCE(q_counts.q_count, 0) as question_count
            FROM exams e
            LEFT JOIN (
                SELECT exam_id, COUNT(*) as q_count FROM questions GROUP BY exam_id
            ) q_counts ON e.id = q_counts.exam_id
            JOIN student_exam_access sea ON e.id = sea.exam_id
            WHERE sea.student_id = ?
            ORDER BY e.id ASC
        """, (student_id,)).fetchall()

    conn.close()
    return [dict(r) for r in rows]

def add_student(reg_number, first_name, last_name, email="", phone="", exam_ids=None, passport_url=""):
    reg_number = (reg_number or "").strip().upper()
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip()
    passport_url = (passport_url or "").strip()

    if not reg_number or not first_name or not last_name:
        return False, "Registration Number, First Name, and Last Name are required.", None

    conn = get_db()
    cursor = conn.cursor()
    try:
        m = re.match(r'^(?:YMO-)?([A-Z]+)-\d+$', reg_number.upper().replace('/', '-'))
        c_code = m.group(1) if m else ""
        if c_code == "DMSM":
            c_code = "DM"
        cursor.execute("""
            INSERT INTO students (reg_number, first_name, last_name, email, phone, passport_url, primary_track)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (reg_number, first_name, last_name, email, phone, passport_url or None, c_code))
        student_id = cursor.lastrowid

        if exam_ids:
            for eid in exam_ids:
                try:
                    cursor.execute("INSERT OR IGNORE INTO student_exam_access (student_id, exam_id) VALUES (?, ?)", (student_id, int(eid)))
                except Exception:
                    pass

        conn.commit()
        return True, f"Student '{reg_number}' ({first_name} {last_name}) enrolled successfully.", student_id
    except sqlite3.IntegrityError:
        return False, f"A student with Registration Number '{reg_number}' already exists in the system.", None
    finally:
        conn.close()

def update_student(student_id, reg_number, first_name, last_name, email="", phone="", exam_ids=None, is_active=1, passport_url=None):
    reg_number = (reg_number or "").strip().upper()
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip()

    if not reg_number or not first_name or not last_name:
        return False, "Registration Number, First Name, and Last Name are required."

    conn = get_db()
    cursor = conn.cursor()
    try:
        if passport_url is not None:
            cursor.execute("""
                UPDATE students 
                SET reg_number = ?, first_name = ?, last_name = ?, email = ?, phone = ?, is_active = ?, passport_url = ?
                WHERE id = ?
            """, (reg_number, first_name, last_name, email, phone, int(is_active), passport_url or None, student_id))
        else:
            cursor.execute("""
                UPDATE students 
                SET reg_number = ?, first_name = ?, last_name = ?, email = ?, phone = ?, is_active = ?
                WHERE id = ?
            """, (reg_number, first_name, last_name, email, phone, int(is_active), student_id))


        if exam_ids is not None:
            cursor.execute("DELETE FROM student_exam_access WHERE student_id = ?", (student_id,))
            for eid in exam_ids:
                cursor.execute("INSERT OR IGNORE INTO student_exam_access (student_id, exam_id) VALUES (?, ?)", (student_id, int(eid)))

        conn.commit()
        return True, "Student record updated successfully."
    except sqlite3.IntegrityError:
        return False, f"Registration Number '{reg_number}' is already assigned to another student."
    finally:
        conn.close()

def delete_student(student_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM student_exam_access WHERE student_id = ?", (student_id,))
    cursor.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    conn.close()
    return True

def verify_student_access(first_name, last_name, exam_id, reg_number=None):
    """
    Verifies if a candidate is permitted to sit for a specific exam.
    Strict Verification:
    1. Registration Number is mandatory.
    2. Candidate First Name and Last Name must match the registered student record for that Reg Number.
    3. Student account must be active.
    4. Student must be enrolled/whitelisted in the selected course track (exam_id).
    Returns: (is_allowed: bool, reason_message: str, student_dict: dict or None)
    """
    settings = get_settings()
    if settings.get("require_registration", "true").lower() == "false":
        return True, "Open access mode active", None

    reg_number = (reg_number or "").strip().upper()
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()

    if not reg_number:
        return False, "Registration Number is required. Please enter your official Student ID / Registration Number to continue.", None

    if not first_name or not last_name:
        return False, "Candidate First Name and Last Name are required. Please enter both names as registered on your course list.", None

    # Step 1: Look up student by Registration Number
    student = get_student_by_reg(reg_number)
    if not student:
        return False, f"Access Denied: Registration Number '{reg_number}' was not found in the official hub student database. Please check your Registration Number or contact your facilitator.", None

    # Step 2: Verify candidate name matches the student registered under this Registration Number
    fn_in = first_name.lower().strip()
    ln_in = last_name.lower().strip()
    fn_rec = student['first_name'].lower().strip()
    ln_rec = student['last_name'].lower().strip()

    name_matched = False
    # Exact match or swapped order (e.g. surname entered in first name field)
    if (fn_in == fn_rec and ln_in == ln_rec) or (fn_in == ln_rec and ln_in == fn_rec):
        name_matched = True
    else:
        # Flexible match for middle names / compound names
        words_in = set(re.findall(r'\w+', f"{fn_in} {ln_in}"))
        words_rec = set(re.findall(r'\w+', f"{fn_rec} {ln_rec}"))
        if (fn_in in words_rec or any(w in words_rec for w in re.findall(r'\w+', fn_in))) and \
           (ln_in in words_rec or any(w in words_rec for w in re.findall(r'\w+', ln_in))):
            name_matched = True
        elif len(words_in & words_rec) >= 2:
            name_matched = True

    if not name_matched:
        return False, f"Identity Mismatch: Candidate name '{first_name} {last_name}' does not match the official student record on file for '{reg_number}' (Registered as: '{student['first_name']} {student['last_name']}'). Please check the spelling of your name or contact your facilitator.", None

    # Step 3: Check active status
    if student.get("is_active", 1) == 0:
        return False, f"Access Denied: Account for '{student['first_name']} {student['last_name']}' ({reg_number}) is currently inactive. Please contact hub administration.", student

    # Step 4: Check course track assignment
    assigned_exams = student.get("assigned_exams", [])
    assigned_exam_ids = [e["id"] for e in assigned_exams]

    target_exam = get_exam(exam_id)
    target_exam_title = target_exam["title"] if target_exam else f"Assessment #{exam_id}"

    if exam_id not in assigned_exam_ids:
        if assigned_exams:
            enrolled_titles = " • ".join([f"'{e['title']}'" for e in assigned_exams])
            return False, f"Course Restriction: Candidate '{student['first_name']} {student['last_name']}' ({student['reg_number']}) is enrolled under {enrolled_titles}, NOT '{target_exam_title}'. Please select your registered course track from the dropdown.", student
        else:
            return False, f"Course Restriction: Candidate '{student['first_name']} {student['last_name']}' ({student['reg_number']}) has not been assigned to any active assessment tracks. Please contact your facilitator.", student

    return True, "Verification successful.", student

def match_exam_by_title(course_name, all_exams):
    if not course_name:
        return None
    name_clean = course_name.lower().strip()

    # 1. Check official canonical course patterns
    target_canonical_title = None
    for c in OFFICIAL_COURSES:
        for p in c["patterns"]:
            if p in name_clean:
                target_canonical_title = c["title"]
                break
        if target_canonical_title:
            break

    if target_canonical_title:
        for e in all_exams:
            if e["title"].lower().strip() == target_canonical_title.lower().strip():
                return e

    # 2. Exact match fallback
    for e in all_exams:
        if e["title"].lower().strip() == name_clean:
            return e

    return None

def discover_google_sheet_tabs(sheet_key):
    """
    Fetches the Google Sheet webpage and extracts all available tabs with their title and gid.
    Returns a list of dicts: [{'title': '...', 'gid': '...'}, ...]
    """
    html_url = f"https://docs.google.com/spreadsheets/d/{sheet_key}/edit"
    req = urllib.request.Request(
        html_url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except Exception:
        return []

    tabs = []
    seen = set()
    pattern = r'\[[0-9]+,[0-9]+,\\\"([0-9]+)\\\",\[\{\\\"1\\\":\[\[0,0,\\\"([^\\\"]+)\\\"'
    for gid, title in re.findall(pattern, html):
        if gid not in seen:
            seen.add(gid)
            tabs.append({'title': title.strip(), 'gid': gid})

    if not tabs:
        pattern2 = r'\[[0-9]+,[0-9]+,\"([0-9]+)\",\[\{\"1\":\[\[0,0,\"([^\"]+)\"'
        for gid, title in re.findall(pattern2, html):
            if gid not in seen:
                seen.add(gid)
                tabs.append({'title': title.strip(), 'gid': gid})

    return tabs


def fetch_google_sheet_csv(raw_input, selected_gid=None):
    """
    Fetches raw CSV text from Google Sheets export if raw_input looks like a URL or ID.
    If no gid is specified, automatically discovers workbook tabs and prioritizes roster/candidate tabs.
    Returns (ok: bool, text_or_err: str, meta: dict).
    """
    clean = raw_input.strip()
    if not clean:
        return False, "No Google Sheet data or URL provided.", {}

    is_url = ("spreadsheets/d/" in clean) or ("docs.google.com" in clean) or clean.startswith("http://") or clean.startswith("https://")
    if is_url:
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", clean)
        if not match:
            match = re.search(r"id=([a-zA-Z0-9-_]+)", clean)
        if not match:
            return False, "Invalid Google Sheets URL. Please copy the complete spreadsheet link from your browser or Share dialog.", {}

        sheet_key = match.group(1)

        gid_val = selected_gid
        if not gid_val:
            gid_match = re.search(r"[#&?]gid=([0-9]+)", clean)
            if gid_match:
                gid_val = gid_match.group(1)

        tabs = []
        selected_tab_title = ""
        # If no gid was provided in URL, discover tabs in the workbook
        if not gid_val:
            tabs = discover_google_sheet_tabs(sheet_key)
            if tabs:
                best_tab = None
                for t in tabs:
                    title_l = t['title'].lower()
                    if any(k in title_l for k in ("course roster", "roster", "course", "candidate")):
                        best_tab = t
                        break
                if not best_tab:
                    for t in tabs:
                        title_l = t['title'].lower()
                        if any(k in title_l for k in ("registration", "response", "student", "bio data")):
                            best_tab = t
                            break
                if best_tab:
                    gid_val = best_tab['gid']
                    selected_tab_title = best_tab['title']
        else:
            tabs = discover_google_sheet_tabs(sheet_key)
            for t in tabs:
                if t['gid'] == gid_val:
                    selected_tab_title = t['title']
                    break

        gid_param = f"&gid={gid_val}" if gid_val else ""
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_key}/export?format=csv{gid_param}"

        try:
            req = urllib.request.Request(
                csv_url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                }
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_bytes = resp.read()
                raw_text = content_bytes.decode('utf-8-sig', errors='replace')
        except Exception as e:
            return False, f"Could not connect to Google Sheets ({str(e)}). Please ensure Google Sheet sharing is set to 'Anyone with the link can view'.", {}

        # Detect private/restricted Google Sheet redirecting to Google Login HTML
        lower_head = raw_text[:2000].lower()
        if "<html" in lower_head or "accounts.google.com" in lower_head or "servicelogin" in lower_head:
            return False, (
                "Google Sheets returned an access login screen because the spreadsheet is currently private or restricted. "
                "To fix this in Google Sheets: Click 'Share' (top right) -> Under 'General access', change from 'Restricted' to 'Anyone with the link' (Viewer) -> Click 'Copy link' and try again."
            ), {}

        meta = {
            "sheet_key": sheet_key,
            "gid": gid_val or "0",
            "selected_tab_title": selected_tab_title or "Default Tab",
            "available_tabs": tabs
        }
        return True, raw_text, meta

    return True, clean, {}


def parse_google_sheet_records(raw_text):
    """
    Parses spreadsheet text (CSV/TSV) and extracts candidate records grouped by course.
    Supports:
    1. Multi-Course Column-Grouped Roster (e.g. Course Rosters tab:
       Row 4 = Courses title merging across columns,
       Row 6 = Subheadings like Reg No, First Name, Last Name, Passport URL,
       Row 7+ = Student records strictly organized under their respective course header)
    2. Flat master tables (First Name, Last Name, Course/Slot, Reg No, Passport photograph, Email, Phone)
    3. Plain text line lists
    Returns (ok: bool, msg: str, records: list)
    """
    if not raw_text or not raw_text.strip():
        return False, "Spreadsheet data is empty.", []

    delim = "\t" if "\t" in raw_text else (";" if ";" in raw_text else ",")
    try:
        reader = list(csv.reader(io.StringIO(raw_text), delimiter=delim))
    except Exception as e:
        return False, f"Spreadsheet format error: {str(e)}", []

    cleaned_rows = [r for r in reader if any(c.strip() for c in r)]
    if not cleaned_rows:
        return False, "Spreadsheet has no rows with data.", []

    # =========================================================================
    # Strategy 1: Multi-Course Column-Grouped Roster (Course Titles on Row 4, Subheadings on Row 6)
    # =========================================================================
    course_row_idx = -1
    sub_row_idx = -1

    for r_idx in range(min(10, len(cleaned_rows))):
        row = [c.strip() for c in cleaned_rows[r_idx]]
        matched_courses_in_row = []
        for col_idx, cell in enumerate(row):
            if cell and len(cell) >= 3:
                for cand in OFFICIAL_COURSES:
                    if any(p in cell.lower() for p in cand["patterns"]):
                        matched_courses_in_row.append((col_idx, cand["title"]))
                        break
        # If at least 2 official course tracks are found in this row, it's the Course Title Header row!
        if len(matched_courses_in_row) >= 2:
            course_row_idx = r_idx
            # Look for subheadings row underneath (usually within next 1-4 rows)
            for next_idx in range(r_idx + 1, min(r_idx + 5, len(cleaned_rows))):
                sub_row = [c.lower() for c in cleaned_rows[next_idx]]
                if any("first" in c or "name" in c or "reg" in c for c in sub_row):
                    sub_row_idx = next_idx
                    break
            break

    if course_row_idx != -1 and sub_row_idx != -1:
        row_course = [c.strip() for c in cleaned_rows[course_row_idx]]
        row_sub = [c.lower().strip() for c in cleaned_rows[sub_row_idx]]
        max_cols = max(len(r) for r in cleaned_rows)

        # Detect each official course and its start column
        course_blocks = []
        for col_idx, cell in enumerate(row_course):
            if not cell:
                continue
            matched_canonical = None
            for cand in OFFICIAL_COURSES:
                if any(p in cell.lower() for p in cand["patterns"]):
                    matched_canonical = cand["title"]
                    break
            if matched_canonical:
                course_blocks.append((col_idx, matched_canonical))

        # Sort strictly by column order
        course_blocks.sort(key=lambda x: x[0])

        records = []
        for b_idx, (start_col, c_title) in enumerate(course_blocks):
            end_col = course_blocks[b_idx + 1][0] if b_idx + 1 < len(course_blocks) else max_cols

            # Locate columns strictly inside this course's column range
            reg_col, fn_col, ln_col, passport_col = None, None, None, None
            for col in range(start_col, min(end_col, len(row_sub))):
                sub = row_sub[col]
                if any(k in sub for k in ("reg", "matric", "id", "number", "no")):
                    if reg_col is None: reg_col = col
                elif "first" in sub:
                    if fn_col is None: fn_col = col
                elif any(k in sub for k in ("last", "surname", "sur")):
                    if ln_col is None: ln_col = col
                elif any(k in sub for k in ("passport", "photo", "image", "url", "pic")):
                    if passport_col is None: passport_col = col

            # Fallback if subheading text was slightly atypical
            if fn_col is None or ln_col is None:
                block_cols = [c for c in range(start_col, min(end_col, len(row_sub))) if row_sub[c] and not any(k in row_sub[c] for k in ("total", "sn", "s/n"))]
                if len(block_cols) >= 3:
                    reg_col = block_cols[0]
                    fn_col = block_cols[1]
                    ln_col = block_cols[2]
                    if len(block_cols) >= 4:
                        passport_col = block_cols[3]

            # Read all student rows starting from row immediately after subheadings
            data_start_idx = sub_row_idx + 1
            for r_idx in range(data_start_idx, len(cleaned_rows)):
                row = cleaned_rows[r_idx]
                f_name = row[fn_col].strip() if (fn_col is not None and fn_col < len(row)) else ""
                l_name = row[ln_col].strip() if (ln_col is not None and ln_col < len(row)) else ""
                reg_val = row[reg_col].strip() if (reg_col is not None and reg_col < len(row)) else ""
                passport_val = row[passport_col].strip() if (passport_col is not None and passport_col < len(row)) else ""

                # Ignore blank rows or header repetitions
                if not f_name or not l_name:
                    continue
                if any(k in f_name.lower() for k in ("first name", "name", "total", "enrolled")):
                    continue
                # Validate and reconcile course using official course code prefix if present in Reg No
                final_course = c_title
                if reg_val:
                    m_code = re.search(r'YMO-([A-Z]+)-', reg_val.upper())
                    if m_code:
                        raw_code = m_code.group(1)
                        if raw_code == "DMSM":
                            raw_code = "DM"
                        for oc in OFFICIAL_COURSES:
                            if oc["code"] == raw_code:
                                final_course = oc["title"]
                                break

                records.append({
                    "course": final_course,
                    "reg_number": reg_val.upper() if reg_val else "",
                    "first_name": f_name.title(),
                    "last_name": l_name.title(),
                    "passport_url": format_passport_image_url(passport_val),
                    "email": "",
                    "phone": ""
                })

        if records:
            return True, f"Found {len(records)} student(s) organized under {len(course_blocks)} course track(s).", records

    # =========================================================================
    # Strategy 2: Flat Master Table (e.g. Registration Response Sheet)
    # =========================================================================
    flat_header_idx = -1
    for r_idx in range(min(8, len(cleaned_rows))):
        row_lower = [c.lower().strip() for c in cleaned_rows[r_idx]]
        has_name = any("name" in c or "first" in c for c in row_lower)
        has_course = any(("course" in c or "preferred slot" in c or "track" in c or "assessment" in c) and "hear" not in c and "total" not in c for c in row_lower)
        if has_name and has_course:
            flat_header_idx = r_idx
            break

    if flat_header_idx != -1:
        header_row = [c.lower().strip() for c in cleaned_rows[flat_header_idx]]
        col_reg, col_fn, col_ln, col_course, col_email, col_phone, col_passport = -1, -1, -1, -1, -1, -1, -1
        for idx, col_name in enumerate(header_row):
            if any(k in col_name for k in ("reg no", "reg number", "reg_no", "registration", "matric", "student id")) and col_reg == -1:
                col_reg = idx
            elif "first" in col_name and col_fn == -1:
                col_fn = idx
            elif any(k in col_name for k in ("last", "surname", "sur")) and col_ln == -1:
                col_ln = idx
            elif "email" in col_name and col_email == -1:
                col_email = idx
            elif "phone" in col_name and col_phone == -1:
                col_phone = idx
            elif any(k in col_name for k in ("course", "preferred slot", "track", "assessment")) and "hear" not in col_name and "total" not in col_name and col_course == -1:
                col_course = idx
            elif any(k in col_name for k in ("passport", "photo", "image", "photograph", "picture")) and col_passport == -1:
                col_passport = idx

        records = []
        for r_idx in range(flat_header_idx + 1, len(cleaned_rows)):
            row = cleaned_rows[r_idx]
            course_val = row[col_course].strip() if col_course >= 0 and col_course < len(row) else ""
            if not course_val:
                continue
            f_name = row[col_fn].strip().title() if col_fn >= 0 and col_fn < len(row) else ""
            l_name = row[col_ln].strip().title() if col_ln >= 0 and col_ln < len(row) else ""
            reg_val = row[col_reg].strip().upper() if col_reg >= 0 and col_reg < len(row) else ""
            email = row[col_email].strip() if col_email >= 0 and col_email < len(row) else ""
            phone = row[col_phone].strip() if col_phone >= 0 and col_phone < len(row) else ""
            passport_val = row[col_passport].strip() if col_passport >= 0 and col_passport < len(row) else ""

            if f_name and l_name:
                records.append({
                    "course": course_val,
                    "reg_number": reg_val,
                    "first_name": f_name,
                    "last_name": l_name,
                    "passport_url": format_passport_image_url(passport_val),
                    "email": email,
                    "phone": phone
                })
        if records:
            return True, f"Found {len(records)} student(s).", records

    # =========================================================================
    # Strategy 3: Standard Fallback
    # =========================================================================
    records = []
    for line_idx, row in enumerate(cleaned_rows):
        parts = [c.strip() for c in row if c.strip()]
        if not parts:
            continue
        if parts[0].lower() in ("reg_number", "reg no", "registration", "id", "student id", "matric", "first name", "last name", "name"):
            continue

        f_name, l_name, auto_reg, email, phone = "", "", "", "", ""
        if len(parts) == 2:
            f_name = parts[0].title()
            l_name = parts[1].title()
        elif len(parts) >= 3:
            if "/" in parts[0] or "-" in parts[0] or any(c.isdigit() for c in parts[0]):
                auto_reg = parts[0].upper()
                f_name = parts[1].title()
                l_name = parts[2].title()
                email = parts[3] if len(parts) > 3 else ""
                phone = parts[4] if len(parts) > 4 else ""
            else:
                f_name = parts[0].title()
                l_name = parts[1].title()
                email = parts[2] if len(parts) > 2 else ""
                phone = parts[3] if len(parts) > 3 else ""

        if f_name and l_name:
            records.append({
                "course": "General Enrolment",
                "reg_number": auto_reg,
                "first_name": f_name,
                "last_name": l_name,
                "passport_url": "",
                "email": email,
                "phone": phone
            })

    if not records:
        return False, "Could not detect student names or courses in the spreadsheet.", []

    return True, f"Found {len(records)} student(s).", records



def preview_google_sheet_students(raw_text_or_url, selected_gid=None):
    """
    Simulates Google Sheet import without altering database records.
    Returns dict with courses, counts, samples, selected tab, and available tabs.
    """
    ok, text_or_err, meta = fetch_google_sheet_csv(raw_text_or_url, selected_gid=selected_gid)
    if not ok:
        return {"ok": False, "message": text_or_err}

    ok, msg, records = parse_google_sheet_records(text_or_err)
    if not ok:
        return {"ok": False, "message": msg}

    all_exams = get_all_exams()
    grouped = {}
    for r in records:
        c_title = r["course"]
        matched_canonical = match_exam_by_title(c_title, all_exams)
        display_title = matched_canonical["title"] if matched_canonical else c_title
        if display_title not in grouped:
            grouped[display_title] = []
        reg_clean = normalize_reg_number(r.get("reg_number"))
        reg_str = f" ({reg_clean})" if reg_clean else ""
        grouped[display_title].append(f"{r['first_name']} {r['last_name']}{reg_str}")

    courses_list = []
    for c_title, names in grouped.items():
        courses_list.append({
            "title": c_title,
            "count": len(names),
            "samples": names[:3]
        })

    selected_title = meta.get("selected_tab_title", "Active Sheet")
    tabs = meta.get("available_tabs", [])

    return {
        "ok": True,
        "message": f"Successfully read {len(records)} student(s) across {len(courses_list)} course track(s) from '{selected_title}'.",
        "courses": courses_list,
        "total_students": len(records),
        "selected_tab": selected_title,
        "selected_gid": meta.get("gid", ""),
        "available_tabs": tabs
    }


def import_google_sheet_students(raw_text_or_url, selected_gid=None):
    """
    Imports students arranged under course headers from Google Sheets or pasted cells.
    Saves student whitelist and maps them to their respective courses in SQLite.
    Strictly maps to the 5 official courses. Unassigned slots do not create new exams.
    """
    ok, text_or_err, meta = fetch_google_sheet_csv(raw_text_or_url, selected_gid=selected_gid)
    if not ok:
        return 0, 0, text_or_err

    ok, msg, records = parse_google_sheet_records(text_or_err)
    if not ok:
        return 0, 0, msg

    ensure_canonical_courses()
    all_exams = get_all_exams()

    success_count = 0
    updated_count = 0
    imported_students_per_course = {}
    unassigned_count = 0

    for rec in records:
        course_name = rec["course"]
        matched = match_exam_by_title(course_name, all_exams)
        eid = matched["id"] if matched else None
        exam_ids_to_assign = [eid] if eid else []

        raw_reg = rec.get("reg_number", "").strip()
        reg_val = normalize_reg_number(raw_reg)
        f_name = rec["first_name"].strip().title()
        l_name = rec["last_name"].strip().title()
        email = rec.get("email", "").strip()
        phone = rec.get("phone", "").strip()

        if not f_name or not l_name:
            continue

        existing = None
        if reg_val:
            existing = get_student_by_reg(reg_val)
        if not existing and raw_reg:
            existing = get_student_by_reg(raw_reg)
        # Only fallback to name match if neither reg_val nor raw_reg was present in the sheet
        if not existing and not reg_val and not raw_reg:
            existing = get_student_by_name(f_name, l_name)

        passport_val = rec.get("passport_url", "").strip()

        if existing:
            # When syncing from a course-roster sheet, assign strictly to this course track to eliminate bleeding
            target_exams = exam_ids_to_assign if exam_ids_to_assign else existing["assigned_exam_ids"]
            final_reg = reg_val if reg_val else existing["reg_number"]
            update_student(
                existing["id"],
                final_reg,
                f_name,
                l_name,
                email or existing.get("email", ""),
                phone or existing.get("phone", ""),
                exam_ids=target_exams,
                passport_url=passport_val or existing.get("passport_url", "")
            )
            updated_count += 1
        else:
            if not reg_val:
                if matched:
                    abbr = "".join([w[0] for w in matched["title"].split() if w[0].isalnum()])[:3].upper()
                    reg_val = f"YMO-{abbr}-{100 + success_count + 1:03d}"
                else:
                    reg_val = f"YMO-STU-{100 + success_count + 1:03d}"
            ok_add, add_msg, sid = add_student(
                reg_val, 
                f_name, 
                l_name, 
                email=email, 
                phone=phone, 
                exam_ids=exam_ids_to_assign,
                passport_url=passport_val
            )
            if ok_add:
                success_count += 1

        if eid:
            imported_students_per_course[eid] = imported_students_per_course.get(eid, 0) + 1
        else:
            unassigned_count += 1

    summary_parts = []
    for eid, cnt in imported_students_per_course.items():
        ex = get_exam(eid)
        if ex:
            summary_parts.append(f"{ex['title']}: {cnt} student(s)")
    if unassigned_count:
        summary_parts.append(f"Unassigned: {unassigned_count} student(s)")

    selected_title = meta.get("selected_tab_title", "")
    tab_suffix = f" from '{selected_title}'" if selected_title else ""
    msg = f"Successfully imported {success_count} new student(s){tab_suffix}"
    if updated_count:
        msg += f" (and updated {updated_count} existing)"
    if summary_parts:
        msg += f" across courses: {', '.join(summary_parts)}."

    return success_count + updated_count, 0, msg

def bulk_import_students(raw_text, default_exam_ids=None):
    """
    Parses comma-, tab-, or semicolon-separated lines of students:
    Format per line:
    REG_NUMBER, FIRST_NAME, LAST_NAME [, EMAIL] [, PHONE]
    or
    FIRST_NAME, LAST_NAME [, EMAIL]
    """
    if not raw_text or not raw_text.strip():
        return 0, 0, "No data provided to import."

    lines = raw_text.strip().splitlines()
    success_count = 0
    skipped_count = 0
    errors = []

    for line_idx, line in enumerate(lines, 1):
        clean_line = line.strip()
        if not clean_line or clean_line.startswith("#"):
            continue

        if "\t" in clean_line:
            parts = [p.strip() for p in clean_line.split("\t")]
        elif ";" in clean_line:
            parts = [p.strip() for p in clean_line.split(";")]
        else:
            parts = [p.strip() for p in clean_line.split(",")]

        if parts[0].lower() in ("reg_number", "reg no", "registration", "id", "student id", "matric"):
            continue

        if len(parts) == 2:
            f_name = parts[0].title()
            l_name = parts[1].title()
            auto_reg = f"YMO-STU-{100 + line_idx:03d}"
            email, phone = "", ""
        elif len(parts) >= 3:
            if "/" in parts[0] or "-" in parts[0] or any(c.isdigit() for c in parts[0]):
                auto_reg = normalize_reg_number(parts[0])
                f_name = parts[1].title()
                l_name = parts[2].title()
                email = parts[3] if len(parts) > 3 else ""
                phone = parts[4] if len(parts) > 4 else ""
            else:
                f_name = parts[0].title()
                l_name = parts[1].title()
                email = parts[2] if len(parts) > 2 else ""
                phone = parts[3] if len(parts) > 3 else ""
                auto_reg = f"YMO-STU-{100 + line_idx:03d}"
        else:
            skipped_count += 1
            continue

        existing = get_student_by_name(f_name, l_name) or get_student_by_reg(auto_reg)
        if existing:
            merged_exams = list(set(existing["assigned_exam_ids"] + (default_exam_ids or [])))
            update_student(existing["id"], existing["reg_number"], f_name, l_name, email or existing.get("email", ""), phone or existing.get("phone", ""), exam_ids=merged_exams)
            success_count += 1
        else:
            ok, msg, sid = add_student(auto_reg, f_name, l_name, email=email, phone=phone, exam_ids=default_exam_ids)
            if ok:
                success_count += 1
            else:
                skipped_count += 1

    summary = f"Imported/Updated {success_count} student(s)."
    if skipped_count:
        summary += f" Skipped {skipped_count} row(s)."
    return success_count, skipped_count, summary

def get_all_submissions(exam_id=None, allowed_exam_ids=None, course_code=None, include_archived=True):
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT s.*, e.title as exam_title, e.course_code as exam_course_code,
               COALESCE(st.primary_track, '') as student_primary_track
        FROM submissions s
        LEFT JOIN exams e ON s.exam_id = e.id
        LEFT JOIN students st ON UPPER(TRIM(COALESCE(s.reg_number, ''))) = UPPER(TRIM(st.reg_number))
    """
    params = []
    conditions = []
    if not include_archived:
        conditions.append("(s.is_archived = 0 OR s.is_archived IS NULL)")
    if exam_id:
        conditions.append("s.exam_id = ?")
        params.append(int(exam_id))
    elif course_code:
        conditions.append("(UPPER(e.course_code) = ? OR e.title LIKE ?)")
        params.extend([course_code.upper(), f"%{course_code.upper()}%"])
    elif allowed_exam_ids is not None:
        if len(allowed_exam_ids) == 0:
            conn.close()
            return []
        placeholders = ",".join("?" for _ in allowed_exam_ids)
        conditions.append(f"s.exam_id IN ({placeholders})")
        params.extend([int(x) for x in allowed_exam_ids])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY s.submitted_at DESC"
    rows = cursor.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
