const statusNode = document.querySelector('#status');
const filters = document.querySelector('#filters');
const simulateForm = document.querySelector('#simulate');
let current = null;
let requestGeneration = 0;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function text(tag, value) {
  const node = document.createElement(tag);
  node.textContent = typeof value === 'object' ? JSON.stringify(value) : String(value ?? '未知');
  return node;
}

function amount(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value !== 'number') return String(value);
  return value.toLocaleString('zh-CN', {maximumFractionDigits: 2});
}

function table(container, rows) {
  container.replaceChildren();
  if (!rows.length) {
    container.append(text('p', '当前范围没有记录'));
    return;
  }
  const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
  const grid = document.createElement('table');
  const head = document.createElement('tr');
  for (const column of columns) head.append(text('th', column));
  const thead = document.createElement('thead');
  thead.append(head);
  grid.append(thead);
  const body = document.createElement('tbody');
  for (const row of rows) {
    const line = document.createElement('tr');
    for (const column of columns) line.append(text('td', row[column]));
    body.append(line);
  }
  grid.append(body);
  container.append(grid);
}

function keyValueTable(container, rows) {
  container.replaceChildren();
  const grid = document.createElement('table');
  const thead = document.createElement('thead');
  const head = document.createElement('tr');
  head.append(text('th', '项目'), text('th', '值'));
  thead.append(head);
  grid.append(thead);
  const body = document.createElement('tbody');
  for (const [key, value] of rows) {
    const line = document.createElement('tr');
    line.append(text('td', key), text('td', amount(value)));
    body.append(line);
  }
  grid.append(body);
  container.append(grid);
}

function buildOptions(select, values, selected, includeAll = false) {
  select.replaceChildren();
  if (includeAll) {
    const option = text('option', '全部');
    option.value = '';
    select.append(option);
  }
  for (const value of values) {
    const option = text('option', value);
    option.value = value;
    if (value === selected) option.selected = true;
    select.append(option);
  }
}

function renderDisparity(value) {
  const container = document.querySelector('#disparity');
  container.replaceChildren();
  const disparity = value.result.disparity;
  if (!disparity) {
    document.querySelector('#disparity-meta').textContent = '当前范围没有可比记录。';
    return;
  }
  document.querySelector('#disparity-meta').textContent = `${disparity.dimension_label}，参照 ${disparity.baseline}，口径：${disparity.metric.label}。${disparity.metric.definition}`;
  for (const comparison of disparity.comparisons) {
    const block = document.createElement('div');
    block.className = 'pair';
    const title = text('h3', `参照 ${comparison.baseline} 对比 ${comparison.group}`);
    block.append(title);
    const primary = comparison.primary;
    if (!primary.comparable) {
      block.append(text('p', `不可比：${primary.reason}`));
      const note = text('p', `组均值：${comparison.baseline} ${amount(primary.sample[comparison.baseline].mean)}，${comparison.group} ${amount(primary.sample[comparison.group].mean)}；共同支持单元格 0。`);
      block.append(note);
      container.append(block);
      continue;
    }
    keyValueTable(block, [
      ['原始组间差异（参照减对比）', primary.raw_difference],
      ['可比样本内原始差异', primary.matched_raw_difference],
      ['可比单元格内差异', primary.within_difference],
      ['可比单元格构成差异', primary.composition_difference],
      ['未纳入可比单元格的差异', primary.unmatched_difference],
      ['共同支持单元格数', primary.shared_cell_count],
    ]);
    const coverage = text('p', `样本覆盖：${comparison.baseline} ${primary.coverage[comparison.baseline].matched}/${primary.coverage[comparison.baseline].eligible}；${comparison.group} ${primary.coverage[comparison.group].matched}/${primary.coverage[comparison.group].eligible}。原始差异 = 可比样本内差异 + 未纳入部分；可比样本内差异 = 单元格内差异 + 构成差异。`);
    block.append(coverage);
    const tenureRange = primary.tenure_ranges;
    if (tenureRange && (tenureRange[comparison.baseline] || tenureRange[comparison.group])) {
      const parts = [comparison.baseline, comparison.group].map(label => {
        const node = tenureRange[label];
        return node ? `${label} ${node.min}–${node.max}` : `${label} 无记录`;
      });
      block.append(text('p', `任职时间区间：${parts.join('；')}；${tenureRange.overlap ? '有重叠' : '无重叠（任职时间带不能构成可比）'}。`));
    }
    if (primary.skipped_cells?.length) {
      block.append(text('p', `因任职区间不重叠而剔除的单元格 ${primary.skipped_cells.length} 个。`));
    }
    const specs = document.createElement('div');
    specs.append(text('h4', '匹配口径敏感度'));
    const specTable = document.createElement('table');
    specTable.innerHTML = '<thead><tr><th>匹配字段</th><th>可比</th><th>岗位内差异</th><th>构成差异</th><th>单元格</th></tr></thead>';
    const specBody = document.createElement('tbody');
    for (const spec of comparison.sensitivity.specs) {
      const line = document.createElement('tr');
      line.append(
        text('td', spec.field_labels.join('、') || '无匹配'),
        text('td', spec.comparable ? '是' : `否（${spec.reason}）`),
        text('td', amount(spec.within_difference)),
        text('td', amount(spec.composition_difference)),
        text('td', spec.shared_cell_count),
      );
      specBody.append(line);
    }
    specTable.append(specBody);
    specs.append(specTable, text('p', comparison.sensitivity.note));
    block.append(specs);
    const samples = document.createElement('div');
    samples.append(text('h4', '样本选择敏感度'));
    const sampleTable = document.createElement('table');
    sampleTable.innerHTML = '<thead><tr><th>样本</th><th>人数</th><th>可比</th><th>岗位内差异</th><th>说明</th></tr></thead>';
    const sampleBody = document.createElement('tbody');
    for (const sample of comparison.samples.samples) {
      const line = document.createElement('tr');
      line.append(
        text('td', sample.sample),
        text('td', sample.n),
        text('td', sample.comparable ? '是' : '否'),
        text('td', amount(sample.within_difference)),
        text('td', sample.comparable ? `单元格 ${sample.shared_cell_count}` : sample.reason),
      );
      sampleBody.append(line);
    }
    sampleTable.append(sampleBody);
    samples.append(sampleTable, text('p', comparison.samples.note));
    block.append(samples);
    const tenure = document.createElement('div');
    tenure.append(text('h4', '任职时间带宽敏感度'));
    const tenureTable = document.createElement('table');
    tenureTable.innerHTML = '<thead><tr><th>带宽</th><th>可比</th><th>岗位内差异</th><th>单元格</th></tr></thead>';
    const tenureBody = document.createElement('tbody');
    for (const item of comparison.tenure_widths.widths) {
      const line = document.createElement('tr');
      line.append(
        text('td', item.width),
        text('td', item.comparable ? '是' : `否（${item.reason}）`),
        text('td', amount(item.within_difference)),
        text('td', item.shared_cell_count),
      );
      tenureBody.append(line);
    }
    tenureTable.append(tenureBody);
    tenure.append(tenureTable, text('p', comparison.tenure_widths.note));
    block.append(tenure);
    const models = document.createElement('div');
    models.append(text('h4', '模型设定敏感度'));
    const modelTable = document.createElement('table');
    modelTable.innerHTML = '<thead><tr><th>控制项</th><th>样本</th><th>可比岗位内差异</th><th>采用 / 丢弃</th><th>说明</th></tr></thead>';
    const modelBody = document.createElement('tbody');
    for (const model of comparison.models.models) {
      const line = document.createElement('tr');
      line.append(
        text('td', model.model.join('、') || '无'),
        text('td', model.n ?? '—'),
        text('td', amount(model.adjusted_difference)),
        text('td', model.available ? `${(model.controls_used || []).join('、') || '无控制项'}${model.controls_omitted?.length ? `；丢弃：${model.controls_omitted.join('、')}` : ''}` : '—'),
        text('td', model.reason || (model.available ? '' : '未估计')),
      );
      modelBody.append(line);
    }
    modelTable.append(modelBody);
    models.append(modelTable, text('p', comparison.models.note));
    block.append(models);
    if (primary.shared_cells.length) {
      const cells = document.createElement('div');
      cells.append(text('h4', '可比单元格'));
      table(cells, primary.shared_cells.map(cell => ({
        单元格: Object.values(cell.cell).join(' / '),
        合计人数: cell.n_total,
        小样本: cell.small_cell ? '是' : '否',
        [`${comparison.baseline} 均值`]: amount(cell.baseline.mean),
        [`${comparison.group} 均值`]: amount(cell.comparison.mean),
        [`${comparison.baseline} 人数`]: cell.baseline.n,
        [`${comparison.group} 人数`]: cell.comparison.n,
      })));
      block.append(cells);
    }
    container.append(block);
  }
}

function renderSimulation(simulation) {
  const container = document.querySelector('#simulation');
  container.replaceChildren();
  if (!simulation) {
    container.append(text('p', '尚未运行试算。'));
    return;
  }
  const result = simulation.result;
  const heading = text('h3', `${result.scenario.rule_label}（目标 ${result.scope.dimension}：${result.scope.targets.join('、') || '全部'}）`);
  container.append(heading);
  keyValueTable(container, [
    ['预算', result.budget.amount],
    ['实际成本', result.budget.cost],
    ['预算余额', result.budget.remaining],
    ['预算差额', result.budget.shortfall],
    ['请求比例（%）', result.budget.requested_percent],
    ['执行比例（%）', result.budget.effective_percent],
    ['政策调薪成本', result.costs.policy_raise],
    ['薪资下限补齐成本', result.costs.floor_lift],
    ['薪带下限补齐成本', result.costs.band_lift],
    ['薪带上限截断避免额', result.costs.cap_avoided],
    ['仅满足薪资下限成本', result.costs.floor_only],
  ]);
  const scope = text('p', `命中 ${result.scope.n_selected} 人，上调 ${result.scope.n_affected} 人，不变 ${result.scope.n_unchanged} 人。成本恒等式：${result.costs.identity_note}。`);
  container.append(scope);
  const unmet = document.createElement('div');
  unmet.append(text('h4', '未满足的约束'));
  if (result.unmet.length) {
    for (const item of result.unmet) unmet.append(text('p', `${item.label}：${item.detail}${item.amount !== null ? `（金额 ${amount(item.amount)}）` : ''}${item.count !== null ? `（涉及 ${item.count} 人）` : ''}`));
  } else {
    unmet.append(text('p', '当前方案没有未满足的约束。'));
  }
  container.append(unmet);
  if (result.adjustments.length) {
    const adjustments = document.createElement('div');
    adjustments.append(text('h4', '方案调整'));
    for (const item of result.adjustments) adjustments.append(text('p', `${item.label}：${item.detail}`));
    container.append(adjustments);
  }
  const groupTable = document.createElement('div');
  groupTable.append(text('h4', '分组合计成本'));
  table(groupTable, Object.entries(result.by_group).map(([key, value]) => ({组合: key, 人数: value.n, 上调: value.affected, 成本: amount(value.cost), 下限补齐: amount(value.floor_lift_cost), 薪带补齐: amount(value.band_lift_cost)})));
  container.append(groupTable);
  const jobTable = document.createElement('div');
  jobTable.append(text('h4', '岗位合计成本'));
  table(jobTable, Object.entries(result.by_job).map(([key, value]) => ({岗位: key, 人数: value.n, 上调: value.affected, 成本: amount(value.cost), 下限补齐: amount(value.floor_lift_cost), 薪带补齐: amount(value.band_lift_cost)})));
  container.append(jobTable);
  const notices = document.createElement('ul');
  for (const notice of result.notices) notices.append(text('li', notice));
  container.append(notices);
}

function render(value, savedTitle = '') {
  current = value;
  const summary = document.querySelector('#summary');
  summary.replaceChildren();
  for (const [key, amountValue] of Object.entries(value.result.summary)) {
    summary.append(text('dt', key), text('dd', amountValue));
  }
  document.querySelector('#notes').replaceChildren(...value.result.notes.map(note => text('li', note)));
  document.querySelector('#digest').textContent = `资料指纹：${value.digest}`;
  table(document.querySelector('#results'), value.result.rows);
  renderDisparity(value);
  statusNode.textContent = savedTitle ? `正在查看保存的分析：${savedTitle}` : '分析已更新';
  filters.elements.cutoff.value = value.options.cutoff;
  filters.elements.group.value = value.options.group;
  filters.elements.dimension.value = value.options.dimension;
  filters.elements.metric.value = value.options.metric;
  filters.elements.tenure_width.value = value.options.tenure_width;
  const disparity = value.result.disparity;
  buildOptions(filters.elements.baseline, disparity ? disparity.values : [], value.options.baseline);
  document.querySelector('#export-status').textContent = '';
}

function simulationPayload() {
  const payload = {};
  for (const [key, value] of Object.entries(current.options)) payload[key] = value;
  payload.scenario = {
    rule: simulateForm.elements.rule.value,
    percent: Number(simulateForm.elements.percent.value),
    budget: Number(simulateForm.elements.budget.value),
    floor: Number(simulateForm.elements.floor.value),
    dimension: simulateForm.elements.dimension.value,
    targets: Array.from(simulateForm.elements.targets.selectedOptions, option => option.value),
    honor_bands: simulateForm.elements.honor_bands.checked,
    fit_budget: simulateForm.elements.fit_budget.checked,
  };
  return payload;
}

function simulationTargetValues() {
  const field = {employee_group: 'group', region: 'region', job: 'job'}[simulateForm.elements.dimension.value];
  const rows = current?.result.rows || [];
  return Array.from(new Set(rows.map(row => row[field]))).sort();
}

function refreshTargets() {
  const values = simulationTargetValues();
  buildOptions(simulateForm.elements.targets, values, '');
  simulateForm.elements.targets.multiple = true;
}

async function runSimulation() {
  if (!current) return;
  const status = document.querySelector('#export-status');
  try {
    const result = await api('/api/simulate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(simulationPayload())});
    current.simulationScenario = simulationPayload().scenario;
    renderSimulation(result);
    status.textContent = '试算已更新。';
  } catch (error) {
    status.textContent = error.message;
  }
}

async function refresh() {
  const generation = ++requestGeneration;
  const query = new URLSearchParams(new FormData(filters));
  statusNode.textContent = '正在分析';
  try {
    const result = await api(`/api/view?${query}`);
    if (generation === requestGeneration) {
      render(result);
      refreshTargets();
    }
  } catch (error) {
    if (generation === requestGeneration) {
      current = null;
      statusNode.textContent = error.message;
    }
  }
}

async function history() {
  const entries = await api('/api/analyses');
  const list = document.querySelector('#history');
  list.replaceChildren();
  for (const entry of entries) {
    const item = document.createElement('li');
    const button = text('button', entry.title);
    button.type = 'button';
    button.addEventListener('click', async () => {
      const generation = ++requestGeneration;
      try {
        const saved = await api(`/api/analyses/${entry.id}`);
        if (generation === requestGeneration) render(saved, saved.title);
      } catch (error) {
        statusNode.textContent = error.message;
      }
    });
    item.append(button, text('small', entry.created_at));
    list.append(item);
  }
}

async function download(format) {
  const status = document.querySelector('#export-status');
  if (!current) {
    status.textContent = '请先更新分析。';
    return;
  }
  try {
    let identifier = current.id;
    if (!identifier) {
      const saved = await api('/api/analyses', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: '评审导出', options: current.options})});
      identifier = saved.id;
      await history();
    }
    const body = {id: identifier, simulation: current.simulationScenario || null};
    const response = await fetch(`/api/exports?format=${format}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!response.ok) throw new Error((await response.json()).error || '导出失败');
    const payload = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(payload);
    link.download = format === 'csv' ? 'pay-review.csv' : 'pay-review.json';
    document.body.append(link);
    link.click();
    link.remove();
    status.textContent = '导出完成：仅含汇总，不含员工级记录。';
  } catch (error) {
    status.textContent = error.message;
  }
}

filters.addEventListener('submit', event => {
  event.preventDefault();
  refresh();
});

filters.elements.dimension.addEventListener('change', () => {
  filters.elements.baseline.replaceChildren();
});

simulateForm.elements.dimension.addEventListener('change', refreshTargets);

simulateForm.addEventListener('submit', event => {
  event.preventDefault();
  runSimulation();
});

document.querySelector('#save').addEventListener('submit', async event => {
  event.preventDefault();
  if (!current) return;
  try {
    const saved = await api('/api/analyses', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: event.target.elements.title.value, options: current.options})});
    render(saved, saved.title);
    await history();
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

document.querySelector('#export-json').addEventListener('click', () => download('json'));
document.querySelector('#export-csv').addEventListener('click', () => download('csv'));

document.querySelector('#source').addEventListener('click', async () => {
  try {
    const data = await api(`/api/source?table=${encodeURIComponent(document.querySelector('#tables').value)}`);
    table(document.querySelector('#raw'), data.rows);
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

async function start() {
  const catalog = await api('/api/catalog');
  document.querySelector('#description').textContent = catalog.description;
  filters.elements.cutoff.min = catalog.min_cutoff;
  filters.elements.cutoff.max = catalog.max_cutoff;
  filters.elements.cutoff.value = catalog.default_cutoff;
  const groups = [['', '全部'], ...catalog.groups.map(group => [group, group])];
  for (const [key, label] of groups) {
    const option = text('option', label);
    option.value = key;
    filters.elements.group.append(option);
  }
  for (const name of Object.keys(catalog.tables)) {
    const option = text('option', `${name}（${catalog.tables[name]}）`);
    option.value = name;
    document.querySelector('#tables').append(option);
  }
  renderSimulation(null);
  await refresh();
  await history();
}

start().catch(error => { statusNode.textContent = error.message; });
