"""Aggregate review exports.

The export never contains employee identifiers or employee-level rows. Every
segment smaller than ``SMALL_CELL`` people is withheld, and complementary
suppression prevents recovering a hidden value by subtracting published
figures from a published total.
"""

from .disparity import SMALL_CELL


SUPPRESSED = '少于五人的分组已抑制'


def _suppress_breakdown(breakdown):
    """Withhold small segments and apply complementary suppression.

    If any segment is below the threshold, publishing the remaining segment
    costs would let a reader subtract to recover the hidden value, so every
    per-segment cost in that breakdown is withheld. The aggregate total is
    still published by the caller.
    """
    published = {}
    small_keys = []
    for key, bucket in sorted(breakdown.items()):
        suppressed = bucket['n'] < SMALL_CELL
        if suppressed:
            small_keys.append(key)
        published[key] = {
            'n': None if suppressed else bucket['n'],
            'n_bucket': '<5' if suppressed else str(bucket['n']),
            'affected': None if suppressed else bucket.get('affected'),
            'cost': None if suppressed else bucket['cost'],
            'suppressed': suppressed,
        }
    withheld = []
    if small_keys:
        for key in small_keys:
            withheld.append({'key': key, 'reason': SUPPRESSED})
        withheld.append({
            'key': '全部分组',
            'reason': '存在少于五人的分组，为防反推，其余分组成本一并抑制；仅保留总成本。',
        })
        for node in published.values():
            node['cost'] = None
            node['suppressed'] = True
    return published, withheld


def _pair_row(comparison):
    primary = comparison['primary']
    row = {
        'baseline': comparison['baseline'],
        'comparison': comparison['group'],
        'comparable': primary['comparable'],
        'reason': primary['reason'],
        'raw_difference': primary['raw_difference'],
        'within_difference': primary['within_difference'],
        'composition_difference': primary['composition_difference'],
        'shared_cell_count': primary['shared_cell_count'],
        'sensitivity_spread': comparison['sensitivity']['range']['spread'],
        'model_spread': comparison['models'].get('range', {}).get('spread'),
        'suppressed': False,
        'coverage': None,
        'coverage_note': '',
    }
    tiny_cell = any(cell.get('small_cell') for cell in primary.get('shared_cells', []))
    tiny_sample = False
    for node in primary['sample'].values():
        if (node or {}).get('n', 0) < SMALL_CELL:
            tiny_sample = True
    if tiny_sample or tiny_cell:
        row.update({
            'raw_difference': None,
            'within_difference': None,
            'composition_difference': None,
            'sensitivity_spread': None,
            'model_spread': None,
            'suppressed': True,
            'suppression_note': '该比较依赖少于五人的分组或可比单元格，不发布任何差异数值。',
        })
        return row
    if primary['comparable']:
        row['coverage'] = primary['coverage']
        if any(node['matched'] < SMALL_CELL for node in primary['coverage'].values()):
            row['coverage'] = None
            row['coverage_note'] = '共同支持人数不足五人，不发布覆盖明细。'
    else:
        row['coverage_note'] = '没有共同支持单元格。'
    return row


def build_export(analysis, simulation=None):
    if not isinstance(analysis, dict) or not isinstance(analysis.get('result'), dict):
        raise ValueError('缺少可导出的分析结果')
    result = analysis['result']
    missing = [key for key in ('summary', 'notes') if key not in result]
    if missing:
        raise ValueError('分析结果缺少字段：' + '、'.join(missing))
    disparity = result.get('disparity')
    material = {
        'title': analysis.get('title') or '薪酬评审汇总',
        'digest': analysis.get('digest'),
        'options': analysis.get('options'),
        'generated_from': '本地分析版本；不含员工级记录',
        'summary': result['summary'],
        'notes': result['notes'],
        'small_cell_threshold': SMALL_CELL,
        'suppression_rule': f'人数或分项人数少于 {SMALL_CELL} 的分组不发布数值，并做互补抑制。',
        'disparity': None,
        'simulation': None,
        'withheld': [],
    }
    if disparity:
        material['disparity'] = {
            'dimension': disparity['dimension'],
            'dimension_label': disparity['dimension_label'],
            'baseline': disparity['baseline'],
            'metric': disparity['metric'],
            'comparisons': [_pair_row(item) for item in disparity['comparisons']],
            'counts': {
                'comparable': disparity['comparable_count'],
                'incomparable': disparity['incomparable_count'],
            },
        }
    if simulation:
        by_group, group_withheld = _suppress_breakdown(simulation['by_group'])
        by_job, job_withheld = _suppress_breakdown(simulation['by_job'])
        material['simulation'] = {
            'scenario': dict(simulation['scenario']),
            'scope': simulation['scope'],
            'budget': simulation['budget'],
            'costs': simulation['costs'],
            'by_group': by_group,
            'by_job': by_job,
            'unmet': simulation['unmet'],
            'adjustments': simulation['adjustments'],
            'notices': simulation['notices'],
        }
        material['withheld'] = [
            {'level': 'group', **item} for item in group_withheld
        ] + [
            {'level': 'job', **item} for item in job_withheld
        ]
    return material


COST_LABELS = {
    'total': '名义固定薪资总成本',
    'policy_raise': '政策调薪成本',
    'floor_lift': '员工薪资下限补齐成本',
    'band_lift': '薪带下限补齐成本',
    'cap_avoided': '薪带上限截断避免额',
    'floor_only': '仅满足员工薪资下限成本',
}


def csv_rows(material):
    """Flatten the review material into labelled aggregate CSV rows."""
    rows = [['项目', '参照/范围', '对象', '口径', '值', '说明']]
    for key, value in material['summary'].items():
        rows.append(['摘要', '', '', key, value, ''])
    if material['disparity']:
        disparity = material['disparity']
        for pair in disparity['comparisons']:
            rows.append(['差异', pair['baseline'], pair['comparison'], '可比', pair['comparable'], pair['reason']])
            if pair['suppressed']:
                rows.append(['差异', pair['baseline'], pair['comparison'], '数值', '', pair.get('suppression_note', '')])
            else:
                for field, label in [('raw_difference', '原始差异'), ('within_difference', '可比岗位内差异'), ('composition_difference', '构成差异')]:
                    rows.append(['差异', pair['baseline'], pair['comparison'], label, pair[field], f'指标：{disparity["metric"]["label"]}'])
                rows.append(['敏感度', pair['baseline'], pair['comparison'], '口径范围', pair['sensitivity_spread'], '仅表示口径敏感性，不是置信区间'])
                rows.append(['敏感度', pair['baseline'], pair['comparison'], '模型范围', pair['model_spread'], '仅在共同支持样本内'])
            if pair['coverage'] is None:
                rows.append(['覆盖', pair['baseline'], pair['comparison'], '共同支持', '', pair['coverage_note']])
    if material['simulation']:
        simulation = material['simulation']
        rule = simulation['scenario']['rule_label']
        for key, label in COST_LABELS.items():
            rows.append(['试算成本', rule, '', label, simulation['costs'].get(key), ''])
        rows.append(['试算预算', rule, '', '预算', simulation['budget']['amount'], ''])
        rows.append(['试算预算', rule, '', '实际成本', simulation['budget']['cost'], ''])
        rows.append(['试算预算', rule, '', '预算差额', simulation['budget']['shortfall'], ''])
        rows.append(['试算预算', rule, '', '执行比例（%）', simulation['budget']['effective_percent'], ''])
        for level, breakdown in (('group', simulation['by_group']), ('job', simulation['by_job'])):
            for segment, bucket in breakdown.items():
                rows.append(['试算分组', level, segment, '成本', bucket['cost'], '已抑制' if bucket['suppressed'] else ''])
        for issue in simulation['unmet']:
            value = issue['amount'] if issue.get('amount') is not None else issue.get('count')
            rows.append(['未满足约束', issue['label'], '', issue['id'], value, issue['detail']])
    for item in material['withheld']:
        rows.append(['已抑制', item['level'], item['key'], '', '', item['reason']])
    return rows
