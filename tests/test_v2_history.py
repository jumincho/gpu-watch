import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest import mock

import server


class V2HistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = server.Store(Path(temp.name) / "test.sqlite3")

    @staticmethod
    def process(pid, user, generation="1000"):
        return {"pid": pid, "user": user, "started": "same-second", "start_identity": generation}

    def test_known_owner_survives_a_new_process_joining(self):
        previous = [self.process(10, "alice")]
        current = [self.process(10, ""), self.process(20, "bob", "2000")]
        self.assertTrue(server.carry_verified_process_owners(previous, current))
        self.assertEqual(server.confirmed_process_users(current), ["alice", "bob"])

    def test_known_owner_survives_a_sibling_process_departing(self):
        previous = [self.process(10, "alice"), self.process(20, "bob", "2000")]
        current = [self.process(10, "")]
        self.assertTrue(server.carry_verified_process_owners(previous, current))
        self.assertEqual(current[0]["user"], "alice")

    def test_reused_or_unverifiable_pid_never_inherits_owner(self):
        previous = [self.process(10, "alice")]
        for current in [[self.process(10, "", "1001")],
                        [{"pid": 10, "user": "", "started": "same-second"}],
                        [self.process(20, "", "2000")]]:
            with self.subTest(current=current):
                self.assertFalse(server.carry_verified_process_owners(previous, current))
                self.assertEqual(server.confirmed_process_users(current), [])

    def test_ambiguous_duplicate_pid_never_inherits_owner(self):
        current = [self.process(10, "")]
        self.assertFalse(server.carry_verified_process_owners(
            [self.process(10, "alice"), self.process(10, "bob")], current))
        duplicate = [self.process(10, ""), self.process(10, "")]
        self.assertFalse(server.carry_verified_process_owners([self.process(10, "alice")], duplicate))

    def test_changing_cohort_preserves_distinct_user_time_attribution(self):
        base = {"index": 0, "uuid": "GPU-test", "name": "GPU", "memory_total": 8192, "memory_used": 4096}
        samples = [(100, [self.process(10, "alice")]),
                   (110, [self.process(10, ""), self.process(20, "bob", "2000")]),
                   (120, [self.process(10, "alice"), self.process(20, "bob", "2000")])]
        for ts, processes in samples:
            with mock.patch("server.now_ts", return_value=ts):
                self.store.update_gpu("test", {**base, "processes": processes}, True, 60)
        with closing(self.store.connect()) as conn:
            users = json.loads(conn.execute("select user_busy_json from gpu_runtime").fetchone()[0])
        self.assertEqual(users, {"alice": 15.0, "bob": 5.0})

    def recent(self, intervals):
        with closing(self.store.connect()) as conn, conn:
            conn.executemany("insert into gpu_usage_interval(host,gpu_index,start_ts,end_ts,users,seconds) values('test',?,?,?,?,?)",
                             [(i, start, end, users, end-start) for i,start,end,users in intervals])
            return self.store._recent_usage_by_host(conn, [], {}, 1500, {
                "poll_interval_seconds": 10, "recent_usage_window_seconds": 1000,
                "activity_policy": {"cold_min_session_seconds": 60}})["test"]

    def test_meaningful_session_split_by_user_at_window_boundary(self):
        usage = self.recent([(0, 430, 490, "alice"), (0, 490, 520, "bob")])
        self.assertEqual(usage["busy_seconds"], 20)
        self.assertEqual(usage["user_seconds"], {"bob": 20})
        self.assertEqual(usage["meaningful_active_seconds"], 20)
        self.assertEqual(usage["last_meaningful_used_ts"], 520)

    def test_meaningful_session_can_cross_gpu_at_window_boundary(self):
        usage = self.recent([(0, 430, 490, "alice"), (1, 485, 520, "bob")])
        self.assertEqual(usage["busy_seconds"], 20)
        self.assertEqual(usage["meaningful_active_seconds"], 20)

    def test_59_second_boundary_trial_still_is_not_meaningful(self):
        usage = self.recent([(0, 470, 490, "alice"), (0, 490, 529, "bob")])
        self.assertEqual(usage["active_seconds"], 29)
        self.assertEqual(usage["meaningful_active_seconds"], 0)
        self.assertIsNone(usage["last_meaningful_used_ts"])

    def test_exact_60_second_boundary_session_is_meaningful(self):
        usage = self.recent([(0, 470, 490, "alice"), (0, 490, 530, "bob")])
        self.assertEqual(usage["meaningful_active_seconds"], 30)

    def test_no_boundary_session_is_invented_across_a_gap(self):
        usage = self.recent([(0, 430, 489, "alice"), (0, 490, 520, "bob")])
        self.assertEqual(usage["meaningful_active_seconds"], 0)

    def test_parallel_sessions_match_an_independent_secondwise_oracle(self):
        import random
        randomizer = random.Random(20260909)
        for case in range(30):
            rows = []
            busy_seconds = 0
            active = [False] * 1500
            for index in range(3):
                cursor = 300
                while cursor < 1500:
                    end = min(1500, cursor + randomizer.randint(1, 80))
                    if randomizer.random() < 0.65:
                        rows.append((index, cursor, end, randomizer.choice(["alice", "bob", None])))
                        busy_seconds += max(0, end - max(500, cursor))
                        active[cursor:end] = [True] * (end - cursor)
                    cursor = end
            meaningful_seconds = 0
            cursor = 0
            while cursor < len(active):
                if not active[cursor]:
                    cursor += 1
                    continue
                end = cursor
                while end < len(active) and active[end]:
                    end += 1
                if end - cursor >= 60:
                    meaningful_seconds += max(0, end - max(500, cursor))
                cursor = end
            with closing(self.store.connect()) as conn, conn:
                conn.execute("delete from gpu_usage_interval")
            actual = self.recent(rows)
            with self.subTest(case=case):
                self.assertEqual(actual["busy_seconds"], busy_seconds)
                self.assertEqual(actual["active_seconds"], sum(active[500:]))
                self.assertEqual(actual["meaningful_active_seconds"], meaningful_seconds)

    def test_internal_observation_events_remain_in_db_but_not_public_pages(self):
        internal = ["observation_gap", "user_change", "observation_lost", "observation_resumed", "observation_error"]
        public = ["busy_start", "free_start", "gpu_down", "gpu_recovered"]
        with closing(self.store.connect()) as conn, conn:
            conn.executemany("insert into events(ts,host,event) values(?,'test',?)", enumerate(internal + public, 100))
        all_public = self.store.events(limit=2)
        next_public = self.store.events(limit=2, offset=all_public["next_offset"])
        self.assertEqual([x["event"] for x in all_public["events"] + next_public["events"]], list(reversed(public)))
        self.assertFalse(next_public["has_more"])
        with closing(self.store.connect()) as conn:
            self.assertEqual(conn.execute("select count(*) from events").fetchone()[0], len(internal+public))

    def test_daily_index_uses_each_gpu_observed_denominator_and_wall_union(self):
        midnight = datetime(2026, 9, 9, tzinfo=server.KST).timestamp()
        with closing(self.store.connect()) as conn, conn:
            for index in (0, 1):
                conn.execute("insert into gpu_runtime(host,gpu_index,active,last_snapshot_json) values('test',?,1,?)", (index, json.dumps({"memory_total": 16384})))
            for index,start,end,used in [(0,0,30,4096),(0,60,120,8192),(1,20,50,2048)]:
                self.store._record_capacity_interval(conn,"test",index,midnight+start,midnight+end,True,used,16384)
            self.store._refresh_daily_vram_rollups(conn, {"hosts":[{"name":"test","lab":"lab"}],"labs":[{"id":"lab"}]}, midnight+120)
            row = conn.execute("select * from lab_daily_index").fetchone()
        self.assertAlmostEqual(row["average_used_gb"], 8.6667, places=4)
        self.assertEqual(row["observed_seconds"], 110)
        self.assertEqual(row["complete"], 0)


if __name__ == "__main__":
    unittest.main()
