# 受控 Text2SQL 查询模板

## 设计原则

科研人员使用自然语言提问；模型只能提取查询参数，不能提交 SQL 文本。后端根据已发布的标准模板、字段字典和权限，选择下列固定 SQL 模板并绑定参数。

所有查询仅访问已发布标准数据；原始文件、待处理记录、审核记录和其他科研人员私有附件不在查询范围内。

## 两种使用入口

- **农业科研助手**：科研人员用自然语言提问。规则能识别时直接填充受控查询参数；识别不充分时，模型只能补充受限 JSON 参数，不能生成或执行 SQL。
- **结构化查询面板**：科研人员自行选择标准模板、展示字段和数值范围。面板不展示 SQL，也不要求填写 SQL；它调用的仍是本文件定义的同一组受控查询模板。

结构化面板支持两个公开模板的全部标准字段：

- `rice_phenotype`：品种名称 + 22 个水稻表型字段。
- `root_phenotype`：材料/品种名称 + 10 个根系表型字段。

数值条件可针对同一字段同时添加“`gte` 不小于”和“`lte` 不超过”，用于构成可追溯的数值区间。

## 模板一：按品种查询水稻表型

适用提问：`田两优9号的株高和千粒重是多少？`

```json
{
  "scope": "rice_phenotype",
  "variety_ids": ["由后端根据品种名或别名解析"],
  "trait_codes": ["plant_height", "thousand_grain_weight"],
  "filters": [],
  "limit": 100
}
```

读取：`variety_basic` + `phenotype_observation`。

## 模板二：按字段查询水稻表型

适用提问：`列出所有已发布品种的千粒重。`

```json
{
  "scope": "rice_phenotype",
  "variety_ids": [],
  "trait_codes": ["thousand_grain_weight"],
  "filters": [],
  "limit": 100
}
```

## 模板三：按多个条件筛选品种

适用提问：`筛选株高不超过100厘米、千粒重大于25克的品种。`

```json
{
  "scope": "rice_phenotype",
  "variety_ids": [],
  "trait_codes": ["plant_height", "thousand_grain_weight"],
  "filters": [
    {"trait_code": "plant_height", "operator": "lte", "value": 100},
    {"trait_code": "thousand_grain_weight", "operator": "gt", "value": 25}
  ],
  "limit": 100
}
```

可用操作符：`eq`、`lt`、`lte`、`gt`、`gte`。后端将其映射到固定的比较符，不接收模型给出的 SQL 片段。

## 模板四：根系表型查询

适用提问：`查询某品种的总根长和根冠比。`

```json
{
  "scope": "root_phenotype",
  "variety_ids": ["由后端根据品种名或别名解析"],
  "trait_codes": ["total_root_length", "root_shoot_ratio"],
  "filters": [],
  "limit": 100
}
```

读取：`variety_basic` + `root_phenotype_observation`。

## 通用查询兜底：受控参数解析

当用户问题没有命中上述确定性规则、但明显是在查询已发布数据时，系统才调用神农进行一次**参数解析**。神农只会收到用户问题和字段字典的元数据，不会收到数据库内容、表结构 SQL 或其他用户的私有材料。

神农只能返回下面的受限 JSON 申请；后端会拒绝额外字段、未知 `trait_code`、不支持的比较符和无法解析的品种名称，然后再选择上述固定模板执行。

```json
{
  "query_needed": true,
  "scope": "rice_phenotype",
  "variety_names": ["田两优9号"],
  "trait_codes": ["plant_height", "thousand_grain_weight"],
  "filters": [],
  "clarification": null
}
```

例如，`帮我看看田两优9号植株高度以及每千粒质量` 可被解析为“株高 + 千粒重”的按品种查询；`高产、抗病的品种有哪些` 因没有可审计的阈值或评价标准，会返回追问，不自动执行筛选。

## 安全和可追溯要求

- 只读访问已发布数据；水稻表型必须满足 `publish_status = 'published'`。
- 品种别名先由后端解析为内部 `variety_id`，字段中文名先由字典映射为稳定的 `trait_code`。
- SQL 参数全部绑定；不允许 `INSERT`、`UPDATE`、`DELETE`、`DROP` 或任意表名、列名。
- 神农不生成或执行 SQL；它的作用仅限于在规则解析失败时填写受限查询参数，服务端会使用 Pydantic、字段白名单和固定 SQL 模板再次校验。
- 结果证据卡片保存模板编号、已验证参数和返回字段数量。
- 证据卡片还展示参数解析方式，区分“规则解析”和“神农受控参数解析”。
- 未识别的品种、字段或歧义条件应追问科研人员，不自动猜测。
- `limit = 100` 表示最多匹配 100 个品种/材料，而不是最多 100 条长表观测记录；后端会按所选字段数放宽内部观测行数上限，避免“全字段查询”被截断。
