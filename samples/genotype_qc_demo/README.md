# 水稻基因型导入与质控完整流程测试数据

本目录中的文件为平台演示数据，不含真实农科院或个人基因型信息。

## 文件

- `rice_genotype_qc_full_demo.vcf`：10 个样本、60 个模拟 SNP 的 VCF 文件，用于测试 VCF 转 PLINK、染色体规范化和水稻专用 QC。
- `rice_genotype_qc_full_demo_sample_mapping.csv`：将 VCF 样本映射到平台中已有的 8 个候选材料和 2 个对照材料。
- `rice_genotype_qc_full_demo_phenotype.csv`：一个分析环境下的连续性状模拟值，可在发布基因型版本后用于连续性状 GWAS 的完整流程演示。

| VCF 样本 | 平台材料编码 |
| --- | --- |
| SEQ2025_001 - SEQ2025_008 | ME-A01 - ME-A08 |
| SEQ2025_009 | CK-01 |
| SEQ2025_010 | CK-02 |

## 测试流程

1. 以科研人员身份登录，进入“基因型数据”，新建资产，例如“水稻基因型 QC 完整流程测试”。
2. 群体类型选择“稳定育种材料”，参考基因组选择 `IRGSP-1.0`。
3. 上传 `rice_genotype_qc_full_demo.vcf`，等待后台完成格式转换和 QC。
4. 下载或导入同目录的样本映射 CSV。10 个样本均应显示为“可发布”。
5. 手动确认后发布该版本。发布后状态应为 `analysis_ready`，并可下载质控报告、工作簿和结果 ZIP。
6. 进入“水稻连续性状 GWAS”，选择该已发布版本。可下载系统预填的表型模板，也可上传本目录的 `rice_genotype_qc_full_demo_phenotype.csv`，选择 `trait_value` 作为连续性状列后执行流程演示。

这套数据用于验证流程，不用于得出任何育种或遗传学结论。
