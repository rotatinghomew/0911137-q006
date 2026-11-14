"""Auditable pay-disparity calculations.

The analysis works with explicit comparable cells. It never extrapolates a
matched estimate across groups that do not share a job level, and it returns
``None`` instead of inventing an adjusted gap when common support is absent.

Sign convention: every difference is ``baseline - comparison group``. A
negative value therefore means the baseline group is paid less.
"""

from collections import defaultdict
from statistics import mean, median


FULL_TIME = 0.999
TENURE_WIDTH = 10
SMALL_CELL = 5
METRIC_IDS = ('fixed_fulltime', 'bonus_declared', 'equity', 'total_complete')


class Metric:
    def __init__(self, identifier, label, definition, value, eligible, missing_note):
        self.id = identifier
        self.label = label
        self.definition = definition
        self.value = value
        self.eligible = eligible
        self.missing_note = missing_note

    def describe(self):
        return {'id': self.id, 'label': self.label, 'definition': self.definition, 'missing_note': self.missing_note}


METRICS = {
    'fixed_fulltime': Metric(
        'fixed_fulltime',
        '固定薪资（全职口径）',
        'fixed 除以 fte，按全职固定薪资比较；薪带也按该口径检查。',
        lambda row: row['fulltime_fixed'],
        lambda row: True,
        '',
    ),
    'bonus_declared': Metric(
        'bonus_declared',
        '奖金（仅已申报）',
        'bonus=null 表示未申报，从样本剔除且不按零计入；bonus=0 是确认无奖金，计入均值。',
        lambda row: row['bonus'],
        lambda row: row['bonus'] is not None,
        '未申报奖金不计为零。',
    ),
    'equity': Metric(
        'equity',
        '股权（当期统一估值）',
        'equity_units 乘分析年度 valuations.price；0 表示确认无股权，计入均值。',
        lambda row: row['equity'],
        lambda row: True,
        '',
    ),
    'total_complete': Metric(
        'total_complete',
        '完整记录总额（实际支付）',
        'fixed+已申报奖金+当期股权，均为实际支付额；跨工时比较须同时匹配工时。',
        lambda row: row['total'],
        lambda row: row['total'] is not None,
        '缺报奖金的记录不参与总额比较，也不按零补足。',
    ),
}

DIMENSIONS = {
    'group': ('group', '合成分组'),
    'region': ('region', '地区'),
}

MATCH_LABELS = {
    'job': '岗位层级',
    'fte': '工时',
    'tenure_band': '任职时间带',
    'region': '地区',
}

PRIMARY_SPEC = ('job', 'fte', 'region')
SENSITIVITY_SPECS = (
    PRIMARY_SPEC,
    ('job', 'fte'),
    ('job',),
    ('job', 'fte', 'region', 'tenure_band'),
)
REGRESSION_MODELS = (
    ('job',),
    ('job', 'fte'),
    ('job', 'fte', 'region'),
    ('job', 'fte', 'region', 'tenure'),
)
TENURE_WIDTHS = (5, 10, 15, 20)

GENERAL_NOTES = [
    '差异分析是描述性口径，不证明因果歧视；没有共享岗位层级的群体不产生“已调整差距”。',
    '奖金未申报与确认零奖金分开处理；股权一律使用所选年度的统一估值单价。',
    '试算是名义固定薪资情景，不是个人补薪指令；统计残差不直接转成个人调整。',
    '员工级记录只在本机浏览；评审导出另行汇总，并抑制少于五人的单元格。',
]


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('数值必须为有限实数')
    if not (-float('inf') < value < float('inf')):
        raise ValueError('数值必须为有限实数')
    return value


def money(value):
    if value is None:
        return None
    result = round(float(value), 2)
    return 0.0 if result == 0 else result


def tenure_band(tenure, width=TENURE_WIDTH):
    start = (int(tenure) // width) * width
    return f'{start}-{start + width - 1}'


def cell_key(row, fields, width=TENURE_WIDTH):
    if 'tenure_band' in fields:
        return tuple(
            tenure_band(row['tenure'], width) if field == 'tenure_band' else row[field]
            for field in fields
        )
    return tuple(row[field] for field in fields)


def field_label(field):
    return MATCH_LABELS[field]


def build_rows(data, cutoff, group='', width=TENURE_WIDTH):
    """Build one comparable row per person for a cutoff and region scope."""
    employees = {
        row['employee']: row
        for row in data['employees']
        if not group or row['region'] == group
    }
    prices = {row['year']: number(row['price']) for row in data['valuations']}
    bands = {row['job']: row for row in data['bands']}
    rows = []
    for compensation in data['compensation']:
        employee = employees.get(compensation['employee'])
        if compensation['year'] != cutoff or employee is None:
            continue
        fte = number(employee['fte'])
        if not 0 < fte <= 1:
            raise ValueError('工时比例须在零到一之间')
        if cutoff not in prices:
            raise ValueError('该年缺少统一股权估值')
        fixed = number(compensation['fixed'])
        fulltime_fixed = fixed / fte
        bonus = compensation['bonus']
        if bonus is not None:
            bonus = number(bonus)
        equity = number(compensation['equity_units']) * prices[cutoff]
        band = bands.get(employee['job'])
        total = fixed + bonus + equity if bonus is not None else None
        rows.append({
            'employee': employee['employee'],
            'job': employee['job'],
            'group': employee['group'],
            'region': employee['region'],
            'tenure': int(employee['tenure']),
            'tenure_band': tenure_band(employee['tenure'], width),
            'fte': fte,
            'fixed': fixed,
            'fulltime_fixed': fulltime_fixed,
            'bonus': bonus,
            'equity': equity,
            'total': total,
            'band_lower': band['lower'] if band else None,
            'band_upper': band['upper'] if band else None,
            'within_band': band['lower'] <= fulltime_fixed <= band['upper'] if band else None,
        })
    rows.sort(key=lambda row: row['employee'])
    return rows


def dimension_value(row, dimension):
    return row[DIMENSIONS[dimension][0]]


def dimension_values(rows, dimension):
    return sorted({dimension_value(row, dimension) for row in rows})


def effective_fields(fields, dimension):
    """A comparison dimension cannot also be a matching control."""
    dimension_field = DIMENSIONS[dimension][0]
    return tuple(field for field in fields if field != dimension_field)


def _series(values):
    return {
        'n': len(values),
        'mean': money(mean(values)),
        'median': money(median(values)),
    }


def _eligible_rows(rows, metric):
    return [row for row in rows if metric.eligible(row)]


def _tenure_ranges(rows, dimension, baseline, comparison):
    ranges = {}
    for label in (baseline, comparison):
        values = [row['tenure'] for row in rows if dimension_value(row, dimension) == label]
        ranges[label] = {'min': min(values), 'max': max(values), 'n': len(values)} if values else None
    if ranges[baseline] and ranges[comparison]:
        ranges['overlap'] = (
            max(ranges[baseline]['min'], ranges[comparison]['min'])
            <= min(ranges[baseline]['max'], ranges[comparison]['max'])
        )
    else:
        ranges['overlap'] = False
    return ranges


def _cell_members(rows, other, fields, width, metric):
    """Collect metric values and tenures per cell and comparison group."""
    cells = defaultdict(lambda: defaultdict(list))
    tenures = defaultdict(lambda: defaultdict(list))
    for row in _eligible_rows(rows, metric):
        key = cell_key(row, fields, width)
        label = dimension_value(row, other)
        cells[key][label].append(metric.value(row))
        tenures[key][label].append(row['tenure'])
    return cells, tenures


def _shared_cells(cells, tenures, baseline, comparison, fields, width, require_tenure_overlap):
    shared = []
    skipped = []
    for key, members in sorted(cells.items(), key=lambda item: tuple(str(part) for part in item[0])):
        if baseline not in members or comparison not in members:
            continue
        cell = dict(zip(fields, key))
        if require_tenure_overlap and 'tenure_band' in fields:
            base_tenure = tenures[key][baseline]
            other_tenure = tenures[key][comparison]
            overlaps = max(min(base_tenure), min(other_tenure)) <= min(max(base_tenure), max(other_tenure))
            if not overlaps:
                skipped.append({
                    'cell': cell,
                    'reason': '两组实际任职时间区间不重叠，仅有相同时间带标签不足以比较。',
                    'n_total': len(base_tenure) + len(other_tenure),
                })
                continue
        small = len(members[baseline]) + len(members[comparison]) < SMALL_CELL
        shared.append({
            'cell': cell,
            'n_total': len(members[baseline]) + len(members[comparison]),
            'small_cell': small,
            'tenure_ranges': {
                baseline: {'min': min(tenures[key][baseline]), 'max': max(tenures[key][baseline])},
                comparison: {'min': min(tenures[key][comparison]), 'max': max(tenures[key][comparison])},
            },
            'baseline': _series(members[baseline]),
            'comparison': _series(members[comparison]),
        })
    return shared, skipped


def _weighted_difference(shared, baseline, comparison):
    total = sum(cell['n_total'] for cell in shared)
    if not total:
        return None
    return sum(
        cell['n_total'] / total
        * (cell['baseline']['mean'] - cell['comparison']['mean'])
        for cell in shared
    )


def decompose(rows, dimension, baseline, comparison, metric, fields, width=TENURE_WIDTH):
    """Decompose the pair gap into matched composition and within-cell parts.

    The decomposition is exact on the common-support sample. People outside
    shared cells are reported separately and are never silently reweighted.
    Tenure cells additionally require the two groups' observed tenure ranges
    to overlap, so a shared band label alone cannot create comparability.
    """
    fields = effective_fields(fields, dimension)
    eligible = _eligible_rows(rows, metric)
    base_values = [metric.value(row) for row in eligible if dimension_value(row, dimension) == baseline]
    other_values = [metric.value(row) for row in eligible if dimension_value(row, dimension) == comparison]
    raw = money(mean(base_values) - mean(other_values)) if base_values and other_values else None
    cells, tenures = _cell_members(rows, dimension, fields, width, metric)
    shared, skipped = _shared_cells(cells, tenures, baseline, comparison, fields, width, True)
    result = {
        'fields': list(fields),
        'field_labels': [field_label(field) for field in fields],
        'groups': [baseline, comparison],
        'baseline': baseline,
        'comparison': comparison,
        'sample': {
            baseline: _series(base_values),
            comparison: _series(other_values),
        },
        'tenure_ranges': _tenure_ranges(rows, dimension, baseline, comparison),
        'raw_difference': raw,
        'matched_raw_difference': None,
        'within_difference': None,
        'composition_difference': None,
        'unmatched_difference': None,
        'shared_cell_count': 0,
        'shared_cells': [],
        'skipped_cells': skipped,
        'coverage': {
            baseline: {'eligible': len(base_values), 'matched': 0},
            comparison: {'eligible': len(other_values), 'matched': 0},
        },
        'weighting': '合并样本量加权；仅使用两组同时出现且任职区间重叠的可比单元格',
    }
    if not shared:
        result['comparable'] = False
        result['reason'] = '所选匹配维度上没有两组同时具备有效共同支持的可比单元格，不能给出调整后差距。'
        return result
    matched_base = [value for cell in shared for value in cells[cell_key_from_cell(cell, fields, width)][baseline]]
    matched_other = [value for cell in shared for value in cells[cell_key_from_cell(cell, fields, width)][comparison]]
    matched_raw = money(mean(matched_base) - mean(matched_other))
    within = money(_weighted_difference(shared, baseline, comparison))
    coverage = {
        baseline: {'eligible': len(base_values), 'matched': len(matched_base)},
        comparison: {'eligible': len(other_values), 'matched': len(matched_other)},
    }
    result.update({
        'comparable': True,
        'reason': '',
        'matched_raw_difference': matched_raw,
        'within_difference': within,
        'composition_difference': money(matched_raw - within),
        'unmatched_difference': money(raw - matched_raw) if raw is not None else None,
        'shared_cell_count': len(shared),
        'shared_cells': shared,
        'coverage': coverage,
    })
    return result


def cell_key_from_cell(cell, fields, width):
    return tuple(cell['cell'][field] for field in fields)


def _matched_rows(rows, dimension, baseline, comparison, metric, fields, width):
    """Rows that belong to cells shared by both groups, with overlap checks."""
    fields = effective_fields(fields, dimension)
    selected = [
        row for row in _eligible_rows(rows, metric)
        if dimension_value(row, dimension) in (baseline, comparison)
    ]
    cells, tenures = _cell_members(rows, dimension, fields, width, metric)
    shared, _ = _shared_cells(cells, tenures, baseline, comparison, fields, width, True)
    keys = {(cell_key_from_cell(cell, fields, width)) for cell in shared}
    return [row for row in selected if cell_key(row, fields, width) in keys]


def _orthonormal_basis(vectors):
    basis = []
    for vector in vectors:
        work = list(vector)
        for unit in basis:
            projection = sum(a * b for a, b in zip(work, unit))
            work = [a - projection * b for a, b in zip(work, unit)]
        norm = sum(a * a for a in work) ** .5
        original = sum(a * a for a in vector) ** .5
        if not original or norm <= 1e-9 * original:
            continue
        basis.append([a / norm for a in work])
    return basis


def _solve(matrix, values):
    size = len(values)
    augmented = [list(row) + [values[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            for item in range(column, size + 1):
                augmented[row][item] -= factor * augmented[column][item]
    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        total = augmented[row][size] - sum(augmented[row][column] * solution[column] for column in range(row + 1, size))
        solution[row] = total / augmented[row][row]
    return solution


def _control_columns(rows, model):
    """Design controls for a model, dropping bases that carry no information."""
    candidates = []
    jobs = sorted({row['job'] for row in rows})
    regions = sorted({row['region'] for row in rows})
    mean_fte = mean(row['fte'] for row in rows)
    mean_tenure = mean(row['tenure'] for row in rows)
    if 'job' in model:
        for job in jobs[1:]:
            candidates.append(('岗位层级', [1.0 if row['job'] == job else 0.0 for row in rows], f'岗位层级={job}'))
    if 'fte' in model:
        candidates.append(('工时', [row['fte'] - mean_fte for row in rows], '工时比例'))
    if 'region' in model:
        for region in regions[1:]:
            candidates.append(('地区', [1.0 if row['region'] == region else 0.0 for row in rows], f'地区={region}'))
    if 'tenure' in model:
        candidates.append(('任职时间', [row['tenure'] - mean_tenure for row in rows], '任职时间（年）'))
    return candidates


def _column_rank(columns):
    return len(_orthonormal_basis(columns))


def fit_regression(rows, dimension, baseline, comparison, metric, model, width=TENURE_WIDTH):
    """Fit a restricted OLS on the common-support sample only.

    The outcome and continuous controls are centred, so the group coefficient
    is the baseline-minus-comparison difference at control means. Controls
    collinear with the retained design (for example region when it is fixed
    by job) are dropped and reported instead of producing an unstable fit.
    """
    eligible = _eligible_rows(rows, metric)
    has_baseline = any(dimension_value(row, dimension) == baseline for row in eligible)
    has_comparison = any(dimension_value(row, dimension) == comparison for row in eligible)
    if not has_baseline or not has_comparison:
        return {'model': list(model), 'available': False, 'reason': '共同支持样本中缺少一个群体。', 'n': len(eligible)}
    outcome = [metric.value(row) for row in eligible]
    outcome_mean = mean(outcome)
    if not any(value != outcome_mean for value in outcome):
        return {'model': list(model), 'available': False, 'reason': '结果变量没有变异。', 'n': len(eligible)}
    working = [value - outcome_mean for value in outcome]
    columns = [[1.0] * len(eligible), [1.0 if dimension_value(row, dimension) == baseline else 0.0 for row in eligible]]
    used = []
    omitted = []
    rank = _column_rank(columns)
    for label, vector, detail in _control_columns(eligible, model):
        candidate = columns + [vector]
        candidate_rank = _column_rank(candidate)
        if candidate_rank == rank:
            if label not in omitted:
                omitted.append(label)
            continue
        columns = candidate
        rank = candidate_rank
        if detail not in used:
            used.append(detail)
    matrix = [[sum(a * b for a, b in zip(left, right)) for right in columns] for left in columns]
    values = [sum(a * b for a, b in zip(column, working)) for column in columns]
    solution = _solve(matrix, values)
    if solution is None:
        return {'model': list(model), 'available': False, 'reason': '设计矩阵秩不足，无法稳定估计。', 'n': len(eligible)}
    return {
        'model': list(model),
        'available': True,
        'reason': '',
        'n': len(eligible),
        'rank': rank,
        'controls_used': used,
        'controls_omitted': omitted,
        'adjusted_difference': money(solution[1]),
    }


def _spec_id(fields):
    return '_'.join(fields) if fields else 'none'


def sensitivity(rows, dimension, baseline, comparison, metric, width=TENURE_WIDTH):
    specs = []
    seen = set()
    for fields in SENSITIVITY_SPECS:
        effective = effective_fields(fields, dimension)
        if effective in seen:
            continue
        seen.add(effective)
        result = decompose(rows, dimension, baseline, comparison, metric, effective, width)
        specs.append({
            'id': _spec_id(effective) or 'none',
            'fields': list(effective),
            'field_labels': [field_label(field) for field in effective],
            'comparable': result['comparable'],
            'reason': result['reason'],
            'within_difference': result['within_difference'],
            'composition_difference': result['composition_difference'],
            'shared_cell_count': result['shared_cell_count'],
            'coverage': result['coverage'],
        })
    estimates = [spec['within_difference'] for spec in specs if spec['comparable'] and spec['within_difference'] is not None]
    return {
        'metric': metric.id,
        'specs': specs,
        'comparable_spec_count': len(estimates),
        'range': {
            'min': min(estimates) if estimates else None,
            'max': max(estimates) if estimates else None,
            'spread': money(max(estimates) - min(estimates)) if estimates else None,
        },
        'note': '各口径的共同支持样本不同，范围只表示口径敏感性，不是置信区间。',
    }


def sample_sensitivity(rows, dimension, baseline, comparison, metric, width=TENURE_WIDTH):
    variants = [('全部工时', rows), ('仅全职（fte=1）', [row for row in rows if row['fte'] >= FULL_TIME])]
    if metric.id == 'bonus_declared':
        variants.append(('已申报且非零奖金', [row for row in rows if row['bonus'] is not None and row['bonus'] > 0]))
    samples = []
    for label, subset in variants:
        result = decompose(subset, dimension, baseline, comparison, metric, PRIMARY_SPEC, width)
        samples.append({
            'sample': label,
            'n': len(subset),
            'comparable': result['comparable'],
            'reason': result['reason'],
            'within_difference': result['within_difference'],
            'composition_difference': result['composition_difference'],
            'shared_cell_count': result['shared_cell_count'],
            'coverage': result['coverage'],
        })
    estimates = [item['within_difference'] for item in samples if item['comparable'] and item['within_difference'] is not None]
    return {
        'metric': metric.id,
        'samples': samples,
        'range': {
            'min': min(estimates) if estimates else None,
            'max': max(estimates) if estimates else None,
            'spread': money(max(estimates) - min(estimates)) if len(estimates) > 1 else None,
        },
        'note': '样本选择变化同时改变人员构成和共同支持范围，不能只当作精度变化。',
    }


def tenure_sensitivity(rows, dimension, baseline, comparison, metric, width=TENURE_WIDTH):
    widths = sorted({*TENURE_WIDTHS, width})
    results = []
    for item in widths:
        result = decompose(rows, dimension, baseline, comparison, metric, PRIMARY_SPEC, item)
        results.append({
            'width': item,
            'comparable': result['comparable'],
            'reason': result['reason'],
            'within_difference': result['within_difference'],
            'shared_cell_count': result['shared_cell_count'],
            'coverage': result['coverage'],
        })
    estimates = [item['within_difference'] for item in results if item['comparable'] and item['within_difference'] is not None]
    return {
        'metric': metric.id,
        'widths': results,
        'range': {
            'min': min(estimates) if estimates else None,
            'max': max(estimates) if estimates else None,
            'spread': money(max(estimates) - min(estimates)) if estimates else None,
        },
        'note': '任职时间带越窄越强调同任职阶段；带宽改变会改变可比单元格和样本覆盖。',
    }


def model_sensitivity(rows, dimension, baseline, comparison, metric, width=TENURE_WIDTH):
    primary = decompose(rows, dimension, baseline, comparison, metric, PRIMARY_SPEC, width)
    if not primary['comparable']:
        return {
            'metric': metric.id,
            'models': [{'id': _spec_id(model), 'model': list(model), 'available': False, 'reason': '主口径没有共同支持，回归同样不估计。'} for model in REGRESSION_MODELS],
            'note': '回归只允许在共同支持样本内拟合；没有岗位重叠时不外推。',
        }
    matched = _matched_rows(rows, dimension, baseline, comparison, metric, PRIMARY_SPEC, width)
    models = []
    for model in REGRESSION_MODELS:
        fit = fit_regression(matched, dimension, baseline, comparison, metric, model, width)
        fit['id'] = _spec_id(model)
        models.append(fit)
    estimates = [item['adjusted_difference'] for item in models if item['available'] and item['adjusted_difference'] is not None]
    return {
        'metric': metric.id,
        'models': models,
        'range': {
            'min': min(estimates) if estimates else None,
            'max': max(estimates) if estimates else None,
            'spread': money(max(estimates) - min(estimates)) if estimates else None,
        },
        'note': '回归仅限主口径共同支持样本；共线控制项会被丢弃并逐项列出。',
    }


def pair_analysis(rows, dimension, baseline, comparison, metric, width=TENURE_WIDTH):
    primary = decompose(rows, dimension, baseline, comparison, metric, PRIMARY_SPEC, width)
    specs = sensitivity(rows, dimension, baseline, comparison, metric, width)
    samples = sample_sensitivity(rows, dimension, baseline, comparison, metric, width)
    tenure = tenure_sensitivity(rows, dimension, baseline, comparison, metric, width)
    models = model_sensitivity(rows, dimension, baseline, comparison, metric, width)
    estimates = [
        *[spec['within_difference'] for spec in specs['specs'] if spec['comparable'] and spec['within_difference'] is not None],
        *[item['adjusted_difference'] for item in models['models'] if item['available'] and item['adjusted_difference'] is not None],
    ]
    return {
        'group': comparison,
        'baseline': baseline,
        'metric': metric.id,
        'primary': primary,
        'sensitivity': specs,
        'samples': samples,
        'tenure_widths': tenure,
        'models': models,
        'model_range': {
            'min': min(estimates) if estimates else None,
            'max': max(estimates) if estimates else None,
            'spread': money(max(estimates) - min(estimates)) if estimates else None,
            'note': '包含匹配口径和回归模型；各模型样本覆盖不同。',
        },
    }


def analyze_disparity(rows, dimension, baseline, metric, width=TENURE_WIDTH):
    values = dimension_values(rows, dimension)
    if baseline not in values:
        raise ValueError('参照群体在当前分析范围不存在')
    comparisons = [
        pair_analysis(rows, dimension, baseline, value, metric, width)
        for value in values if value != baseline
    ]
    return {
        'dimension': dimension,
        'dimension_label': DIMENSIONS[dimension][1],
        'baseline': baseline,
        'values': values,
        'metric': metric.describe(),
        'comparisons': comparisons,
        'comparable_count': sum(1 for item in comparisons if item['primary']['comparable']),
        'incomparable_count': sum(1 for item in comparisons if not item['primary']['comparable']),
        'notes': GENERAL_NOTES,
    }
