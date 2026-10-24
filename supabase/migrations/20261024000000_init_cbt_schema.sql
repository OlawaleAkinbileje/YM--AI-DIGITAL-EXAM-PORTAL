-- YMO CBT Portal - Supabase PostgreSQL Schema Migration
-- Matches the canonical assessment schema with complete relational integrity.

-- 1. Assessment Settings (Key-Value)
CREATE TABLE IF NOT EXISTS assessment_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 2. Facilitators & Staff Accounts
CREATE TABLE IF NOT EXISTS facilitators (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'facilitator',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Exams & Course Tracks
CREATE TABLE IF NOT EXISTS exams (
    id BIGINT PRIMARY KEY,
    title TEXT NOT NULL,
    cohort TEXT,
    instructions TEXT,
    duration_minutes INTEGER DEFAULT 60,
    passing_cutoff NUMERIC(5,2) DEFAULT 85.00,
    is_active INTEGER DEFAULT 1,
    course_code TEXT,
    course_id INTEGER,
    created_by BIGINT REFERENCES facilitators(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Facilitator Course Assignments
CREATE TABLE IF NOT EXISTS facilitator_courses (
    id BIGSERIAL PRIMARY KEY,
    facilitator_id BIGINT NOT NULL REFERENCES facilitators(id) ON DELETE CASCADE,
    exam_id BIGINT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_facilitator_exam UNIQUE (facilitator_id, exam_id)
);

-- 5. Question Bank
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    exam_id BIGINT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    q_num INTEGER,
    question TEXT NOT NULL,
    opt_a TEXT NOT NULL,
    opt_b TEXT NOT NULL,
    opt_c TEXT NOT NULL,
    opt_d TEXT NOT NULL,
    answer VARCHAR(5) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Candidates / Students Roster
CREATE TABLE IF NOT EXISTS students (
    id BIGSERIAL PRIMARY KEY,
    reg_number TEXT UNIQUE NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    is_active INTEGER DEFAULT 1,
    passport_url TEXT,
    primary_track TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Student Exam Access Permissions (Whitelisting & Cross-Track Overrides)
CREATE TABLE IF NOT EXISTS student_exam_access (
    id BIGSERIAL PRIMARY KEY,
    student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    exam_id BIGINT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_student_exam UNIQUE (student_id, exam_id)
);

-- 8. Candidate Submissions & Results
CREATE TABLE IF NOT EXISTS submissions (
    id BIGSERIAL PRIMARY KEY,
    exam_id BIGINT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    attempt_number INTEGER DEFAULT 1,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    percentage NUMERIC(5,2) NOT NULL,
    passed INTEGER NOT NULL,
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    reg_number TEXT,
    is_archived INTEGER DEFAULT 0
);

-- Indexes for lightning fast queries
CREATE INDEX IF NOT EXISTS idx_questions_exam_id ON questions(exam_id);
CREATE INDEX IF NOT EXISTS idx_students_reg_number ON students(reg_number);
CREATE INDEX IF NOT EXISTS idx_submissions_exam_reg ON submissions(exam_id, reg_number);
CREATE INDEX IF NOT EXISTS idx_student_exam_access ON student_exam_access(student_id, exam_id);

