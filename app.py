import io
import csv
import json
import os
import re
import urllib.request
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, Response, jsonify, send_file, abort

import db
from grader import grade_submission
from ai_assistant import generate_questions_with_ai, smart_parse_raw_text

app = Flask(__name__)
app.secret_key = "ymo_cbt_secret_key_session"
MAX_ATTEMPTS = 2

# Initialize SQLite database with multi-exam schema
db.init_db()
db.ensure_canonical_courses()

# ----------------- Auth Decorator -----------------

def facilitator_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'facilitator_id' not in session:
            return redirect(url_for('facilitator_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'facilitator_id' not in session:
            return redirect(url_for('facilitator_login', next=request.url))
        if session.get('facilitator_role') != 'admin':
            return redirect(url_for('facilitator_students', err="Access Denied: Only Hub Administrators have permission to perform this action."))
        return f(*args, **kwargs)
    return decorated_function

# ----------------- Candidate Routes -----------------

@app.route('/')
def index():
    settings = db.get_settings()
    active_exams = db.get_all_exams(active_only=True)
    return render_template('index.html', settings=settings, active_exams=active_exams)

@app.route('/start_exam', methods=['POST'])
def start_exam():
    reg_number = request.form.get('reg_number', '').strip().upper()
    last_name = request.form.get('last_name', '').strip()

    settings = db.get_settings()
    active_exams = db.get_all_exams(active_only=True)

    if not reg_number or not last_name:
        return render_template(
            'index.html',
            settings=settings,
            active_exams=active_exams,
            error="Please enter both your Student Registration Number and Surname (Last Name as password).",
            reg_number=reg_number,
            last_name=last_name
        )

    # Authenticate student by Reg No and Surname
    student = db.get_student_by_reg_and_lastname(reg_number, last_name)
    if not student:
        return render_template(
            'index.html',
            settings=settings,
            active_exams=active_exams,
            error=f"Access Denied: No candidate record found for Registration Number '{reg_number}' with Surname '{last_name}'. Please verify your details or contact your facilitator.",
            reg_number=reg_number,
            last_name=last_name
        )

    if student.get("is_active", 1) == 0:
        return render_template(
            'index.html',
            settings=settings,
            active_exams=active_exams,
            error=f"Access Denied: Account for '{student['first_name']} {student['last_name']}' is currently inactive. Please contact hub administration.",
            reg_number=reg_number,
            last_name=last_name
        )

    # Save authenticated candidate in session
    session['student_id'] = student['id']
    session['reg_number'] = student['reg_number']
    session['first_name'] = student['first_name']
    session['last_name'] = student['last_name']
    session['passport_url'] = student.get('passport_url') or ""

    return redirect(url_for('confirm_identity'))

@app.route('/confirm_identity')
def confirm_identity():
    if 'student_id' not in session or 'reg_number' not in session:
        return redirect(url_for('index'))

    student = db.get_student(session['student_id'])
    if not student:
        return redirect(url_for('index'))

    assigned_exams = student.get('assigned_exams', [])
    all_active_exams = db.get_all_exams(active_only=True)
    available_exams = assigned_exams if assigned_exams else all_active_exams

    settings = db.get_settings()
    error = request.args.get('err')

    return render_template(
        'confirm_identity.html',
        student=student,
        available_exams=available_exams,
        all_active_exams=all_active_exams,
        settings=settings,
        error=error
    )

@app.route('/begin_cbt', methods=['POST'])
def begin_cbt():
    if 'student_id' not in session or 'reg_number' not in session:
        return redirect(url_for('index'))

    exam_id_val = request.form.get('exam_id')
    try:
        exam_id = int(exam_id_val)
    except (ValueError, TypeError):
        return redirect(url_for('confirm_identity', err="Please select a valid examination to sit for."))

    exam = db.get_exam(exam_id)
    if not exam:
        return redirect(url_for('confirm_identity', err="Selected examination does not exist."))

    student = db.get_student(session['student_id'])
    if not student:
        return redirect(url_for('index'))

    # Verify whitelist permission
    allowed, reason, student_info = db.verify_student_access(
        student['first_name'],
        student['last_name'],
        exam_id,
        reg_number=student['reg_number']
    )
    if not allowed:
        return redirect(url_for('confirm_identity', err=reason))

    # Check previous attempts
    previous_attempts = db.get_student_attempts(exam_id, student['first_name'], student['last_name'], reg_number=student['reg_number'])
    cutoff = float(exam.get("passing_cutoff", 85.0))

    if len(previous_attempts) >= MAX_ATTEMPTS:
        best_score = max(a['score'] for a in previous_attempts)
        best_pct = max(a['percentage'] for a in previous_attempts)
        
        # Primary enrolled track of student
        student_primary_track = (student.get('primary_track') or '').upper() if student else ''
        if not student_primary_track and student.get('reg_number'):
            m = re.match(r'^(?:YMO-)?([A-Z]+)-\d+$', str(student['reg_number']).upper().replace('/', '-'))
            student_primary_track = m.group(1) if m else ''
            if student_primary_track == 'DMSM': student_primary_track = 'DM'

        exam_course_code = (exam.get('course_code') or '').upper()

        track_map = {
            'AIGA': 'Artificial Intelligence & Generative AI',
            'DL': 'Digital Literacy',
            'GDCC': 'Graphic Design & Content Creation',
            'CP': 'Coding & Programming',
            'DM': 'Digital Marketing & Social Media'
        }
        primary_track_name = track_map.get(student_primary_track, student_primary_track or 'Digital Skills')
        exam_track_name = track_map.get(exam_course_code, exam.get('cohort') or exam.get('title') or exam_course_code)

        return render_template(
            'result.html',
            student=student,
            student_id=student['id'],
            passport_url=student.get('passport_url'),
            course_code=student_primary_track,
            course_track=primary_track_name,
            exam_course_code=exam_course_code,
            exam_track_name=exam_track_name,
            reg_number=student['reg_number'],
            first_name=student['first_name'],
            last_name=student['last_name'],
            score=best_score,
            total=previous_attempts[0]['total'],
            percentage=best_pct,
            passed=(best_pct >= cutoff),
            attempts_used=len(previous_attempts),
            can_retake=False,
            locked_out=True,
            settings=exam
        )

    session['exam_id'] = exam_id
    session['first_name'] = student['first_name']
    session['last_name'] = student['last_name']
    session['reg_number'] = student['reg_number']

    return redirect(url_for('exam'))

@app.route('/logout_candidate')
def logout_candidate():
    session.pop('student_id', None)
    session.pop('reg_number', None)
    session.pop('first_name', None)
    session.pop('last_name', None)
    session.pop('passport_url', None)
    session.pop('exam_id', None)
    return redirect(url_for('index'))

@app.route('/student/passport/<int:student_id>')
def student_passport(student_id):
    student = db.get_student(student_id)
    if not student:
        return abort(404)

    passport_url = (student.get('passport_url') or '').strip()
    file_id = db.extract_drive_file_id(passport_url) if passport_url else None

    passports_dir = os.path.join(app.root_path, 'static', 'passports')
    os.makedirs(passports_dir, exist_ok=True)

    # 1. Check if passport_url is already a local static path
    if passport_url and passport_url.startswith('/static/'):
        local_rel = passport_url.lstrip('/')
        local_abs = os.path.join(app.root_path, local_rel)
        if os.path.exists(local_abs) and os.path.getsize(local_abs) > 500:
            mimetype = 'image/png' if local_abs.lower().endswith('.png') else 'image/jpeg'
            return send_file(local_abs, mimetype=mimetype, max_age=86400)

    # 2. Check local disk candidates by reg number, student ID, or file ID
    reg_clean = db.normalize_reg_number(student.get('reg_number', '')).replace('/', '-').replace(' ', '_')
    local_candidates = [
        f"{file_id}.jpg" if file_id else None,
        f"{file_id}.png" if file_id else None,
        f"{reg_clean}.jpg" if reg_clean else None,
        f"{reg_clean}.png" if reg_clean else None,
        f"{reg_clean}.jpeg" if reg_clean else None,
        f"student_{student_id}.jpg",
        f"student_{student_id}.png"
    ]
    for cname in local_candidates:
        if cname:
            cpath = os.path.join(passports_dir, cname)
            if os.path.exists(cpath) and os.path.getsize(cpath) > 500:
                mimetype = 'image/png' if cname.lower().endswith('.png') else 'image/jpeg'
                return send_file(cpath, mimetype=mimetype, max_age=86400)

    # 3. If remote URL exists, attempt downloading (with SSL context)
    if passport_url:
        download_urls = []
        if file_id:
            download_urls.append(f"https://lh3.googleusercontent.com/d/{file_id}")
        if passport_url and not passport_url.startswith("https://lh3.googleusercontent.com/d/"):
            download_urls.append(passport_url)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
        }

        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        cache_key = file_id if file_id else f"student_{student_id}"
        cache_path = os.path.join(passports_dir, f"{cache_key}.jpg")

        for url in download_urls:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=1.5, context=ctx) as resp:
                    content_type = resp.info().get_content_type()
                    data = resp.read()
                    is_img = (content_type.startswith('image/') or 
                              data.startswith(b'\xff\xd8\xff') or 
                              data.startswith(b'\x89PNG') or 
                              data.startswith(b'RIFF') or 
                              data.startswith(b'GIF8'))
                    if is_img and len(data) > 500:
                        with open(cache_path, 'wb') as f:
                            f.write(data)
                        return send_file(cache_path, mimetype=content_type if content_type.startswith('image/') else 'image/jpeg', max_age=86400)
            except Exception:
                continue

    # 4. High-Fidelity Official Candidate Identity SVG Badge
    fn = student.get('first_name', 'Student').strip()
    ln = student.get('last_name', '').strip()
    reg = student.get('reg_number', 'YMO-CBT').strip()
    initials = f"{fn[:1]}{ln[:1]}".upper() if (fn and ln) else (fn[:2].upper() if fn else "ST")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="300" height="360" viewBox="0 0 300 360">
      <defs>
        <linearGradient id="pbg" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#022c22"/>
          <stop offset="50%" stop-color="#044e39"/>
          <stop offset="100%" stop-color="#0f172a"/>
        </linearGradient>
        <linearGradient id="gold" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#fbbf24"/>
          <stop offset="100%" stop-color="#d97706"/>
        </linearGradient>
      </defs>
      <rect width="300" height="360" rx="14" fill="url(#pbg)"/>
      <circle cx="150" cy="115" r="54" fill="#ffffff" fill-opacity="0.08" stroke="url(#gold)" stroke-width="2.5"/>
      <circle cx="150" cy="115" r="46" fill="#044e39"/>
      <text x="150" y="126" font-family="'Plus Jakarta Sans', system-ui, sans-serif" font-size="30" font-weight="800" fill="#ffffff" text-anchor="middle">{initials}</text>
      <rect x="25" y="195" width="250" height="30" rx="15" fill="#ffffff" fill-opacity="0.12" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
      <text x="150" y="215" font-family="'Plus Jakarta Sans', system-ui, sans-serif" font-size="13" font-weight="800" fill="#fef08a" text-anchor="middle" letter-spacing="1">{reg}</text>
      <text x="150" y="255" font-family="'Plus Jakarta Sans', system-ui, sans-serif" font-size="16" font-weight="800" fill="#ffffff" text-anchor="middle">{fn} {ln}</text>
      <text x="150" y="278" font-family="'Plus Jakarta Sans', system-ui, sans-serif" font-size="11" font-weight="700" fill="#a7f3d0" text-anchor="middle" letter-spacing="0.5">OFFICIAL CBT CANDIDATE</text>
      <rect x="35" y="305" width="230" height="1" fill="rgba(255,255,255,0.15)"/>
      <text x="150" y="328" font-family="'Plus Jakarta Sans', system-ui, sans-serif" font-size="10" font-weight="600" fill="#94a3b8" text-anchor="middle">YEYE MOREMI OODUA TRAINING HUB</text>
    </svg>'''
    return Response(svg, mimetype='image/svg+xml', headers={'Cache-Control': 'public, max-age=600'})


@app.route('/exam')
def exam():
    if 'first_name' not in session or 'last_name' not in session or 'exam_id' not in session:
        return redirect(url_for('index'))

    first_name = session['first_name']
    last_name = session['last_name']
    exam_id = session['exam_id']

    exam = db.get_exam(exam_id)
    if not exam:
        return redirect(url_for('index'))

    previous_attempts = db.get_student_attempts(exam_id, first_name, last_name)
    if len(previous_attempts) >= MAX_ATTEMPTS:
        return redirect(url_for('index'))

    questions = db.get_questions_by_exam(exam_id)
    return render_template('cbt_question.html', questions=questions, settings=exam)

@app.route('/submit', methods=['POST'])
@app.route('/submit_exam', methods=['POST'])
def submit_exam():
    if request.is_json:
        form_data = request.get_json() or {}
        if "answers" in form_data and isinstance(form_data["answers"], dict):
            flat = dict(form_data)
            flat.update(form_data["answers"])
            form_data = flat
    else:
        form_data = request.form.to_dict()

    first_name = form_data.get('first_name') or session.get('first_name', 'Unknown')
    last_name = form_data.get('last_name') or session.get('last_name', 'Candidate')
    reg_number = form_data.get('reg_number') or session.get('reg_number')
    exam_id = int(form_data.get('exam_id') or session.get('exam_id', 1))

    exam = db.get_exam(exam_id) or {"title": "Assessment", "passing_cutoff": 85.0}

    previous_attempts = db.get_student_attempts(exam_id, first_name, last_name, reg_number=reg_number)
    current_attempt_num = len(previous_attempts) + 1

    if current_attempt_num > MAX_ATTEMPTS:
        if request.is_json:
            return jsonify({
                "status": "error",
                "message": "Maximum exam attempts reached",
                "max_attempts": MAX_ATTEMPTS
            }), 403
        return redirect(url_for('index'))

    # Grade submission dynamically against the specific exam's questions
    score, total, percentage, passed = grade_submission(form_data, exam_id)

    # Save to SQLite with student registration number
    db.save_submission(exam_id, first_name, last_name, current_attempt_num, score, total, percentage, passed, reg_number=reg_number)

    cutoff = float(exam.get("passing_cutoff", 85.0))

    # Determine highest grade achieved
    all_percentages = [a['percentage'] for a in previous_attempts] + [percentage]
    all_scores = [a['score'] for a in previous_attempts] + [score]
    best_percentage = max(all_percentages)
    best_score = max(all_scores)
    passed_overall = best_percentage >= cutoff

    can_retake = (current_attempt_num < MAX_ATTEMPTS)

    if request.is_json or request.headers.get('Accept') == 'application/json':
        return jsonify({
            "status": "success",
            "first_name": first_name,
            "last_name": last_name,
            "reg_number": reg_number,
            "exam_id": exam_id,
            "exam_title": exam.get('title'),
            "score": score,
            "total": total,
            "percentage": percentage,
            "passed": passed,
            "passed_overall": passed_overall,
            "attempt_number": current_attempt_num,
            "can_retake": can_retake
        }), 200

    # Fetch full student profile
    student = None
    if reg_number:
        student = db.get_student_by_reg(reg_number)
    if not student and session.get('student_id'):
        student = db.get_student(session.get('student_id'))

    student_id = student['id'] if student else None
    passport_url = student.get('passport_url') if student else None
    # Permanent primary enrolled track of student
    student_primary_track = (student.get('primary_track') or '').upper() if student else ''
    if not student_primary_track and reg_number:
        m = re.match(r'^(?:YMO-)?([A-Z]+)-\d+$', str(reg_number).upper().replace('/', '-'))
        student_primary_track = m.group(1) if m else ''
        if student_primary_track == 'DMSM': student_primary_track = 'DM'

    # Course track of the specific exam taken
    exam_course_code = (exam.get('course_code') or '').upper()

    track_map = {
        'AIGA': 'Artificial Intelligence & Generative AI',
        'DL': 'Digital Literacy',
        'GDCC': 'Graphic Design & Content Creation',
        'CP': 'Coding & Programming',
        'DM': 'Digital Marketing & Social Media'
    }
    primary_track_name = track_map.get(student_primary_track, student_primary_track or 'Digital Skills')
    exam_track_name = track_map.get(exam_course_code, exam.get('cohort') or exam.get('title') or exam_course_code)

    return render_template(
        'result.html',
        student=student,
        student_id=student_id,
        passport_url=passport_url,
        course_code=student_primary_track,
        course_track=primary_track_name,
        exam_course_code=exam_course_code,
        exam_track_name=exam_track_name,
        reg_number=reg_number,
        first_name=first_name,
        last_name=last_name,
        score=score,
        total=total,
        percentage=percentage,
        best_score=best_score,
        best_percentage=best_percentage,
        passed=passed_overall,
        attempt_number=current_attempt_num,
        can_retake=can_retake,
        locked_out=False,
        settings=exam
    )

# ----------------- Facilitator Authentication -----------------

@app.route('/facilitator/login', methods=['GET', 'POST'])
def facilitator_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = db.authenticate_facilitator(username, password)
        if user:
            session['facilitator_id'] = user['id']
            session['facilitator_username'] = user['username']
            session['facilitator_name'] = user['name']
            session['facilitator_role'] = user['role']
            next_url = request.args.get('next')
            if user['role'] == 'admin':
                return redirect(next_url or url_for('instructor_dashboard'))
            return redirect(next_url or url_for('facilitator_questions'))
        return render_template('facilitator_login.html', error="Invalid username or password.")
    return render_template('facilitator_login.html')

@app.route('/facilitator/register', methods=['GET', 'POST'])
def facilitator_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        course_code = request.form.get('course_code', 'ALL').strip().upper()

        if len(password) < 6:
            return render_template('facilitator_register.html', error="Password must be at least 6 characters.")

        ok, msg = db.create_facilitator(username, name, password)
        if ok:
            user = db.authenticate_facilitator(username, password)
            if user:
                fid = user['id']
                canonical_codes = {"AIGA": 1, "DL": 2, "GDCC": 3, "CP": 4, "DM": 5}
                if course_code in canonical_codes:
                    cid = canonical_codes[course_code]
                    db.assign_facilitator_courses(fid, [cid])
                else:
                    db.assign_facilitator_courses(fid, [1, 2, 3, 4, 5])

            return render_template('facilitator_login.html', message="Account created! Please sign in with your credentials.")
        return render_template('facilitator_register.html', error=msg)
    return render_template('facilitator_register.html')

@app.route('/facilitator/logout')
def facilitator_logout():
    session.pop('facilitator_id', None)
    session.pop('facilitator_username', None)
    session.pop('facilitator_name', None)
    session.pop('facilitator_role', None)
    return redirect(url_for('facilitator_login'))

# ----------------- Facilitator Multi-Exam & Question Management -----------------

@app.route('/facilitator/questions')
@facilitator_required
def facilitator_questions():
    role = session.get('facilitator_role', 'facilitator')
    facilitator_id = session.get('facilitator_id')

    if role == 'admin':
        return redirect(url_for('instructor_dashboard', msg="Administrator accounts oversee submissions, student enrollment, and facilitator accounts."))

    my_courses = db.get_facilitator_courses(facilitator_id)
    all_exams = my_courses if my_courses else db.get_all_exams(active_only=True)

    if not all_exams:
        db.create_exam("Default Assessment", duration_minutes=60, passing_cutoff=85.0)
        all_exams = db.get_all_exams()

    exam_id_arg = request.args.get('exam_id')
    current_exam = None
    if exam_id_arg:
        try:
            current_exam = db.get_exam(int(exam_id_arg))
        except (ValueError, TypeError):
            pass

    if not current_exam:
        current_exam = all_exams[0]

    questions = db.get_questions_by_exam(current_exam['id'])
    settings = db.get_settings()
    message = request.args.get('msg')
    error = request.args.get('err')

    return render_template(
        'facilitator_questions.html',
        questions=questions,
        current_exam=current_exam,
        all_exams=all_exams,
        settings=settings,
        message=message,
        error=error,
        role=role
    )

@app.route('/facilitator/exams/create', methods=['POST'])
@facilitator_required
def facilitator_create_exam():
    if session.get('facilitator_role') == 'admin':
        return redirect(url_for('facilitator_questions', err="Administrator accounts belong to no course and cannot create exams. Assessments are created by assigned track facilitators."))

    title = request.form.get('title', '').strip()
    cohort = request.form.get('cohort', '').strip()
    course_code = request.form.get('course_code', '').strip().upper()
    duration = int(request.form.get('duration_minutes', 60))
    cutoff = float(request.form.get('passing_cutoff', 85.0))
    instructions = request.form.get('instructions', '').strip()
    is_active = 1 if request.form.get('is_active') == '1' else 0
    auto_assign = True if request.form.get('auto_assign_students') == '1' else False
    facilitator_id = session.get('facilitator_id')

    if not title:
        return redirect(url_for('facilitator_questions', err="Assessment title is required."))

    new_exam_id = db.create_exam(
        title=title,
        cohort=cohort,
        duration_minutes=duration,
        passing_cutoff=cutoff,
        instructions=instructions,
        is_active=is_active,
        course_code=course_code,
        created_by=facilitator_id,
        auto_assign_students=auto_assign
    )
    course_label = f" under {course_code}" if course_code else ""
    return redirect(url_for('facilitator_questions', exam_id=new_exam_id, msg=f"Assessment '{title}' created successfully{course_label}! You can now add questions to it."))

@app.route('/facilitator/courses/create', methods=['POST'])
@facilitator_required
def facilitator_create_course():
    if session.get('facilitator_role') != 'admin':
        return redirect(url_for('facilitator_questions', err="Only administrators can register new course tracks."))

    code = request.form.get('code', '').strip().upper()
    title = request.form.get('title', '').strip()
    cohort = request.form.get('cohort', '').strip()
    duration = int(request.form.get('duration_minutes', 60))
    cutoff = float(request.form.get('passing_cutoff', 85.0))
    instructions = request.form.get('instructions', '').strip()

    success, msg = db.create_course_track(code, title, cohort, duration, cutoff, instructions)
    if success:
        return redirect(url_for('facilitator_manage_facilitators', msg=msg))
    else:
        return redirect(url_for('facilitator_manage_facilitators', err=msg))

@app.route('/facilitator/manage_facilitators', methods=['GET', 'POST'])
@facilitator_required
def facilitator_manage_facilitators():
    if session.get('facilitator_role') != 'admin':
        return redirect(url_for('facilitator_questions', err="Access restricted. Administrator privilege required."))

    message = request.args.get('msg')
    error = request.args.get('err')

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        course_ids = request.form.getlist('course_ids')

        if not name or not username or not password:
            error = "Facilitator name, username, and password are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            success, msg = db.create_facilitator(username, name, password, role="facilitator")
            if success:
                all_facs = db.get_all_facilitators()
                new_fac = next((f for f in all_facs if f['username'].lower() == username.lower()), None)
                if new_fac and course_ids:
                    db.assign_facilitator_courses(new_fac['id'], course_ids)
                message = f"Facilitator '{name}' (@{username}) successfully created and assigned courses."
            else:
                error = msg

    facilitators = db.get_all_facilitators()
    for f in facilitators:
        f['assigned_courses'] = db.get_facilitator_courses(f['id'])

    all_exams = db.get_all_exams()
    course_tracks = []
    seen_codes = set()
    for e in all_exams:
        code = (e.get('course_code') or '').upper()
        if code and code not in seen_codes:
            seen_codes.add(code)
            course_tracks.append(e)

    total_students = len(db.get_all_students())
    settings = db.get_settings()

    return render_template(
        'facilitator_manage.html',
        facilitators=facilitators,
        all_exams=all_exams,
        course_tracks=course_tracks,
        total_students=total_students,
        settings=settings,
        message=message,
        error=error,
        role=session.get('facilitator_role')
    )

@app.route('/facilitator/facilitator/delete/<int:facilitator_id>', methods=['POST'])
@facilitator_required
def facilitator_delete(facilitator_id):
    if session.get('facilitator_role') != 'admin':
        return redirect(url_for('facilitator_questions', err="Administrator privilege required."))

    success, msg = db.delete_facilitator(facilitator_id)
    if success:
        return redirect(url_for('facilitator_manage_facilitators', msg=msg))
    else:
        return redirect(url_for('facilitator_manage_facilitators', err=msg))

@app.route('/facilitator/facilitator/assign_courses/<int:facilitator_id>', methods=['POST'])
@facilitator_required
def facilitator_assign_courses(facilitator_id):
    if session.get('facilitator_role') != 'admin':
        return redirect(url_for('facilitator_questions', err="Administrator privilege required."))

    course_ids = request.form.getlist('course_ids')
    db.assign_facilitator_courses(facilitator_id, course_ids)
    return redirect(url_for('facilitator_manage_facilitators', msg="Facilitator courses updated successfully."))

@app.route('/facilitator/exams/edit/<int:exam_id>', methods=['POST'])
@facilitator_required
def facilitator_edit_exam(exam_id):
    title = request.form.get('title', '').strip()
    cohort = request.form.get('cohort', '').strip()
    course_code = request.form.get('course_code', '').strip().upper()
    duration = int(request.form.get('duration_minutes', 60))
    cutoff = float(request.form.get('passing_cutoff', 85.0))
    instructions = request.form.get('instructions', '').strip()
    is_active = 1 if request.form.get('is_active') == '1' else 0

    if not title:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err="Assessment title is required."))

    db.update_exam(exam_id, title, cohort, duration, cutoff, instructions, is_active, course_code=course_code)
    return redirect(url_for('facilitator_questions', exam_id=exam_id, msg="Assessment parameters updated successfully!"))

@app.route('/facilitator/exams/delete/<int:exam_id>', methods=['POST'])
@facilitator_required
def facilitator_delete_exam(exam_id):
    if exam_id <= 5:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err="The 5 primary course tracks (AIGA, DL, GDCC, CP, DM) are canonical and cannot be deleted. You can delete any additional assessments created under them."))

    all_exams = db.get_all_exams()
    if len(all_exams) <= 1:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err="Cannot delete the only existing assessment."))

    db.delete_exam(exam_id)
    remaining_exams = db.get_all_exams()
    next_exam_id = remaining_exams[0]['id'] if remaining_exams else 1
    return redirect(url_for('facilitator_questions', exam_id=next_exam_id, msg="Assessment and its questions deleted successfully."))


@app.route('/facilitator/questions/add', methods=['POST'])
@facilitator_required
def facilitator_add_question():
    exam_id = int(request.form.get('exam_id', 1))
    question = request.form.get('question', '').strip()
    opt_a = request.form.get('opt_a', '').strip()
    opt_b = request.form.get('opt_b', '').strip()
    opt_c = request.form.get('opt_c', '').strip()
    opt_d = request.form.get('opt_d', '').strip()
    answer = request.form.get('answer', 'A').strip().upper()

    if not question or not opt_a or not opt_b or not opt_c or not opt_d:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err="All question fields and options are required."))

    db.add_question(exam_id, question, opt_a, opt_b, opt_c, opt_d, answer)
    return redirect(url_for('facilitator_questions', exam_id=exam_id, msg="New question added successfully!"))

@app.route('/facilitator/questions/edit/<q_id>', methods=['POST'])
@facilitator_required
def facilitator_edit_question(q_id):
    exam_id = int(request.form.get('exam_id', 1))
    question = request.form.get('question', '').strip()
    opt_a = request.form.get('opt_a', '').strip()
    opt_b = request.form.get('opt_b', '').strip()
    opt_c = request.form.get('opt_c', '').strip()
    opt_d = request.form.get('opt_d', '').strip()
    answer = request.form.get('answer', 'A').strip().upper()

    if not question or not opt_a or not opt_b or not opt_c or not opt_d:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err="All question fields are required."))

    db.update_question(q_id, question, opt_a, opt_b, opt_c, opt_d, answer)
    return redirect(url_for('facilitator_questions', exam_id=exam_id, msg="Question updated successfully!"))

@app.route('/facilitator/questions/delete/<q_id>', methods=['POST'])
@facilitator_required
def facilitator_delete_question(q_id):
    q = db.get_question(q_id)
    exam_id = q['exam_id'] if q else 1
    db.delete_question(q_id)
    return redirect(url_for('facilitator_questions', exam_id=exam_id, msg="Question deleted and numbers re-sequenced."))

@app.route('/facilitator/settings', methods=['POST'])
@admin_required
def facilitator_update_settings():
    form_data = request.form.to_dict()
    current_exam_id = form_data.pop('current_exam_id', '1')
    db.update_settings(form_data)
    return redirect(url_for('facilitator_questions', exam_id=current_exam_id, msg="Global settings updated successfully!"))

@app.route('/facilitator/ai/generate', methods=['POST'])
@facilitator_required
def facilitator_ai_generate():
    data = request.get_json() or {}
    topic = data.get('topic', '').strip()
    count = int(data.get('count', 3))
    difficulty = data.get('difficulty', 'Intermediate')
    context = data.get('context', '').strip()

    if not topic:
        return jsonify({"success": False, "error": "Please provide a topic or concept."}), 400

    settings = db.get_settings()
    api_key = settings.get("gemini_api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()

    if not api_key:
        return jsonify({
            "success": False,
            "error": "No Google Gemini API key configured. Please enter your API key in Settings (or ask an administrator to set the GEMINI_API_KEY environment variable)."
        }), 400

    try:
        questions = generate_questions_with_ai(topic=topic, count=count, difficulty=difficulty, context=context, api_key=api_key)
        return jsonify({"success": True, "questions": questions})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/facilitator/ai/batch_insert', methods=['POST'])
@facilitator_required
def facilitator_ai_batch_insert():
    exam_id = int(request.form.get('exam_id', 1))
    raw_json = request.form.get('questions_json', '')
    if not raw_json:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err="No questions data received to import."))

    try:
        questions_list = json.loads(raw_json)
        inserted_count = 0
        for q in questions_list:
            db.add_question(
                exam_id,
                q.get("question", ""),
                q.get("opt_a", ""),
                q.get("opt_b", ""),
                q.get("opt_c", ""),
                q.get("opt_d", ""),
                q.get("answer", "A")
            )
            inserted_count += 1
        return redirect(url_for('facilitator_questions', exam_id=exam_id, msg=f"Successfully imported {inserted_count} AI questions!"))
    except Exception as e:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err=f"Batch import failed: {str(e)}"))

@app.route('/facilitator/ai/parse', methods=['POST'])
@facilitator_required
def facilitator_ai_parse():
    exam_id = int(request.form.get('exam_id', 1))
    raw_text = request.form.get('raw_text', '').strip()
    if not raw_text:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err="Please paste questions text to parse."))

    try:
        parsed_qs = smart_parse_raw_text(raw_text)
        if not parsed_qs:
            return redirect(url_for('facilitator_questions', exam_id=exam_id, err="No valid questions could be detected. Please ensure questions are numbered with 4 options (A-D)."))

        count = 0
        for q in parsed_qs:
            db.add_question(
                exam_id,
                q["question"],
                q["opt_a"],
                q["opt_b"],
                q["opt_c"],
                q["opt_d"],
                q["answer"]
            )
            count += 1
        return redirect(url_for('facilitator_questions', exam_id=exam_id, msg=f"Smart Text Parser successfully imported {count} question(s)!"))
    except Exception as e:
        return redirect(url_for('facilitator_questions', exam_id=exam_id, err=f"Parsing error: {str(e)}"))

@app.route('/instructor')
@facilitator_required
def instructor_dashboard():
    exam_id_arg = request.args.get('exam_id')
    course_code_arg = request.args.get('course_code', '').strip().upper()
    selected_exam_id = None
    if exam_id_arg:
        try:
            selected_exam_id = int(exam_id_arg)
        except (ValueError, TypeError):
            pass

    role = session.get('facilitator_role', 'facilitator')
    facilitator_id = session.get('facilitator_id')

    if role == 'admin':
        all_exams = db.get_all_exams()
        submissions = db.get_all_submissions(exam_id=selected_exam_id, course_code=course_code_arg)
    else:
        all_exams = db.get_facilitator_courses(facilitator_id)
        allowed_exam_ids = [c['id'] for c in all_exams]
        submissions = db.get_all_submissions(exam_id=selected_exam_id, allowed_exam_ids=allowed_exam_ids, course_code=course_code_arg)

    # Distinct course tracks for course switcher
    course_tracks = []
    seen_codes = set()
    for e in all_exams:
        code = (e.get('course_code') or '').upper()
        if code and code not in seen_codes:
            seen_codes.add(code)
            course_tracks.append({
                "code": code,
                "title": e.get('title'),
                "id": e.get('id')
            })

    settings = db.get_settings()
    message = request.args.get('msg')
    error = request.args.get('err')

    # Calculate questions count & responsive active title for display
    target_exam = None
    if selected_exam_id:
        target_exam = db.get_exam(selected_exam_id)
    elif course_code_arg:
        target_exam = next((e for e in all_exams if (e.get('course_code') or '').upper() == course_code_arg), None)

    if target_exam:
        total_questions = target_exam.get('question_count', 0)
        exam_settings = dict(settings)
        exam_settings.update(target_exam)
        c_code = target_exam.get('course_code')
        active_display_title = f"{target_exam['title']} ({c_code}) Assessment" if c_code else f"{target_exam['title']} Assessment"
        active_display_cohort = target_exam.get('cohort') or target_exam['title']
    else:
        total_questions = sum(e.get('question_count', 0) for e in all_exams)
        exam_settings = settings
        active_display_title = "All Academy Courses & Assessments"
        active_display_cohort = "All Enrolled Cohorts & Digital Tracks"

    return render_template(
        'instructor.html',
        submissions=submissions,
        settings=exam_settings,
        active_display_title=active_display_title,
        active_display_cohort=active_display_cohort,
        all_exams=all_exams,
        course_tracks=course_tracks,
        selected_exam_id=selected_exam_id,
        selected_course_code=course_code_arg,
        total_questions=total_questions,
        message=message,
        error=error,
        role=role
    )

@app.route('/facilitator/submissions/reset_retake', methods=['POST'])
@facilitator_required
def reset_submission_retake():
    exam_id = request.form.get('exam_id')
    reg_number = request.form.get('reg_number')
    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')
    next_url = request.form.get('next') or url_for('instructor_dashboard')

    if not exam_id:
        return redirect(f"{next_url}?err=Missing+assessment+identifier.")

    success, msg = db.reset_student_retake(exam_id, reg_number=reg_number, first_name=first_name, last_name=last_name)
    cand_label = f" for {first_name} {last_name} ({reg_number})" if first_name else ""
    return redirect(f"{next_url}?msg=Retake+ability+successfully+reset{cand_label}!+Candidate+can+now+sit+for+the+exam.")

@app.route('/facilitator/facilitator/reset_password/<int:facilitator_id>', methods=['POST'])
@admin_required
def facilitator_reset_password(facilitator_id):
    new_password = request.form.get('new_password', '').strip()
    if len(new_password) < 6:
        return redirect(url_for('facilitator_manage_facilitators', err="Password must be at least 6 characters."))

    success, msg = db.update_facilitator_password(facilitator_id, new_password)
    if success:
        return redirect(url_for('facilitator_manage_facilitators', msg="Facilitator password updated successfully."))
    return redirect(url_for('facilitator_manage_facilitators', err=msg))

@app.route('/facilitator/change_password', methods=['POST'])
@facilitator_required
def facilitator_change_password():
    current_password = request.form.get('current_password', '').strip()
    new_password = request.form.get('new_password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    next_url = request.form.get('next') or url_for('instructor_dashboard')

    if len(new_password) < 6:
        return redirect(f"{next_url}?err=New+password+must+be+at+least+6+characters.")
    if new_password != confirm_password:
        return redirect(f"{next_url}?err=New+password+and+confirmation+do+not+match.")

    user_id = session.get('facilitator_id')
    user_name = session.get('facilitator_username')
    auth_user = db.authenticate_facilitator(user_name, current_password)
    if not auth_user:
        return redirect(f"{next_url}?err=Current+password+is+incorrect.")

    success, msg = db.update_facilitator_password(user_id, new_password)
    if success:
        return redirect(f"{next_url}?msg=Your+password+has+been+updated+successfully!")
    return redirect(f"{next_url}?err={msg}")

@app.route('/instructor/export_csv')
@facilitator_required
def export_csv():
    exam_id_arg = request.args.get('exam_id')
    selected_exam_id = None
    if exam_id_arg:
        try:
            selected_exam_id = int(exam_id_arg)
        except (ValueError, TypeError):
            pass

    role = session.get('facilitator_role', 'facilitator')
    facilitator_id = session.get('facilitator_id')

    course_code_arg = request.args.get('course_code', '').strip().upper()

    if role == 'admin':
        submissions = db.get_all_submissions(exam_id=selected_exam_id, course_code=course_code_arg)
    else:
        my_courses = db.get_facilitator_courses(facilitator_id)
        allowed_exam_ids = [c['id'] for c in my_courses]
        submissions = db.get_all_submissions(exam_id=selected_exam_id, allowed_exam_ids=allowed_exam_ids, course_code=course_code_arg)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Reg Number', 'First Name', 'Last Name', 'Primary Track', 'Assessment Sat For', 'Attempt', 'Score', 'Total', 'Percentage (%)', 'Status', 'Timestamp'])
    for s in submissions:
        status_label = 'PASSED' if s['passed'] == 1 else 'RETAKE'
        writer.writerow([
            s.get('reg_number') or '—',
            s['first_name'],
            s['last_name'],
            s.get('student_primary_track') or '—',
            s.get('exam_title', 'Assessment'),
            s['attempt_number'],
            s['score'],
            s['total'],
            s['percentage'],
            status_label,
            s['submitted_at']
        ])

    file_suffix = f"_{course_code_arg}" if course_code_arg else (f"_Exam_{selected_exam_id}" if selected_exam_id else "_All")
    filename = f"YMO_Assessment_Results{file_suffix}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

# ----------------- Facilitator Student Roster & Course Access Management -----------------

@app.route('/facilitator/students')
@facilitator_required
def facilitator_students():
    search = request.args.get('search', '').strip()
    exam_id_arg = request.args.get('exam_id')
    filter_exam_id = None
    if exam_id_arg:
        try:
            filter_exam_id = int(exam_id_arg)
        except (ValueError, TypeError):
            pass

    role = session.get('facilitator_role', 'facilitator')
    facilitator_id = session.get('facilitator_id')
    all_exams = db.get_all_exams()

    if role == 'admin':
        allowed_exam_ids = None
        my_courses = all_exams
    else:
        my_courses = db.get_facilitator_courses(facilitator_id)
        allowed_exam_ids = [c['id'] for c in my_courses]

    students = db.get_all_students(search=search, exam_id=filter_exam_id, allowed_exam_ids=allowed_exam_ids)
    settings = db.get_settings()
    message = request.args.get('msg')
    error = request.args.get('err')

    return render_template(
        'facilitator_students.html',
        students=students,
        all_exams=all_exams,
        my_courses=my_courses,
        settings=settings,
        message=message,
        error=error,
        selected_exam_id=filter_exam_id,
        search=search,
        role=role
    )

@app.route('/facilitator/set_courses', methods=['POST'])
@facilitator_required
def facilitator_set_courses():
    exam_ids = [int(x) for x in request.form.getlist('exam_ids') if x.isdigit()]
    facilitator_id = session['facilitator_id']
    db.assign_facilitator_courses(facilitator_id, exam_ids)
    next_url = request.form.get('next') or url_for('facilitator_students')
    return redirect(f"{next_url}?msg=Teaching+program+assignments+updated+successfully!")

@app.route('/facilitator/students/add', methods=['POST'])
@admin_required
def facilitator_add_student():
    reg_number = request.form.get('reg_number', '').strip()
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    exam_ids = request.form.getlist('exam_ids')

    ok, msg, sid = db.add_student(reg_number, first_name, last_name, email=email, phone=phone, exam_ids=exam_ids)
    if ok:
        return redirect(url_for('facilitator_students', msg=msg))
    return redirect(url_for('facilitator_students', err=msg))

@app.route('/facilitator/students/edit/<int:student_id>', methods=['POST'])
@facilitator_required
def facilitator_edit_student(student_id):
    reg_number = request.form.get('reg_number', '').strip()
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    is_active = int(request.form.get('is_active', 1))
    exam_ids = request.form.getlist('exam_ids')
    passport_url = request.form.get('passport_url', '').strip()

    uploaded_passport = request.files.get('passport_file')
    if uploaded_passport and uploaded_passport.filename:
        passports_dir = os.path.join(app.root_path, 'static', 'passports')
        os.makedirs(passports_dir, exist_ok=True)
        ext = os.path.splitext(uploaded_passport.filename)[1].lower() or '.jpg'
        clean_reg = db.normalize_reg_number(reg_number) or f"student_{student_id}"
        clean_reg = clean_reg.replace('/', '-').replace(' ', '_')
        save_name = f"{clean_reg}{ext}"
        save_path = os.path.join(passports_dir, save_name)
        uploaded_passport.save(save_path)
        passport_url = f"/static/passports/{save_name}"

    ok, msg = db.update_student(
        student_id, 
        reg_number, 
        first_name, 
        last_name, 
        email=email, 
        phone=phone, 
        exam_ids=exam_ids, 
        is_active=is_active,
        passport_url=passport_url if passport_url else None
    )
    if ok:
        return redirect(url_for('facilitator_students', msg=msg))
    return redirect(url_for('facilitator_students', err=msg))

@app.route('/facilitator/students/delete/<int:student_id>', methods=['POST'])
@admin_required
def facilitator_delete_student(student_id):
    db.delete_student(student_id)
    return redirect(url_for('facilitator_students', msg="Student record and exam permissions removed."))

@app.route('/facilitator/students/bulk_import', methods=['POST'])
@admin_required
def facilitator_bulk_import_students():
    raw_text = request.form.get('raw_text', '').strip()
    exam_ids = [int(x) for x in request.form.getlist('exam_ids') if x.isdigit()]

    # If a file was uploaded, read text from file
    uploaded_file = request.files.get('file')
    if uploaded_file and uploaded_file.filename:
        try:
            file_bytes = uploaded_file.read()
            file_text = file_bytes.decode('utf-8-sig', errors='replace')
            raw_text = (raw_text + "\n" + file_text).strip()
        except Exception as e:
            return redirect(url_for('facilitator_students', err=f"File read error: {str(e)}"))

    if not raw_text:
        return redirect(url_for('facilitator_students', err="Please paste student data or upload a CSV file."))

    success, skipped, summary = db.bulk_import_students(raw_text, default_exam_ids=exam_ids)
    if success > 0:
        return redirect(url_for('facilitator_students', msg=summary))
    return redirect(url_for('facilitator_students', err=summary))

@app.route('/facilitator/students/export_csv')
@facilitator_required
def facilitator_export_students_csv():
    role = session.get('facilitator_role', 'facilitator')
    facilitator_id = session.get('facilitator_id')
    if role == 'admin':
        students = db.get_all_students()
    else:
        my_courses = db.get_facilitator_courses(facilitator_id)
        allowed_exam_ids = [c['id'] for c in my_courses]
        students = db.get_all_students(allowed_exam_ids=allowed_exam_ids)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Registration Number', 'First Name', 'Last Name', 'Email', 'Phone', 'Status', 'Enrolled Courses', 'Registered At'])
    for s in students:
        courses_str = ", ".join(s.get('assigned_courses', []))
        status_str = "ACTIVE" if s.get('is_active') == 1 else "INACTIVE"
        writer.writerow([
            s['reg_number'],
            s['first_name'],
            s['last_name'],
            s.get('email', ''),
            s.get('phone', ''),
            status_str,
            courses_str,
            s.get('created_at', '')
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=YMO_Registered_Students.csv"}
    )

@app.route('/facilitator/students/preview_google_sheet', methods=['POST'])
@admin_required
def facilitator_preview_google_sheet():
    sheet_url = request.form.get('sheet_url', '').strip()
    sheet_paste = request.form.get('sheet_paste', '').strip()
    sheet_input = sheet_url or sheet_paste or request.form.get('sheet_input', '').strip()
    selected_gid = request.form.get('selected_gid', '').strip() or None

    if not sheet_input:
        return jsonify({'ok': False, 'message': 'Please provide a Google Sheets URL or paste student rows.'})

    result = db.preview_google_sheet_students(sheet_input, selected_gid=selected_gid)
    return jsonify(result)

@app.route('/facilitator/students/import_google_sheet', methods=['POST'])
@admin_required
def facilitator_import_google_sheet():
    sheet_url = request.form.get('sheet_url', '').strip()
    sheet_paste = request.form.get('sheet_paste', '').strip()
    sheet_input = sheet_url or sheet_paste or request.form.get('sheet_input', '').strip()
    selected_gid = request.form.get('selected_gid', '').strip() or None

    uploaded_file = request.files.get('file')
    if uploaded_file and uploaded_file.filename:
        try:
            file_bytes = uploaded_file.read()
            sheet_input = file_bytes.decode('utf-8-sig', errors='replace').strip()
        except Exception as e:
            return redirect(url_for('facilitator_students', err=f"File read error: {str(e)}"))

    if not sheet_input:
        return redirect(url_for('facilitator_students', err="Please provide a Google Sheets URL or paste the spreadsheet cells."))

    total, skipped, msg = db.import_google_sheet_students(sheet_input, selected_gid=selected_gid)
    if total > 0:
        return redirect(url_for('facilitator_students', msg=msg))
    return redirect(url_for('facilitator_students', err=msg))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)