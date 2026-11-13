# 记录样式

以下为当前业务文件中的实际记录，字段口径见 `data-contract.md`；字段标识保持业务接口写法。

### `employees`

字段：`employee`、`fte`、`group`、`job`、`region`、`tenure`。

```json
{
  "employee": "e000",
  "job": "j1",
  "group": "A",
  "region": "成都",
  "tenure": 1,
  "fte": 0.5
}
```

### `compensation`

字段：`bonus`、`employee`、`equity_units`、`fixed`、`year`。

```json
{
  "employee": "e000",
  "year": 2025,
  "fixed": 25297.51,
  "bonus": null,
  "equity_units": 0
}
```

### `bands`

字段：`job`、`lower`、`upper`。

```json
{
  "job": "j1",
  "lower": 40000,
  "upper": 80000
}
```

### `valuations`

字段：`price`、`year`。

```json
{
  "year": 2025,
  "price": 12
}
```
