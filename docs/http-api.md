# 本地接口

- `GET /api/catalog`：资料名称、可选业务范围、业务时点范围、表计数与资料指纹。
- `GET /api/view`：当前摘要与差异分析。可选参数：`cutoff` 整数、`group` 业务范围、`dimension`（`group` 或 `region`）、`baseline` 参照群体、`metric`（`fixed_fulltime`、`bonus_declared`、`equity`、`total_complete`）、`tenure_width`（1–50，默认 10）。未知或越界筛选项返回 400。
- `GET /api/source?table=employees`：按实际资料表名称查询原始记录；不存在的表返回 404。这是员工级本地数据，不应直接外发。
- `POST /api/simulate`：JSON 对象包含分析筛选项与 `scenario`。`scenario` 字段为 `rule`、`percent`、`budget`、`floor`、`dimension`（`employee_group`/`region`/`job`）、`targets`、`honor_bands`、`fit_budget`。返回分项成本、分组合计、未满足约束与方案调整；个人金额只在本地响应中返回，不进入导出。字段或取值无效返回 400。
- `POST /api/analyses`：JSON 对象包含 `title` 与 `options`，重新计算并保存，成功返回 201。`options` 与当前摘要的分析筛选结构相同。
- `GET /api/analyses`：只返回版本目录及资料指纹。
- `GET /api/analyses/{id}`：返回保存的完整结果，不重新计算。
- `POST /api/exports`：JSON 对象包含已保存版本的 `id`，可选 `simulation` 试算方案。返回只含汇总的评审材料；版本不存在返回 404。查询参数 `?format=csv` 返回同内容的 CSV。导出不含员工级记录，并对少于五人的分组做抑制与互补抑制。

API 的 `rows` 是领域行，`summary` 是当前汇总，`notes` 是业务口径提示，`options` 是实际采用的筛选值。差异分析在 `result.disparity` 中。无记录使用空列表，不能把无观测偷偷表示为有效的零值；不可比的群体组合以 `comparable=false` 和 `null` 数值表示，而不是 0。错误 JSON 的 `error` 可直接在页面反馈。

这是本地单用户应用，不包含认证与外网部署设置。默认只监听回环地址，容器中可显式监听所有接口，但宿主机端口建议只绑定回环地址。
