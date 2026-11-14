from .disparity import (
    DIMENSIONS,
    METRICS,
    GENERAL_NOTES,
    analyze_disparity,
    build_rows,
    number,
)


def selected(value, group):
    return not group or value == group


def latest(rows, key, revision, visible, cutoff):
    result = {}
    for row in rows:
        if row[visible] > cutoff:
            continue
        previous = result.get(row[key])
        if previous is None or row[revision] > previous[revision]:
            result[row[key]] = row
    return list(result.values())


def output(rows, summary, notes, disparity=None):
    value = {'rows': rows, 'summary': summary, 'notes': notes}
    if disparity is not None:
        value['disparity'] = disparity
    return value


def resolve(data, options):
    """Validate analysis options and select the focus comparison."""
    cutoff, group = options['cutoff'], options['group']
    dimension = options.get('dimension', 'group')
    if dimension not in DIMENSIONS:
        raise ValueError('未知比较维度')
    metric_id = options.get('metric', 'fixed_fulltime')
    if metric_id not in METRICS:
        raise ValueError('未知薪酬口径')
    width = options.get('tenure_width', 10)
    if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 50:
        raise ValueError('任职时间带宽度须为一到五十的整数')
    prices = {row['year']: number(row['price']) for row in data['valuations']}
    if cutoff not in prices and any(row['year'] == cutoff for row in data['compensation']):
        raise ValueError('该年缺少统一股权估值')
    rows = build_rows(data, cutoff, group, width)
    values = sorted({row[DIMENSIONS[dimension][0]] for row in rows})
    baseline = options.get('baseline', '')
    if not baseline:
        if not values:
            baseline = ''
        else:
            baseline = values[0]
    elif baseline not in values:
        raise ValueError('参照群体在当前分析范围不存在')
    return {
        'cutoff': cutoff,
        'group': group,
        'dimension': dimension,
        'baseline': baseline,
        'metric': metric_id,
        'tenure_width': width,
    }, rows, values


def run(data, options):
    resolved, rows, values = resolve(data, options)
    metric = METRICS[resolved['metric']]
    complete = [row for row in rows if row['total'] is not None]
    summary = {
        '员工数': len(rows),
        '完整薪酬人数': len(complete),
        '完整记录总额': round(sum(row['total'] for row in complete), 2),
        '记录奖金未申报': sum(1 for row in rows if row['bonus'] is None),
        '记录奖金确认为零': sum(1 for row in rows if row['bonus'] == 0),
    }
    notes = [
        '缺报奖金不计为零；本页为本地明细，不能直接作为外发评审材料。',
        *GENERAL_NOTES,
    ]
    disparity = None
    if values and resolved['baseline'] in values:
        disparity = analyze_disparity(
            rows,
            resolved['dimension'],
            resolved['baseline'],
            metric,
            resolved['tenure_width'],
        )
    return output(rows, summary, notes, disparity)
