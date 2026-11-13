import copy
import json
import unittest

from pay_equity.application import ROOT
from pay_equity.domain import run


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'fixtures/data.json').read_text())
        self.data = {key: [] for key in self.fixture['tables']}

    def test_fixture_is_deterministic_and_not_mutated(self):
        original = copy.deepcopy(self.fixture['tables'])
        options = {'cutoff': self.fixture['meta']['default_cutoff'], 'group': ''}
        first = run(self.fixture['tables'], options)
        second = run(self.fixture['tables'], options)
        self.assertEqual(first, second)
        self.assertEqual(original, self.fixture['tables'])
        self.assertTrue(first['rows'])
        json.dumps(first, allow_nan=False)

    def sample(self):
        self.data['employees'] = [dict(employee='e', job='j', group='A', region='甲', tenure=2, fte=.5)]
        self.data['compensation'] = [dict(employee='e', year=2026, fixed=100, bonus=0, equity_units=10)]
        self.data['valuations'] = [dict(year=2026, price=2)]
        self.data['bands'] = [dict(job='j', lower=150, upper=250)]

    def test_components_and_fte(self):
        self.sample()
        row = run(self.data, {'cutoff': 2026, 'group': ''})['rows'][0]
        self.assertEqual(row['total'], 120)
        self.assertEqual(row['fulltime_fixed'], 200)
        self.assertTrue(row['within_band'])

    def test_unknown_bonus_is_not_zero(self):
        self.sample()
        self.data['compensation'][0]['bonus'] = None
        result = run(self.data, {'cutoff': 2026, 'group': ''})
        self.assertIsNone(result['rows'][0]['total'])
        self.assertEqual(result['summary']['完整薪酬人数'], 0)

    def test_missing_valuation_and_invalid_hours_fail(self):
        self.sample()
        self.data['valuations'] = []
        with self.assertRaises(ValueError):
            run(self.data, {'cutoff': 2026, 'group': ''})
        self.sample()
        self.data['employees'][0]['fte'] = 0
        with self.assertRaises(ValueError):
            run(self.data, {'cutoff': 2026, 'group': ''})
