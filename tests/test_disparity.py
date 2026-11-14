import copy
import json
import unittest

from pay_equity.application import ROOT
from pay_equity.disparity import (
    METRICS,
    PRIMARY_SPEC,
    analyze_disparity,
    build_rows,
    decompose,
    fit_regression,
    tenure_band,
)
from pay_equity.domain import run


def fixture_rows():
    data = json.loads((ROOT / 'fixtures/data.json').read_text())['tables']
    return data, build_rows(data, 2026)


class SemanticsTests(unittest.TestCase):
    def test_bonus_null_is_not_zero_and_zero_stays(self):
        data, rows = fixture_rows()
        metric = METRICS['bonus_declared']
        eligible = [row for row in rows if metric.eligible(row)]
        self.assertEqual(len(eligible), len(rows) - sum(row['bonus'] is None for row in rows))
        self.assertTrue(any(row['bonus'] == 0 for row in eligible))
        self.assertTrue(all(row['bonus'] is not None for row in eligible))

    def test_equity_uses_selected_year_price(self):
        data = json.loads((ROOT / 'fixtures/data.json').read_text())['tables']
        prices = {row['year']: row['price'] for row in data['valuations']}
        old = {row['employee']: row for row in build_rows(data, 2025)}
        new = {row['employee']: row for row in build_rows(data, 2026)}
        units = {row['employee']: row['equity_units'] for row in data['compensation'] if row['year'] == 2026}
        for employee, units_value in units.items():
            self.assertAlmostEqual(new[employee]['equity'], units_value * prices[2026])
        shared = [employee for employee in units if units[employee]]
        self.assertTrue(shared)
        self.assertNotEqual(old[shared[0]]['equity'], new[shared[0]]['equity'])

    def test_missing_valuation_fails_only_when_records_exist(self):
        data, _ = fixture_rows()
        empty = {key: [] for key in data}
        self.assertEqual(run(empty, {'cutoff': 2026, 'group': ''})['rows'], [])
        data['valuations'] = []
        with self.assertRaises(ValueError):
            run(data, {'cutoff': 2026, 'group': ''})

    def test_tenure_band_is_explicit(self):
        self.assertEqual(tenure_band(1, 10), '0-9')
        self.assertEqual(tenure_band(11, 10), '10-19')


class DecompositionTests(unittest.TestCase):
    def setUp(self):
        self.data, self.rows = fixture_rows()
        self.metric = METRICS['fixed_fulltime']

    def test_primary_decomposition_is_exact(self):
        result = decompose(self.rows, 'group', 'A', 'B', self.metric, PRIMARY_SPEC)
        self.assertTrue(result['comparable'])
        self.assertAlmostEqual(
            result['matched_raw_difference'],
            result['within_difference'] + result['composition_difference'],
            places=2,
        )
        self.assertAlmostEqual(
            result['raw_difference'],
            result['matched_raw_difference'] + result['unmatched_difference'],
            places=2,
        )
        self.assertGreater(result['shared_cell_count'], 0)

    def test_no_shared_job_yields_no_adjusted_gap(self):
        result = decompose(self.rows, 'group', 'A', 'C', self.metric, PRIMARY_SPEC)
        self.assertFalse(result['comparable'])
        self.assertIsNone(result['within_difference'])
        self.assertIsNone(result['composition_difference'])
        self.assertEqual(result['shared_cell_count'], 0)
        self.assertTrue(result['reason'])
        self.assertIsNotNone(result['raw_difference'])

    def test_region_dimension_has_no_shared_jobs(self):
        result = decompose(self.rows, 'region', '上海', '成都', self.metric, ('job', 'fte'))
        self.assertFalse(result['comparable'])
        self.assertIsNone(result['within_difference'])

    def test_disjoint_tenure_ranges_block_band_matching(self):
        result = decompose(self.rows, 'group', 'A', 'B', self.metric, ('job', 'fte', 'tenure_band'))
        self.assertFalse(result['comparable'])
        self.assertFalse(result['tenure_ranges']['overlap'])

    def test_matching_dimension_cannot_control_itself(self):
        result = decompose(self.rows, 'group', 'A', 'B', self.metric, ('job', 'group'))
        self.assertNotIn('group', result['fields'])

    def test_each_group_analysis_reports_both_pairs(self):
        analysis = analyze_disparity(self.rows, 'group', 'A', self.metric)
        self.assertEqual([item['group'] for item in analysis['comparisons']], ['B', 'C'])
        self.assertEqual(analysis['comparable_count'], 1)
        self.assertEqual(analysis['incomparable_count'], 1)

    def test_sensitivity_includes_refusals(self):
        analysis = analyze_disparity(self.rows, 'group', 'A', self.metric)
        pair = analysis['comparisons'][0]
        specs = {spec['id']: spec for spec in pair['sensitivity']['specs']}
        self.assertTrue(specs['job_fte_region']['comparable'])
        self.assertFalse(specs['job_fte_region_tenure_band']['comparable'])
        self.assertIsNone(specs['job_fte_region_tenure_band']['within_difference'])

    def test_regression_drops_collinear_controls(self):
        matched = [
            row for row in self.rows
            if row['group'] in ('A', 'B')
        ]
        fit = fit_regression(matched, 'group', 'A', 'B', self.metric, ('job', 'fte', 'region', 'tenure'))
        self.assertTrue(fit['available'])
        self.assertIn('地区', fit['controls_omitted'])
        self.assertIn('任职时间', fit['controls_omitted'])
        self.assertIsNotNone(fit['adjusted_difference'])

    def test_bonus_null_excluded_from_bonus_sample(self):
        analysis = analyze_disparity(self.rows, 'group', 'A', METRICS['bonus_declared'])
        primary = analysis['comparisons'][0]['primary']
        expected = sum(1 for row in self.rows if row['group'] == 'A' and row['bonus'] is not None)
        self.assertEqual(primary['coverage']['A']['eligible'], expected)
        self.assertLess(primary['coverage']['A']['eligible'], 40)

    def test_empty_scope_is_safe(self):
        empty = build_rows({key: [] for key in self.data}, 2026)
        self.assertEqual(empty, [])
        first = run({key: [] for key in self.data}, {'cutoff': 2026, 'group': ''})
        self.assertIsNone(first.get('disparity'))


class DeterminismTests(unittest.TestCase):
    def test_analysis_is_deterministic_and_does_not_mutate(self):
        data, _ = fixture_rows()
        original = copy.deepcopy(data)
        options = {'cutoff': 2026, 'group': '', 'dimension': 'group', 'baseline': 'A', 'metric': 'fixed_fulltime', 'tenure_width': 10}
        first = run(data, options)
        second = run(data, options)
        self.assertEqual(first, second)
        self.assertEqual(original, data)
        json.dumps(first, allow_nan=False)
