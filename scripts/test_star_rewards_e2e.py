#!/usr/bin/env python3
"""E2E — Staff Portal Star Rewards (10 Small Stars → 1 Big Star).

  A  Small 0, +1 → 1/10
  B  Small 9, +1 → Big 1 / Small 0
  C  Big 2 / Small 9, +1 → Big 3 / Small 0
  D  existing total 27 → Big 2 / Small 7 (migration, idempotent)
  E  Small 0, − → stays 0
  F  after a Big Star, − on Small never removes the Big Star
  G  Staff A's changes never touch Staff B
Plus: milestone history + who/why on the Big Star, Reset Small vs Reset All (confirm),
explicit owner-only Big Star removal, header + All Staff overview display, permissions.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import auth
import database as db
import star_points
from app import app

_n = 0


def _crew(prefix="StarTest"):
    global _n
    _n += 1
    db.init_db()
    return db.create_crew_member("{0}-{1}-{2}".format(prefix, os.getpid(), _n), role="Mover", active=1)


def _owner():
    global _n
    _n += 1
    db.init_db()
    uid = db.create_staff_user("star-owner-{0}-{1}".format(os.getpid(), _n), auth.hash_password("t"), "Star Owner", is_admin=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return client, uid


def _post(client, crew_id, path, **data):
    data.setdefault("staff_id", crew_id)
    data.setdefault("range", "week")
    data.setdefault("week", 0)
    return client.post("/staff/crew/{0}/star-points/{1}".format(crew_id, path), data=data, follow_redirects=True)


def _set_small(crew_id, n):
    with db.get_connection() as conn:
        conn.execute("UPDATE crew_members SET star_points = ? WHERE id = ?", (n, crew_id))
        conn.commit()


def _state(crew_id):
    return db.get_crew_big_stars(crew_id), db.get_crew_star_points(crew_id)


def test_case_a_first_small_star():
    owner, _ = _owner()
    c = _crew()
    _post(owner, c, "adjust", action="increment", reason="Helpful on the job")
    assert _state(c) == (0, 1)
    html = owner.get("/staff?staff_id={0}&range=week".format(c)).get_data(as_text=True)
    assert "1 / 10" in html and "★☆☆☆☆☆☆☆☆☆" in html
    return True


def test_case_b_ninth_to_big_star_with_history():
    owner, uid = _owner()
    c = _crew()
    for _ in range(9):
        db.adjust_crew_star_points(c, 1)
    assert _state(c) == (0, 9)
    resp = _post(owner, c, "adjust", action="increment", reason="Great customer feedback")
    assert _state(c) == (1, 0)
    page = resp.get_data(as_text=True)
    assert "Milestone reached" in page
    big = db.list_crew_big_stars(c)
    assert len(big) == 1 and big[0]["awarded_by_user_id"] == uid and big[0]["reason"] == "Great customer feedback"
    assert big[0]["source"] == "milestone" and big[0]["earned_at"]
    events = db.list_crew_star_point_events(c)
    assert events[0]["change_type"] == "milestone"
    assert events[0]["reason"] == "Milestone reached: 10 Small Stars → 1 Big Star"
    assert events[0]["big_stars_after"] == 1 and events[0]["points_after"] == 0
    assert events[1]["change_type"] == "increment" and events[1]["reason"] == "Great customer feedback"
    assert events[1]["points_after"] == 10
    html = owner.get("/staff?staff_id={0}&range=week".format(c)).get_data(as_text=True)
    assert "BIG STARS" in html and "PROGRESS TO NEXT STAR" in html and "0 / 10" in html
    assert '<span class="staff-star-big-count">1</span>' in html
    assert "Milestone reached: 10 Small Stars → 1 Big Star" in html  # history
    return True


def test_case_c_big_two_small_nine():
    owner, _ = _owner()
    c = _crew()
    for _ in range(29):
        db.adjust_crew_star_points(c, 1)
    assert _state(c) == (2, 9)
    _post(owner, c, "adjust", action="increment")
    assert _state(c) == (3, 0)
    html = owner.get("/staff?staff_id={0}&range=week".format(c)).get_data(as_text=True)
    assert "🌟🌟🌟" in html
    return True


def test_case_d_migration_of_existing_totals():
    c27 = _crew()
    c10 = _crew()
    c4 = _crew()
    _set_small(c27, 27)
    _set_small(c10, 10)
    _set_small(c4, 4)
    with db.get_connection() as conn:
        db._migrate_star_points_to_big_stars(conn)
        conn.commit()
    assert _state(c27) == (2, 7)
    assert _state(c10) == (1, 0)
    assert _state(c4) == (0, 4)
    assert all(b["source"] == "migration" for b in db.list_crew_big_stars(c27))
    assert db.list_crew_star_point_events(c27)[0]["change_type"] == "migration"
    db.init_db()  # running the startup migration again changes nothing
    with db.get_connection() as conn:
        db._migrate_star_points_to_big_stars(conn)
        conn.commit()
    assert _state(c27) == (2, 7) and _state(c10) == (1, 0)
    return True


def test_case_e_minus_at_zero():
    owner, _ = _owner()
    c = _crew()
    _post(owner, c, "adjust", action="decrement")
    assert _state(c) == (0, 0)
    assert not db.list_crew_star_point_events(c)
    return True


def test_case_f_minus_after_big_star_keeps_big_star():
    owner, _ = _owner()
    c = _crew()
    for _ in range(12):
        db.adjust_crew_star_points(c, 1)
    assert _state(c) == (1, 2)
    for _ in range(4):
        _post(owner, c, "adjust", action="decrement")
    assert _state(c) == (1, 0)
    return True


def test_case_g_isolation():
    owner, _ = _owner()
    a = _crew("StarA")
    b = _crew("StarB")
    for _ in range(3):
        db.adjust_crew_star_points(b, 1)
    for _ in range(10):
        _post(owner, a, "adjust", action="increment")
    _post(owner, a, "adjust", action="decrement")
    _post(owner, a, "reset", confirm="1", scope="all")
    assert _state(a) == (0, 0)
    assert _state(b) == (0, 3)
    return True


def test_reset_small_vs_all_and_confirm():
    owner, _ = _owner()
    c = _crew()
    for _ in range(25):
        db.adjust_crew_star_points(c, 1)
    assert _state(c) == (2, 5)
    _post(owner, c, "reset", confirm="0", scope="all")
    assert _state(c) == (2, 5), "no confirm, nothing happens"
    _post(owner, c, "reset", confirm="1", scope="small", reason="Quarter")
    assert _state(c) == (2, 0)
    _post(owner, c, "reset", confirm="1", scope="all")
    assert _state(c) == (0, 0)
    assert len(db.list_crew_big_stars(c, include_revoked=True)) == 2, "Big Stars are revoked, never deleted"
    types = [e["change_type"] for e in db.list_crew_star_point_events(c)]
    assert types[:2] == ["reset_all", "reset_small"]
    html = owner.get("/staff?staff_id={0}&range=week".format(c)).get_data(as_text=True)
    assert "Reset Small Stars" in html and "Reset All Star Rewards" in html
    assert html.count('onsubmit="return confirm(') >= 3
    return True


def test_remove_big_star_is_explicit_and_owner_only():
    owner, uid = _owner()
    c = _crew()
    for _ in range(20):
        db.adjust_crew_star_points(c, 1)
    assert _state(c) == (2, 0)
    viewer = app.test_client()
    blocked = viewer.post("/staff/crew/{0}/star-points/remove-big".format(c), data={"confirm": "1"})
    assert blocked.status_code in (302, 403) and _state(c) == (2, 0)
    _post(owner, c, "remove-big", confirm="0")
    assert _state(c) == (2, 0)
    _post(owner, c, "remove-big", confirm="1", reason="Given by mistake")
    assert _state(c) == (1, 0)
    revoked = [b for b in db.list_crew_big_stars(c, include_revoked=True) if b["revoked_at"]]
    assert len(revoked) == 1 and revoked[0]["revoked_by_user_id"] == uid and revoked[0]["revoke_reason"] == "Given by mistake"
    assert db.list_crew_star_point_events(c)[0]["change_type"] == "big_star_removed"
    return True


def test_all_staff_overview_for_owner():
    owner, _ = _owner()
    c = _crew("Overview")
    for _ in range(23):
        db.adjust_crew_star_points(c, 1)
    html = owner.get("/staff?staff_id=all&range=week").get_data(as_text=True)
    assert "STAR REWARDS — ALL STAFF" in html
    import re

    section = html[html.index('class="staff-star-overview"'):]
    section = section[: section.index("</section>")]
    row = re.search(r"Overview-{0}-\d+</a>(.*?)</li>".format(os.getpid()), section, re.S).group(1)
    assert "×2" in row and "★★★☆☆☆☆☆☆☆" in row and "3 / 10" in row
    viewer_html = app.test_client().get("/staff?staff_id=all&range=week").get_data(as_text=True)
    assert "STAR REWARDS — ALL STAFF" not in viewer_html
    return True


def test_deleting_unused_crew_removes_star_records():
    c = _crew("DeleteMe")
    for _ in range(11):
        db.adjust_crew_star_points(c, 1)
    assert db.delete_crew_member(c)
    assert db.get_crew_big_stars(c) == 0 and db.list_crew_star_point_events(c) == []
    assert db.list_crew_big_stars(c, include_revoked=True) == []
    return True


def test_view_helper():
    v = star_points.build_star_rewards_view(7, 2)
    assert v["stars_display"] == "★★★★★★★☆☆☆" and v["count_display"] == "7 / 10" and v["big_icons"] == "🌟🌟" and v["big"] == 2
    many = star_points.build_star_rewards_view(0, 20)
    assert many["big_more"] and len(many["big_icons"]) == star_points.BIG_STAR_ICONS_SHOWN
    return True


def main():
    tests = [
        test_case_a_first_small_star,
        test_case_b_ninth_to_big_star_with_history,
        test_case_c_big_two_small_nine,
        test_case_d_migration_of_existing_totals,
        test_case_e_minus_at_zero,
        test_case_f_minus_after_big_star_keeps_big_star,
        test_case_g_isolation,
        test_reset_small_vs_all_and_confirm,
        test_remove_big_star_is_explicit_and_owner_only,
        test_all_staff_overview_for_owner,
        test_deleting_unused_crew_removes_star_records,
        test_view_helper,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                print("PASS:", test.__name__)
                passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            print("FAIL:", test.__name__, "—", exc)
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
