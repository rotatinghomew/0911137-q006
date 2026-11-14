"""Constraint-aware raise scenarios.

Scenarios are stated policies (a uniform percentage or a band-floor
remediation), never individual top-ups derived from a statistical residual.
The simulator honours the employee pay floor and existing bands, then reports
every cost component and every constraint it could not satisfy.
"""

from .disparity import money, number


RULES = {
    'uniform_percent': '统一比例调薪',
    'band_floor': '仅补齐低于薪带下限者',
}
TARGET_DIMENSIONS = ('employee_group', 'region', 'job')
DIMENSION_FIELDS = {'employee_group': 'group', 'region': 'region', 'job': 'job'}


class ScenarioError(ValueError):
    pass


def normalize(scenario):
    if not isinstance(scenario, dict):
        raise ScenarioError('试算方案须为对象')
    allowed = {'rule', 'budget', 'percent', 'floor', 'dimension', 'targets', 'honor_bands', 'fit_budget'}
    if set(scenario) - allowed:
        raise ScenarioError('试算方案包含未知字段')
    rule = scenario.get('rule', 'uniform_percent')
    if rule not in RULES:
        raise ScenarioError('未知调薪规则')
    budget = number(scenario.get('budget', 0))
    if budget < 0:
        raise ScenarioError('预算不能为负')
    percent = number(scenario.get('percent', 0))
    if not 0 <= percent <= 100:
        raise ScenarioError('调薪比例须在零到一百之间')
    floor = number(scenario.get('floor', 0))
    if floor < 0:
        raise ScenarioError('员工薪资下限不能为负')
    dimension = scenario.get('dimension', 'employee_group')
    if dimension not in TARGET_DIMENSIONS:
        raise ScenarioError('未知调薪目标维度')
    targets = scenario.get('targets', [])
    if not isinstance(targets, list) or not all(isinstance(item, str) and item for item in targets):
        raise ScenarioError('调薪目标须为非空文本列表')
    honor_bands = scenario.get('honor_bands', True)
    if not isinstance(honor_bands, bool):
        raise ScenarioError('薪带约束开关须为布尔值')
    fit_budget = scenario.get('fit_budget', False)
    if not isinstance(fit_budget, bool):
        raise ScenarioError('预算适配开关须为布尔值')
    if rule == 'band_floor' and fit_budget:
        raise ScenarioError('薪带补齐方案的金额不随比例变化，不能靠降比例适配预算')
    return {
        'rule': rule,
        'rule_label': RULES[rule],
        'budget': budget,
        'percent': percent,
        'floor': floor,
        'dimension': dimension,
        'targets': targets,
        'honor_bands': honor_bands,
        'fit_budget': fit_budget,
    }


def _bucket():
    return {
        'n': 0,
        'affected': 0,
        'capped_at_upper': 0,
        'already_above_upper': 0,
        'floor_lift_cost': 0.0,
        'band_lift_cost': 0.0,
        'cost': 0.0,
    }


def _apply(rows, scenario, percent):
    """Apply the scenario at one percentage; percentages only affect uniform raises.

    Cost identity, in full-time currency multiplied by fte:
    ``total = policy_raise + floor_lift + band_lift - cap_avoided``.
    """
    field = DIMENSION_FIELDS[scenario['dimension']]
    selected = [row for row in rows if not scenario['targets'] or row[field] in scenario['targets']]
    by_group = {}
    by_job = {}
    flags = {
        'no_band': 0,
        'below_floor_before': 0,
        'below_floor_after': 0,
        'below_band_before': 0,
        'below_band_after': 0,
        'capped_at_upper': 0,
        'already_above_upper': 0,
        'band_floor_unavailable': 0,
        'floor_above_upper': 0,
    }
    totals = {'cost': 0.0, 'floor_lift': 0.0, 'band_lift': 0.0, 'policy_cost': 0.0, 'cap_avoided': 0.0}
    people = []
    for row in selected:
        current = row['fulltime_fixed']
        floor = scenario['floor']
        lower = row['band_lower']
        upper = row['band_upper']
        if lower is None:
            flags['no_band'] += 1
        if floor and current < floor:
            flags['below_floor_before'] += 1
        if lower is not None and current < lower:
            flags['below_band_before'] += 1
        if floor and upper is not None and floor > upper:
            flags['floor_above_upper'] += 1

        # Policy amount before the two hard constraints.
        if scenario['rule'] == 'band_floor':
            if lower is None:
                flags['band_floor_unavailable'] += 1
                policy_target = current
            else:
                policy_target = max(current, lower)
        else:
            policy_target = current * (1 + percent / 100)
        policy_lift = max(0.0, policy_target - current)

        floor_lift = 0.0
        if floor and policy_target < floor:
            floor_lift = floor - policy_target
            policy_target = floor

        band_lift = 0.0
        if scenario['rule'] == 'band_floor' and lower is not None and policy_target < lower:
            band_lift = lower - policy_target
            policy_target = lower

        capped_away = 0.0
        already_above = upper is not None and current > upper
        if scenario['honor_bands'] and upper is not None and policy_target > upper:
            if already_above:
                flags['already_above_upper'] += 1
                policy_target = current
                policy_lift = 0.0
            else:
                flags['capped_at_upper'] += 1
                capped_away = policy_target - upper
                policy_target = upper

        if floor and policy_target < floor:
            flags['below_floor_after'] += 1
        if lower is not None and policy_target < lower:
            flags['below_band_after'] += 1

        delta = max(0.0, policy_target - current)
        cash = delta * row['fte']
        floor_cash = floor_lift * row['fte']
        band_cash = band_lift * row['fte']
        policy_cash = policy_lift * row['fte']
        totals['cost'] += cash
        totals['floor_lift'] += floor_cash
        totals['band_lift'] += band_cash
        totals['policy_cost'] += policy_cash
        totals['cap_avoided'] += capped_away * row['fte']

        for mapping, key in ((by_group, row['group']), (by_job, row['job'])):
            entry = mapping.setdefault(key, _bucket())
            entry['n'] += 1
            entry['cost'] += cash
            entry['floor_lift_cost'] += floor_cash
            entry['band_lift_cost'] += band_cash
            entry['capped_at_upper'] += 1 if capped_away else 0
            entry['already_above_upper'] += 1 if already_above else 0
            if delta > 0:
                entry['affected'] += 1

        people.append({
            'employee': row['employee'],
            'group': row['group'],
            'job': row['job'],
            'region': row['region'],
            'fte': row['fte'],
            'current': money(current),
            'proposed': money(policy_target),
            'delta_fulltime': money(delta),
            'cost': money(cash),
            'band_lower': lower,
            'band_upper': upper,
            'capped_at_upper': bool(capped_away),
        })
    return {
        'percent': percent,
        'selected': len(selected),
        'people': people,
        'totals': totals,
        'flags': flags,
        'by_group': by_group,
        'by_job': by_job,
    }


def _breakdown(mapping):
    return {
        key: {
            'n': entry['n'],
            'affected': entry['affected'],
            'capped_at_upper': entry['capped_at_upper'],
            'already_above_upper': entry['already_above_upper'],
            'floor_lift_cost': money(entry['floor_lift_cost']),
            'band_lift_cost': money(entry['band_lift_cost']),
            'cost': money(entry['cost']),
        }
        for key, entry in sorted(mapping.items())
    }


def _unmet(awarded, scenario, floor_cost):
    flags = awarded['flags']
    unmet = []
    if awarded['totals']['cost'] > scenario['budget'] or floor_cost > scenario['budget']:
        unmet.append({
            'id': 'budget',
            'label': '预算上限',
            'detail': '所需固定薪资成本无法在预算内完成；差额保留，个人金额不被自动截断。',
            'amount': money(max(0.0, awarded['totals']['cost'] - scenario['budget'])),
            'count': None,
        })
    if flags['below_floor_after']:
        unmet.append({
            'id': 'floor_after',
            'label': '员工薪资下限',
            'detail': '方案执行后仍有员工低于薪资下限。',
            'amount': None,
            'count': flags['below_floor_after'],
        })
    if floor_cost > scenario['budget']:
        unmet.append({
            'id': 'floor_infeasible',
            'label': '员工薪资下限',
            'detail': '仅满足薪资下限所需成本已经超过预算。',
            'amount': money(floor_cost - scenario['budget']),
            'count': None,
        })
    if flags['below_band_after']:
        unmet.append({
            'id': 'band_lower_after',
            'label': '既有薪带下限',
            'detail': '方案执行后仍有员工低于既有薪带下限；统一比例方案不自动补齐，需另行处理。',
            'amount': None,
            'count': flags['below_band_after'],
        })
    if flags['no_band']:
        unmet.append({
            'id': 'missing_band',
            'label': '既有薪带',
            'detail': '缺少对应岗位薪带，无法检查上下限。',
            'amount': None,
            'count': flags['no_band'],
        })
    if flags['band_floor_unavailable']:
        unmet.append({
            'id': 'band_floor_unavailable',
            'label': '薪带下限',
            'detail': '薪带补齐规则下缺少岗位薪带，无法确定补齐目标。',
            'amount': None,
            'count': flags['band_floor_unavailable'],
        })
    if flags['floor_above_upper'] and scenario['honor_bands']:
        unmet.append({
            'id': 'floor_above_upper',
            'label': '员工薪资下限与薪带上限',
            'detail': '薪资下限高于对应岗位薪带上限，两个硬约束无法同时满足；方案保留两者差额。',
            'amount': None,
            'count': flags['floor_above_upper'],
        })
    return unmet


def _floor_cost(rows, scenario):
    """Cost of lifting everyone in scope to the employee floor, ignoring bands."""
    if not scenario['floor']:
        return 0.0
    field = DIMENSION_FIELDS[scenario['dimension']]
    total = 0.0
    for row in rows:
        if scenario['targets'] and row[field] not in scenario['targets']:
            continue
        if row['fulltime_fixed'] < scenario['floor']:
            total += (scenario['floor'] - row['fulltime_fixed']) * row['fte']
    return total


def simulate(rows, scenario):
    scenario = normalize(scenario)
    floor_cost = _floor_cost(rows, scenario)
    requested = _apply(rows, scenario, scenario['percent'])
    effective_percent = scenario['percent']
    awarded = requested
    if scenario['fit_budget'] and requested['totals']['cost'] > scenario['budget']:
        if floor_cost > scenario['budget']:
            effective_percent = 0.0
            awarded = _apply(rows, scenario, 0.0)
        else:
            low, high = 0.0, scenario['percent']
            for _ in range(60):
                middle = (low + high) / 2
                if _apply(rows, scenario, middle)['totals']['cost'] <= scenario['budget']:
                    low = middle
                else:
                    high = middle
            # Truncate rather than round, then back off cent by cent until the
            # reported cost truly fits the budget.
            effective_percent = int(low * 1e6) / 1e6
            awarded = _apply(rows, scenario, effective_percent)
            while awarded['totals']['cost'] > scenario['budget'] and effective_percent > 0:
                effective_percent = max(0.0, round(effective_percent - 1e-6, 6))
                awarded = _apply(rows, scenario, effective_percent)
    cost = awarded['totals']['cost']
    delta = money(scenario['budget'] - cost)
    adjustments = []
    if effective_percent < scenario['percent']:
        adjustments.append({
            'id': 'reduced_percent',
            'label': '调薪比例',
            'detail': '为满足预算，试算比例由请求值下调。' if cost <= scenario['budget'] else '已把比例降至零，硬约束成本仍超过预算；差额保留。',
            'requested': scenario['percent'],
            'effective': effective_percent,
            'within_budget': cost <= scenario['budget'],
        })
    if awarded['flags']['capped_at_upper']:
        adjustments.append({
            'id': 'capped_at_upper',
            'label': '薪带上限',
            'detail': '部分调薪被薪带上限截断，成本按截断后金额计。',
            'count': awarded['flags']['capped_at_upper'],
            'amount_avoided': money(awarded['totals']['cap_avoided']),
        })
    if awarded['flags']['already_above_upper']:
        adjustments.append({
            'id': 'already_above_upper',
            'label': '薪带上限',
            'detail': '方案执行前已高于薪带上限的员工不再上调，也未下调。',
            'count': awarded['flags']['already_above_upper'],
        })
    return {
        'scenario': scenario,
        'scope': {
            'dimension': scenario['dimension'],
            'targets': scenario['targets'],
            'n_selected': awarded['selected'],
            'n_affected': sum(1 for item in awarded['people'] if item['delta_fulltime'] > 0),
            'n_unchanged': sum(1 for item in awarded['people'] if item['delta_fulltime'] == 0),
        },
        'budget': {
            'amount': money(scenario['budget']),
            'cost': money(cost),
            'remaining': delta if delta >= 0 else 0.0,
            'shortfall': money(max(0.0, cost - scenario['budget'])),
            'within_budget': cost <= scenario['budget'],
            'requested_percent': scenario['percent'],
            'effective_percent': effective_percent,
        },
        'costs': {
            'total': money(cost),
            'policy_raise': money(awarded['totals']['policy_cost']),
            'floor_lift': money(awarded['totals']['floor_lift']),
            'band_lift': money(awarded['totals']['band_lift']),
            'cap_avoided': money(awarded['totals']['cap_avoided']),
            'floor_only': money(floor_cost),
            'identity_note': '总额 = 政策调薪 + 下限补齐 + 薪带补齐 - 薪带上限截断',
        },
        'by_group': _breakdown(awarded['by_group']),
        'by_job': _breakdown(awarded['by_job']),
        'unmet': _unmet(awarded, scenario, floor_cost),
        'adjustments': adjustments,
        'data_completeness': {
            'bonus_missing': sum(1 for row in rows if row['bonus'] is None),
            'bonus_zero': sum(1 for row in rows if row['bonus'] == 0),
            'equity_zero': sum(1 for row in rows if row['equity'] == 0),
        },
        'notices': [
            '试算结果是名义固定薪资成本情景，不含奖金与股权，也不是个人调薪指令。',
            '薪资下限与薪带是硬约束；预算不足时保留差额，不擅自截断个人金额。',
            '统计差异不进入本试算的个人分配；定向补齐须另立明确政策并单独复核。',
            '本接口只在本地返回汇总与个人明细；评审导出另行抑制稀疏单元格。',
        ],
    }
