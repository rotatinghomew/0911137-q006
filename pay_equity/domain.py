from collections import Counter, defaultdict
import math
from statistics import mean, median


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('数值必须为有限实数')
    return value


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


def output(rows, summary, notes):
    return {'rows': rows, 'summary': summary, 'notes': notes}


def run(data, options):
    year, group = options['cutoff'], options['group']
    employees = {row['employee']: row for row in data['employees'] if selected(row['region'], group)}
    prices = {row['year']: number(row['price']) for row in data['valuations']}
    bands = {row['job']: row for row in data['bands']}
    rows = []
    for compensation in data['compensation']:
        employee = employees.get(compensation['employee'])
        if compensation['year'] != year or employee is None:
            continue
        fte = number(employee['fte'])
        if not 0 < fte <= 1:
            raise ValueError('工时比例须在零到一之间')
        if year not in prices:
            raise ValueError('该年缺少统一股权估值')
        equity = number(compensation['equity_units']) * prices[year]
        fixed = number(compensation['fixed'])
        bonus = compensation['bonus']
        total = fixed + number(bonus) + equity if bonus is not None else None
        band = bands.get(employee['job'])
        rows.append(dict(employee=employee['employee'], job=employee['job'], group=employee['group'], region=employee['region'], fte=fte, fixed=fixed, fulltime_fixed=fixed / fte, bonus=bonus, equity=equity, total=total, within_band=band['lower'] <= fixed / fte <= band['upper'] if band else None))
    complete = [row for row in rows if row['total'] is not None]
    return output(rows, {'员工数': len(rows), '完整薪酬人数': len(complete), '完整记录总额': round(sum(row['total'] for row in complete), 2)}, ['缺报奖金不计为零；本页为本地明细，不能直接作为外发评审材料。'])
