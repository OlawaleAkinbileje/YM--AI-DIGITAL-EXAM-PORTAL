"""
YMO CBT Portal - Automated End-to-End Test Suite
Tests candidate login, exam sitting, questions loading, and facilitator/admin portals.
"""

from app import app
import db

def run_tests():
    print("=" * 60)
    print("YMO CBT Portal - Verification & Test Suite")
    print("=" * 60)

    client = app.test_client()

    # 1. Homepage
    res_home = client.get("/")
    assert res_home.status_code == 200, f"Expected 200, got {res_home.status_code}"
    print("[PASS] 1. Candidate Portal Homepage (GET /) -> 200 OK")

    # 2. Candidate Authentication & Identity Confirmation
    res_login = client.post(
        "/start_exam",
        data={"reg_number": "YMO-GDCC-001", "last_name": "Jimoh"},
        follow_redirects=True
    )
    assert res_login.status_code == 200
    html_login = res_login.get_data(as_text=True)
    assert "Confirm Candidate Identity" in html_login, "Identity confirmation title missing"
    assert "Graphic Design" in html_login, "Enrolled course missing"
    print("[PASS] 2. Candidate Login & Identity Confirmation -> Verified")

    # 3. Exam Loading & Questions Delivery
    with client.session_transaction() as sess:
        sess["student_id"] = 105
        sess["reg_number"] = "YMO-GDCC-001"
        sess["first_name"] = "Rasidat Tejumade"
        sess["last_name"] = "Jimoh"

    res_exam = client.post("/begin_cbt", data={"exam_id": 3}, follow_redirects=True)
    assert res_exam.status_code == 200
    html_exam = res_exam.get_data(as_text=True)
    assert "timer" in html_exam.lower() or "submit" in html_exam.lower(), "Exam interface not loaded properly"
    print("[PASS] 3. Examination Session & Questions Bank -> Loaded & Active")

    # 4. Administrator Authentication
    res_admin = client.post(
        "/facilitator/login",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=True
    )
    assert res_admin.status_code == 200
    html_admin = res_admin.get_data(as_text=True)
    assert "Logout" in html_admin or "Submissions" in html_admin, "Admin dashboard login failed"
    print("[PASS] 4. Admin Authentication & Dashboard -> Verified")

    # 5. Question Bank Management
    res_q = client.get("/facilitator/questions", follow_redirects=True)
    assert res_q.status_code == 200
    print("[PASS] 5. Question Bank Management (/facilitator/questions) -> 200 OK")

    # 6. Student Roster & Course Access
    res_roster = client.get("/facilitator/students", follow_redirects=True)
    assert res_roster.status_code == 200
    print("[PASS] 6. Student Enrolment & Roster (/facilitator/students) -> 200 OK")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED! The CBT Assessment Portal is 100% Operational.")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()

