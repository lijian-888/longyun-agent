# 水稻参考基因组注释

将与 GWAS 基因型坐标版本一致的水稻 GFF3 文件放在本目录，例如：

```text
reference/IRGSP-1.0.gff3
```

然后在服务器的 `deploy/.env.lan` 中配置：

```text
RICE_GFF3_PATH=/data/reference/IRGSP-1.0.gff3
```

平台只读取 `gene` 特征，并以 `Name`、`gene_name`、`ID` 或 `locus_tag`
作为候选基因名称。未配置或文件坐标版本不匹配时，GWAS 结果会明确输出候选区域，
不会将区域错误表述为已注释基因。
