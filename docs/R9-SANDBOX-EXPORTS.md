# R9 沙盒成果导出合规说明

## 成果入口

| 成果 | 服务端入口 | 格式 | 权限边界 |
| --- | --- | --- | --- |
| 种质解析报告 | `/api/research/intelligence/material-analysis/{run_id}/report.pdf` | PDF | 当前科研账号、当前课题 |
| 亲本辅助推荐报告 | `/api/research/intelligence/parent-recommendations/{run_id}/report.pdf` | PDF | 当前科研账号、当前课题 |
| 试验分析报告 | `/api/research/trial-analysis/runs/{run_id}/report.pdf` | PDF | 当前科研账号、当前课题 |
| 试验台账 | `/api/research/trial-analysis/runs/{run_id}/ledger.xlsx` | XLSX | 当前科研账号、当前课题 |
| 课题申报辅助材料草稿 | `/api/research/trial-analysis/runs/{run_id}/project-application-draft.pdf` | PDF | 当前科研账号、当前课题 |

## 统一合规控制

- 服务端 `sandbox_artifacts.py` 是唯一的成果装饰层，PDF、XLSX、CSV、JSON、PNG 和 ZIP 均在下载副本中写入“隆耘 Agent 沙盒演示环境”、数据来源、模型版本和合规版本。
- 已有结果库文件在下载时再次经过统一合规层，因此升级前生成的旧文件也不会形成无水印旁路。
- 基因型模板、质控产物、GWAS 单项产物和结果包均使用相同控制。
- 浏览器生成的 CSV、XLSX 和 PNG 必须先从 `/api/artifacts/compliance` 读取服务端策略；读取失败即停止导出。该接口不返回上游密钥，也不接受关闭水印的参数。
- 原始上传文件和内部分析输入保持不变，防止水印污染后续统计；只有提供给用户的导出副本被装饰。

## 验收

在后端目录执行：

```powershell
python -m unittest tests.test_sandbox_artifacts
python tests/run_r9_acceptance.py
```

第二条命令会临时生成并逐页/逐工作簿验证 10 份成果，覆盖五种成果类型各 2 份。验收脚本要求每份成果同时具备水印、数据来源、模型版本和 `r9-sandbox-artifact-v1` 合规标识。
