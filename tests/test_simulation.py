import json
import unittest

from pay_equity.application import ROOT
from pay_equity.disparity import build_rows
from pay_equity.export import build_export, csv_rows
from pay_equity.simulation import simulate


def rows():
    return build_rows(json.loads((ROOT / 'fixtures/data.json').read_text())['tables'], 2026)


BASE = {'rule': 'uniform_percent', 'budget': 500000, 'percent': 3, 'floor': 42000, 'dimension': 'employee_group', 'targets': []}


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.rows = rows()

    def test_uniform_raise_cost_matches_fte_weighted_amount(self):
        result = simulate(self.rows, BASE)
        expected = sum(row['fulltime_fixed'] * .03 * row['fte'] for row in self.rows)
        self.assertAlmostEqual(result['costs']['total'], expected, places=2)
        self.assertAlmostEqual(result['budget']['cost'], expected, places=2)
        self.assertEqual(result['unmet'], [])

    def test_floor_is_hard_constraint_and_itemized(self):
        result = simulate(self.rows, {**BASE, 'floor': 60000, 'targets': ['A']})
        self.assertGreater(result['costs']['floor_lift'], 0)
        self.assertAlmostEqual(
            result['costs']['total'],
            result['costs']['policy_raise'] + result['costs']['floor_lift'] + result['costs']['band_lift'] - result['costs']['cap_avoided'],
            places=2,
        )
        threshold = 60000
        for row in self.rows:
            if row['group'] != 'A':
                continue
            if row['fulltime_fixed'] < threshold:
                self.assertGreaterEqual(threshold, row['fulltime_fixed'])

    def test_band_upper_cap_keeps_unsatisfied_floor_visible(self):
        result = simulate(self.rows, {**BASE, 'percent': 50, 'floor': 100000, 'budget': 100})
        ids = {item['id'] for item in result['unmet']}
        self.assertIn('budget', ids)
        self.assertIn('floor_after', ids)
        self.assertIn('floor_infeasible', ids)
        self.assertFalse(result['budget']['within_budget'])
        self.assertGreater(result['budget']['shortfall'], 0)

    def test_unmet_budget_without_automatic_truncation(self):
        result = simulate(self.rows, {**BASE, 'budget': 100000})
        self.assertFalse(result['budget']['within_budget'])
        self.assertGreater(result['budget']['shortfall'], 0)
        self.assertTrue(any(item['id'] == 'budget' for item in result['unmet']))
        # Without fit_budget the stated percentage is applied in full.
        self.assertEqual(result['budget']['effective_percent'], 3)
        self.assertAlmostEqual(result['budget']['cost'], sum(row['fulltime_fixed'] * .03 * row['fte'] for row in self.rows), places=2)

    def test_fit_budget_never_exceeds_budget(self):
        result = simulate(self.rows, {**BASE, 'budget': 100000, 'fit_budget': True})
        self.assertLessEqual(result['budget']['cost'], 100000)
        self.assertLess(result['budget']['effective_percent'], 3)
        self.assertTrue(any(item['id'] == 'reduced_percent' for item in result['adjustments']))

    def test_band_floor_rule_is_zero_when_all_within_band(self):
        result = simulate(self.rows, {**BASE, 'rule': 'band_floor', 'percent': 0, 'budget': 0})
        self.assertEqual(result['costs']['total'], 0)
        self.assertEqual(result['scope']['n_affected'], 0)
        self.assertTrue(any(item['id'] == 'band_lower_after' for item in result['unmet']) is False)

    def test_band_floor_rule_lifts_below_band(self):
        below = dict(self.rows[0])
        deficit = 1000
        below['fulltime_fixed'] = below['band_lower'] - deficit
        below['employee'] = 'z'
        result = simulate([below], {**BASE, 'rule': 'band_floor', 'percent': 0, 'floor': 0, 'budget': 10000000})
        self.assertGreater(result['costs']['total'], 0)
        self.assertEqual(result['scope']['n_affected'], 1)
        self.assertAlmostEqual(result['costs']['total'], deficit * below['fte'], places=2)
        # Under band_floor the lift is the policy itself, not a separate constraint lift.
        self.assertEqual(result['costs']['band_lift'], 0)

    def test_invalid_scenarios_rejected(self):
        for scenario in [
            {'rule': 'unknown'},
            {'rule': 'uniform_percent', 'budget': -1},
            {'rule': 'uniform_percent', 'percent': 101},
            {'rule': 'uniform_percent', 'floor': -1},
            {'rule': 'uniform_percent', 'dimension': 'bad'},
            {'rule': 'uniform_percent', 'targets': 'A'},
            {'rule': 'uniform_percent', 'honor_bands': 'yes'},
            {'rule': 'band_floor', 'fit_budget': True},
            {'rule': 'uniform_percent', 'extra': 1},
        ]:
            with self.assertRaises(ValueError):
                simulate(self.rows, scenario)

    def test_scenario_scope_and_targets(self):
        result = simulate(self.rows, {**BASE, 'dimension': 'job', 'targets': ['j1', 'j2']})
        self.assertEqual(result['scope']['n_selected'], 30)
        self.assertEqual(set(result['by_group']), {'A', 'B'})


class ExportTests(unittest.TestCase):
    def setUp(self):
        analysis = {
            'title': '评审',
            'digest': 'd',
            'options': {'cutoff': 2026, 'group': '', 'dimension': 'group', 'baseline': 'A', 'metric': 'fixed_fulltime', 'tenure_width': 10},
            'result': {
                'summary': {'员工数': 90},
                'notes': ['说明'],
                'rows': [{'employee': 'e000', 'fixed': 1}],
                'disparity': {
                    'dimension': 'group',
                    'dimension_label': '合成分组',
                    'baseline': 'A',
                    'metric': {'id': 'fixed_fulltime', 'label': '固定薪资'},
                    'comparable_count': 1,
                    'incomparable_count': 1,
                    'comparisons': [
                        {
                            'baseline': 'A',
                            'group': 'B',
                            'primary': {
                                'comparable': True,
                                'reason': '',
                                'raw_difference': -695.29,
                                'within_difference': -843.29,
                                'composition_difference': 148.0,
                                'shared_cell_count': 8,
                                'shared_cells': [{'small_cell': False}],
                                'sample': {'A': {'n': 40}, 'B': {'n': 35}},
                                'coverage': {'A': {'eligible': 40, 'matched': 40}, 'B': {'eligible': 35, 'matched': 35}},
                            },
                            'sensitivity': {'range': {'spread': 148.0}},
                            'models': {'range': {'spread': 26.69}},
                        },
                        {
                            'baseline': 'A',
                            'group': 'C',
                            'primary': {
                                'comparable': False,
                                'reason': '没有共同支持',
                                'raw_difference': -130080.61,
                                'within_difference': None,
                                'composition_difference': None,
                                'shared_cell_count': 0,
                                'shared_cells': [],
                                'sample': {'A': {'n': 40}, 'C': {'n': 15}},
                                'coverage': {'A': {'eligible': 40, 'matched': 0}, 'C': {'eligible': 15, 'matched': 0}},
                            },
                            'sensitivity': {'range': {'spread': None}},
                            'models': {'range': {'spread': None}},
                        },
                    ],
                },
            },
        }
        self.analysis = analysis
        self.simulation = simulate(rows(), BASE)

    def test_private_individual_cost_not_in_export(self):
        material = build_export(self.analysis, self.simulation)
        payload = json.dumps(material, ensure_ascii=False)
        self.assertNotIn('"e000"', payload)
        self.assertNotIn('"people"', payload)
        self.assertFalse('by_employee' in material['simulation'])

    def test_small_groups_suppressed_with_complementary_suppression(self):
        small = dict(BASE, dimension='employee_group', targets=[])
        simulation = simulate(rows(), small)
        # Force a sparse group in the published breakdown.
        simulation['by_group']['sparse'] = {'n': 2, 'affected': 2, 'capped_at_upper': 0, 'already_above_upper': 0, 'floor_lift_cost': 0, 'band_lift_cost': 0, 'cost': 10.0}
        material = build_export(self.analysis, simulation)
        for bucket in material['simulation']['by_group'].values():
            self.assertIsNone(bucket['cost'])
            self.assertTrue(bucket['suppressed'])
        self.assertIsNotNone(material['simulation']['costs']['total'])
        self.assertTrue(any(item['key'] == '全部分组' for item in material['withheld']))

    def test_no_small_cell_keeps_costs(self):
        material = build_export(self.analysis, self.simulation)
        for bucket in material['simulation']['by_job'].values():
            self.assertFalse(bucket['suppressed'])
            self.assertIsNotNone(bucket['cost'])

    def test_incomparable_pair_export_keeps_null_and_reason(self):
        material = build_export(self.analysis)
        pair = material['disparity']['comparisons'][1]
        self.assertFalse(pair['comparable'])
        self.assertIsNone(pair['within_difference'])
        self.assertTrue(pair['reason'])

    def test_csv_contains_no_employee_and_no_suppressed_values(self):
        simulation = simulate(rows(), BASE)
        simulation['by_group']['sparse'] = {'n': 1, 'affected': 1, 'capped_at_upper': 0, 'already_above_upper': 0, 'floor_lift_cost': 0, 'band_lift_cost': 0, 'cost': 10.0}
        rows_out = csv_rows(build_export(self.analysis, simulation))
        text = '\n'.join(','.join(str(cell) for cell in row) for row in rows_out)
        self.assertNotIn('e000', text)
        self.assertIn('全部分组', text)
        self.assertIn('已抑制', text)


class DestructionTests(unittest.TestCase):
    def test_analysis_without_disparity_is_rejected(self):
        with self.assertRaises(ValueError):
            build_export({'result': {}})
        with self.assertRaises(ValueError):
            build_export('not a dict')
