"""Pin the offline comparison of bounded health-read scheduling rules (tools/dev)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
import authority_combat_health_scheduling as H  # noqa: E402


class HealthSchedulingComparisonTests(unittest.TestCase):
    def test_current_rule_never_confirms_a_fresh_read_under_back_to_back_hits(self):
        row = H.run('current', readers=1, read_latency=2)
        self.assertEqual((row['fresh'], row['max_stale_ms']), (0, 10000))
        self.assertTrue(row['orders_increasing'])

    def test_aged_hold_bounds_staleness_and_hit_delay_without_reordering(self):
        current = H.run('current', readers=4, read_latency=2)
        row = H.run('aged_2s_500ms_backoff5s', readers=4, read_latency=2)
        self.assertGreater(row['fresh'], 0)
        self.assertLessEqual(row['max_stale_ms'], 3500)
        self.assertLessEqual(row['max_hit_ms'], current['max_hit_ms'] + 500)
        self.assertTrue(row['orders_increasing'])

    def test_backoff_limits_the_cost_of_a_nonresponding_reader(self):
        plain = H.run('aged_2s_500ms', readers=1, read_latency=H.NEVER)
        backoff = H.run('aged_2s_500ms_backoff5s', readers=1, read_latency=H.NEVER)
        self.assertEqual((plain['fresh'], backoff['fresh']), (0, 0))
        self.assertLess(backoff['held_ticks'], plain['held_ticks'])
        self.assertGreater(backoff['hits'], plain['hits'])
        self.assertTrue(backoff['orders_increasing'])


if __name__ == '__main__':
    unittest.main()
