"""Server-side editable taxonomy: pattern matching, priority/specificity, the no-match
fallback, and RETROACTIVE reclassification (editing a rule changes past rollups with no
re-ingest). Plus retention nulls full URLs at 14d."""
import os
import sys
import datetime as dt
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import db, taxonomy, rollup  # noqa: E402


def _rule(match_type, pattern, sub="x", is_meeting=0):
    return {"id": 1, "match_type": match_type, "pattern": pattern,
            "sub_category": sub, "is_meeting": is_meeting}


class TestMatching(unittest.TestCase):
    def test_app_exact_strips_exe(self):
        self.assertTrue(taxonomy.match_rule(_rule("app", "outlook"), "OUTLOOK.EXE", None, None))
        self.assertFalse(taxonomy.match_rule(_rule("app", "outlook"), "chrome", None, None))

    def test_domain_exact_and_suffix(self):
        r = _rule("domain", "github.com")
        self.assertTrue(taxonomy.match_rule(r, "chrome", "github.com", None))
        self.assertTrue(taxonomy.match_rule(r, "chrome", "api.github.com", None))
        self.assertFalse(taxonomy.match_rule(r, "chrome", "notgithub.com", None))

    def test_domain_matches_url_host(self):
        # stored domain is registrable, but the rule's pattern is a subdomain — the url host wins
        r = _rule("domain", "dialer.example.com")
        self.assertTrue(taxonomy.match_rule(r, "chrome", "example.com",
                                            "https://dialer.example.com/calls"))

    def test_url_path_glob(self):
        r = _rule("url_path", "ezlynx.com/quotes/*/rating", "rating")
        self.assertTrue(taxonomy.match_rule(r, "chrome", "ezlynx.com",
                                            "https://app.ezlynx.com/quotes/55/rating"))
        self.assertFalse(taxonomy.match_rule(r, "chrome", "ezlynx.com",
                                             "https://app.ezlynx.com/quotes/55/documents"))


class TestResolver(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_priority_and_specificity(self):
        # a url_path rule (specific) and a domain rule both match → url_path wins via ordering
        self.conn.execute("INSERT OR IGNORE INTO category(sub_category,coarse_class) VALUES ('rating','neutral'),('rater','neutral')")
        self.conn.execute("INSERT INTO taxonomy_rule(match_type,pattern,sub_category,priority) VALUES "
                          "('domain','ez.com','rater',100),('url_path','ez.com/q/*/rating','rating',100)")
        self.conn.commit()
        rules = db.taxonomy_rules(self.conn)
        sub, _, _ = taxonomy.categorize_server(self.conn, rules, "chrome", "ez.com",
                                               "https://ez.com/q/5/rating")
        self.assertEqual(sub, "rating")  # url_path is more specific → ordered first

    def test_no_match_returns_sentinel(self):
        rules = db.taxonomy_rules(self.conn)
        sub, coarse, _ = taxonomy.categorize_server(self.conn, rules, "unknownapp", "nowhere.zzz", None)
        self.assertIsNone(sub)  # caller falls back to the stored sub_category

    def test_conferencing_safety_net(self):
        rules = db.taxonomy_rules(self.conn)
        _, _, is_meeting = taxonomy.categorize_server(self.conn, rules, "zoom", None, None)
        self.assertTrue(is_meeting)


class TestRetroactive(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        self.mfk = db.resolve_machine(self.conn, "ws1", auto_provision=True, hostname="WS1")
        self.ufk = db.resolve_user(self.conn, self.mfk, "sam", auto_provision=True)
        self.day = dt.date.today().isoformat()
        ts = self.day + "T10:00:00+00:00"
        self.conn.execute(
            "INSERT INTO activity_event(machine_fk,user_fk,client_event_id,ts,ts_norm,app,domain,url,"
            "sub_category,category_code,state,active_ms,idle_ms,is_meeting) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.mfk, self.ufk, "e1", ts, ts, "chrome", "newcarrier.example.com",
             "https://newcarrier.example.com/quote", "uncategorized", "neutral", "active",
             1800000, 0, 0))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_editing_rule_reclassifies_history(self):
        L1, e1, _ = rollup.build_ledger(self.conn, self.ufk, self.day)
        subs_before = {t["sub"] for t in e1["top"]}
        self.assertIn("uncategorized", subs_before)

        # admin adds a rule — NO re-ingest
        self.conn.execute("INSERT INTO category(sub_category,coarse_class) VALUES ('carrier_portal','productive')")
        self.conn.execute("INSERT INTO taxonomy_rule(match_type,pattern,sub_category,priority) "
                          "VALUES ('domain','newcarrier.example.com','carrier_portal',50)")
        self.conn.commit()

        L2, e2, _ = rollup.build_ledger(self.conn, self.ufk, self.day)
        subs_after = {t["sub"] for t in e2["top"]}
        self.assertIn("carrier_portal", subs_after)
        self.assertNotIn("uncategorized", subs_after)


class TestRetention(unittest.TestCase):
    def test_full_url_nulled_at_14d(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = db.connect(path)
        db.init_db(conn)
        mfk = db.resolve_machine(conn, "ws1", auto_provision=True, hostname="WS1")
        ufk = db.resolve_user(conn, mfk, "sam", auto_provision=True)
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)).isoformat()
        conn.execute(
            "INSERT INTO activity_event(machine_fk,user_fk,client_event_id,ts,ts_norm,app,domain,url,"
            "window_title,sub_category,category_code,state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mfk, ufk, "old1", old, old, "chrome", "x.com", "https://x.com/secret/123",
             "secret page", "uncategorized", "neutral", "active"))
        conn.commit()
        db.purge_expired(conn)
        row = conn.execute("SELECT url, window_title, domain FROM activity_event WHERE client_event_id='old1'").fetchone()
        self.assertIsNone(row["url"])           # full URL nulled
        self.assertIsNone(row["window_title"])  # title nulled
        self.assertEqual(row["domain"], "x.com")  # domain (non-sensitive) survives
        conn.close()
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
