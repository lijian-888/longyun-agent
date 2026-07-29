"""Small, self-contained local runner for the fixed rice GWAS workflow.

The runner reads a validated PLINK SNP-major BED package directly.  It is kept
inside the API process intentionally for the demo deployment: it has no shell
escape hatch and only consumes files already locked by a GWAS plan.  The model
is a P3D/EMMAX-style mixed-model approximation: variance components are fitted
once under the null model, then every SNP is tested with the same kinship
covariance.  That is appropriate for a practical local proof of concept, not a
replacement for a production HPC pipeline.
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib import pyplot as plt


class LocalGwasError(RuntimeError):
    pass


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise LocalGwasError(f"无法读取文本文件：{path.name}")


def _read_table(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    reader = csv.DictReader(_read_text(path).splitlines(), delimiter=delimiter)
    return [dict(row) for row in reader], reader.fieldnames or []


def _safe_extract_plink(archive_path: Path, destination: Path) -> tuple[Path, Path, Path]:
    with zipfile.ZipFile(archive_path) as archive:
        candidates: dict[str, dict[str, zipfile.ZipInfo]] = {}
        for item in archive.infolist():
            if item.is_dir():
                continue
            basename = Path(item.filename).name
            suffix = Path(basename).suffix.lower()
            if suffix in {".bed", ".bim", ".fam"}:
                candidates.setdefault(Path(basename).stem, {})[suffix] = item
        matches = [(prefix, files) for prefix, files in candidates.items() if {".bed", ".bim", ".fam"}.issubset(files)]
        if len(matches) != 1:
            raise LocalGwasError("PLINK ZIP 中必须只有一组同前缀 bed/bim/fam 文件。")
        prefix, files = matches[0]
        destination.mkdir(parents=True, exist_ok=True)
        result: dict[str, Path] = {}
        for suffix, member in files.items():
            target = destination / f"{prefix}{suffix}"
            with archive.open(member) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            result[suffix] = target
    return result[".bed"], result[".bim"], result[".fam"]


def _read_fam(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for number, line in enumerate(_read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        values = line.split()
        if len(values) < 6:
            raise LocalGwasError(f".fam 第 {number} 行少于 6 列。")
        pairs.append((values[0], values[1]))
    if not pairs:
        raise LocalGwasError(".fam 中没有样本。")
    return pairs


def _read_bim(path: Path) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    for number, line in enumerate(_read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        values = line.split()
        if len(values) < 6:
            raise LocalGwasError(f".bim 第 {number} 行少于 6 列。")
        try:
            position = int(float(values[3]))
        except ValueError as exc:
            raise LocalGwasError(f".bim 第 {number} 行的物理位置不是整数。") from exc
        variants.append({"chr": values[0], "snp": values[1], "cm": values[2], "pos": position, "a1": values[4], "a2": values[5]})
    if not variants:
        raise LocalGwasError(".bim 中没有 SNP。")
    return variants


def _read_bed(path: Path, sample_count: int, variant_count: int) -> np.ndarray:
    raw = path.read_bytes()
    if raw[:3] != b"\x6c\x1b\x01":
        raise LocalGwasError("仅支持 SNP-major PLINK BED 文件。")
    bytes_per_variant = (sample_count + 3) // 4
    expected = 3 + bytes_per_variant * variant_count
    if len(raw) != expected:
        raise LocalGwasError("BED 文件长度与 FAM/BIM 中的样本或 SNP 数不一致。")
    packed = np.frombuffer(raw[3:], dtype=np.uint8).reshape(variant_count, bytes_per_variant)
    shifts = (2 * (np.arange(sample_count) % 4)).astype(np.uint8)
    codes = (packed[:, np.arange(sample_count) // 4] >> shifts) & 3
    # PLINK two-bit coding: 00 hom-A2, 10 het, 11 hom-A1, 01 missing.
    values = np.array([0.0, np.nan, 1.0, 2.0], dtype=np.float64)[codes]
    return values.T


def _normal_cdf_tail(z: np.ndarray) -> np.ndarray:
    return np.fromiter((math.erfc(abs(float(value)) / math.sqrt(2.0)) for value in z), dtype=np.float64, count=z.size)


def _as_numeric_covariates(rows: list[dict[str, str]], headers: list[str], ordered_pairs: list[tuple[str, str]]) -> tuple[np.ndarray, list[str]]:
    if not rows:
        return np.empty((len(ordered_pairs), 0)), []
    by_pair = {(str(row.get("FID", row.get("fid", ""))).strip(), str(row.get("IID", row.get("iid", ""))).strip()): row for row in rows}
    columns = [header for header in headers if header.lower() not in {"fid", "iid"}]
    arrays: list[np.ndarray] = []
    names: list[str] = []
    for column in columns:
        values = [str(by_pair.get(pair, {}).get(column, "")).strip() for pair in ordered_pairs]
        parsed: list[float] = []
        numeric = True
        for value in values:
            if value in {"", "NA", "N/A", ".", "-9"}:
                parsed.append(np.nan)
                continue
            try:
                parsed.append(float(value))
            except ValueError:
                numeric = False
                break
        if numeric:
            vector = np.asarray(parsed, dtype=float)
            median = float(np.nanmedian(vector)) if np.isfinite(vector).any() else 0.0
            arrays.append(np.where(np.isfinite(vector), vector, median))
            names.append(column)
            continue
        categories = sorted({value for value in values if value not in {"", "NA", "N/A", ".", "-9"}})
        for category in categories[:12][1:]:
            arrays.append(np.asarray([1.0 if value == category else 0.0 for value in values]))
            names.append(f"{column}={category}")
    return (np.column_stack(arrays) if arrays else np.empty((len(ordered_pairs), 0)), names)


def _null_reml_h2(y: np.ndarray, covariates: np.ndarray, eigvals: np.ndarray, eigvecs: np.ndarray) -> float:
    y_eigen = eigvecs.T @ y
    cov_eigen = eigvecs.T @ covariates
    best_h2, best_score = 0.5, float("inf")
    # A deterministic coarse grid is robust and sufficient for a local P3D fit.
    for h2 in np.linspace(0.02, 0.98, 49):
        variance = h2 * eigvals + (1.0 - h2)
        weights = 1.0 / np.sqrt(np.maximum(variance, 1e-8))
        y_weighted = weights * y_eigen
        c_weighted = weights[:, None] * cov_eigen
        beta, *_ = np.linalg.lstsq(c_weighted, y_weighted, rcond=None)
        residual = y_weighted - c_weighted @ beta
        dof = max(1, y.size - covariates.shape[1])
        sigma2 = max(float(residual @ residual) / dof, 1e-12)
        gram = c_weighted.T @ c_weighted
        sign, logdet = np.linalg.slogdet(gram)
        if sign <= 0:
            continue
        score = float(np.log(variance).sum() + dof * math.log(sigma2) + logdet)
        if score < best_score:
            best_h2, best_score = float(h2), score
    return best_h2


def _write_tsv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(headers)
        writer.writerows(rows)


def _plot_pca(path: Path, scores: np.ndarray, pairs: list[tuple[str, str]]) -> None:
    fig, axis = plt.subplots(figsize=(7.2, 5.2), dpi=150)
    axis.scatter(scores[:, 0], scores[:, 1], color="#207b61", alpha=0.76, s=22, edgecolors="white", linewidths=0.25)
    axis.set_xlabel("PC1")
    axis.set_ylabel("PC2")
    axis.set_title("Rice population structure (PCA)")
    axis.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _plot_manhattan(path: Path, variants: list[dict[str, Any]], pvalues: np.ndarray, threshold: float) -> None:
    chromosomes: list[str] = []
    positions: list[float] = []
    offsets: dict[str, float] = {}
    current_offset = 0.0
    for chromosome in dict.fromkeys(item["chr"] for item in variants):
        indices = [index for index, item in enumerate(variants) if item["chr"] == chromosome]
        maximum = max(variants[index]["pos"] for index in indices)
        offsets[chromosome] = current_offset
        current_offset += maximum + max(1_000_000, maximum * 0.03)
    for item in variants:
        chromosomes.append(item["chr"])
        positions.append(offsets[item["chr"]] + item["pos"])
    values = -np.log10(np.maximum(pvalues, 1e-300))
    fig, axis = plt.subplots(figsize=(10, 4.8), dpi=150)
    for index, chromosome in enumerate(dict.fromkeys(chromosomes)):
        mask = np.asarray([value == chromosome for value in chromosomes])
        axis.scatter(np.asarray(positions)[mask], values[mask], s=5, alpha=0.7, color=("#287f65" if index % 2 == 0 else "#8bbba8"), linewidths=0)
    axis.axhline(-math.log10(threshold), color="#c74d43", linewidth=1, linestyle="--", label="Bonferroni 0.05")
    axis.set_ylabel("−log10(P)")
    axis.set_xlabel("Chromosome")
    axis.set_title("GWAS Manhattan plot")
    axis.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _plot_qq(path: Path, pvalues: np.ndarray) -> None:
    observed = -np.log10(np.sort(np.maximum(pvalues, 1e-300)))
    expected = -np.log10(np.arange(1, pvalues.size + 1, dtype=float) / (pvalues.size + 1))
    top = max(1.0, float(max(observed.max(), expected.max())))
    fig, axis = plt.subplots(figsize=(5.2, 5.2), dpi=150)
    axis.scatter(expected, observed, color="#207b61", alpha=0.68, s=8, linewidths=0)
    axis.plot([0, top], [0, top], color="#c74d43", linestyle="--", linewidth=1)
    axis.set_xlabel("Expected −log10(P)")
    axis.set_ylabel("Observed −log10(P)")
    axis.set_title("GWAS QQ plot")
    axis.grid(alpha=0.18)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _normalized_chromosome(value: str) -> str:
    normalized = str(value).strip().lower()
    return normalized[3:] if normalized.startswith("chr") else normalized


@lru_cache(maxsize=4)
def _load_gff3_gene_annotations(configured_path: str) -> tuple[dict[str, list[tuple[int, int, str]]], str]:
    """Load only gene features from an explicitly configured local GFF3 file."""
    if not configured_path:
        return {}, "未配置本地 GFF3 注释；输出候选区域。"
    path = Path(configured_path)
    if not path.is_file():
        return {}, f"配置的 GFF3 文件不存在：{path.name}；输出候选区域。"

    annotations: dict[str, list[tuple[int, int, str]]] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip() or line.startswith("#"):
                    continue
                values = line.rstrip("\n").split("\t")
                if len(values) != 9 or values[2].lower() != "gene":
                    continue
                try:
                    start, end = int(values[3]), int(values[4])
                except ValueError:
                    continue
                attributes = {}
                for item in values[8].split(";"):
                    key, separator, value = item.partition("=")
                    if separator:
                        attributes[key.strip().lower()] = value.strip()
                gene = attributes.get("name") or attributes.get("gene_name") or attributes.get("id") or attributes.get("locus_tag")
                if gene:
                    annotations.setdefault(_normalized_chromosome(values[0]), []).append((start, end, gene))
    except OSError as exc:
        return {}, f"无法读取 GFF3 注释：{exc}；输出候选区域。"
    for genes in annotations.values():
        genes.sort(key=lambda item: item[0])
    if not annotations:
        return {}, f"GFF3 中未发现 gene 特征：{path.name}；输出候选区域。"
    return annotations, f"已使用本地 GFF3 注释：{path.name}。"


def _genes_in_window(annotations: dict[str, list[tuple[int, int, str]]], chromosome: str, start: int, end: int) -> str:
    genes = [gene for gene_start, gene_end, gene in annotations.get(_normalized_chromosome(chromosome), []) if gene_start <= end and gene_end >= start]
    return "; ".join(dict.fromkeys(genes)) if genes else "未命中基因"


def _candidate_rows(
    variants: list[dict[str, Any]],
    pvalues: np.ndarray,
    beta: np.ndarray,
    window_kb: int,
    threshold: float,
    annotations: dict[str, list[tuple[int, int, str]]],
    annotation_note: str,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for index in np.flatnonzero(pvalues <= threshold):
        variant = variants[int(index)]
        position = int(variant["pos"])
        window_start, window_end = max(1, position - window_kb * 1000), position + window_kb * 1000
        rows.append([
            variant["chr"], variant["snp"], position, variant["a1"], variant["a2"],
            f"{float(pvalues[index]):.6g}", f"{float(beta[index]):.6g}",
            window_start, window_end,
            _genes_in_window(annotations, str(variant["chr"]), window_start, window_end) if annotations else "未配置本地 GFF3 注释",
            annotation_note,
        ])
    return rows


def run_local_gwas(*, genotype_zip: Path, phenotype_path: Path, trait_column: str, covariate_path: Path | None, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Run QC, PCA, P3D mixed-model GWAS and immutable result generation."""
    work_dir = output_dir / "work"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    bed_path, bim_path, fam_path = _safe_extract_plink(genotype_zip, work_dir)
    pairs = _read_fam(fam_path)
    variants = _read_bim(bim_path)
    genotypes = _read_bed(bed_path, len(pairs), len(variants))

    maf_cutoff = float(parameters.get("maf", 0.05))
    snp_missing_cutoff = float(parameters.get("snp_missing_rate", 0.05))
    sample_missing_cutoff = float(parameters.get("sample_missing_rate", 0.05))
    original_samples, original_variants = genotypes.shape
    snp_missing = np.mean(~np.isfinite(genotypes), axis=0)
    dosage_mean = np.nanmean(genotypes, axis=0)
    maf = np.minimum(dosage_mean / 2.0, 1.0 - dosage_mean / 2.0)
    marker_keep = (snp_missing <= snp_missing_cutoff) & np.isfinite(maf) & (maf >= maf_cutoff)
    genotypes = genotypes[:, marker_keep]
    variants = [item for item, keep in zip(variants, marker_keep) if bool(keep)]
    if genotypes.shape[1] < 100:
        raise LocalGwasError("QC 后少于 100 个 SNP，无法进行稳定的 GWAS。")
    sample_missing = np.mean(~np.isfinite(genotypes), axis=1)
    sample_keep = sample_missing <= sample_missing_cutoff
    genotypes = genotypes[sample_keep]
    pairs = [pair for pair, keep in zip(pairs, sample_keep) if bool(keep)]
    if len(pairs) < 20:
        raise LocalGwasError("QC 后少于 20 个样本，无法进行 GWAS。")

    phenotype_rows, phenotype_headers = _read_table(phenotype_path)
    normalized = {header.lower(): header for header in phenotype_headers}
    fid_column, iid_column = normalized.get("fid"), normalized.get("iid")
    if not fid_column or not iid_column or trait_column not in phenotype_headers:
        raise LocalGwasError("表型文件必须有 FID、IID 和所选性状列。")
    phenotype_by_pair: dict[tuple[str, str], float] = {}
    for row in phenotype_rows:
        value = str(row.get(trait_column, "")).strip()
        if value in {"", "NA", "N/A", ".", "-9"}:
            continue
        try:
            phenotype_by_pair[(str(row[fid_column]).strip(), str(row[iid_column]).strip())] = float(value)
        except ValueError as exc:
            raise LocalGwasError(f"性状列 {trait_column} 含有非数值。") from exc
    phenotype_keep = np.asarray([pair in phenotype_by_pair for pair in pairs])
    pairs = [pair for pair, keep in zip(pairs, phenotype_keep) if bool(keep)]
    genotypes = genotypes[phenotype_keep]
    y = np.asarray([phenotype_by_pair[pair] for pair in pairs], dtype=float)
    if y.size < 20 or float(np.std(y)) <= 1e-12:
        raise LocalGwasError("匹配后的表型样本不足 20 或性状没有变异。")

    means = np.nanmean(genotypes, axis=0)
    genotypes = np.where(np.isfinite(genotypes), genotypes, means)
    allele_frequency = np.mean(genotypes, axis=0) / 2.0
    scale = np.sqrt(np.maximum(2.0 * allele_frequency * (1.0 - allele_frequency), 1e-8))
    standardized = (genotypes - 2.0 * allele_frequency) / scale

    # Take evenly spaced QC SNPs to make local execution predictable on a laptop.
    kinship_marker_count = min(5000, standardized.shape[1])
    marker_indices = np.linspace(0, standardized.shape[1] - 1, kinship_marker_count, dtype=int)
    pca_matrix = standardized[:, marker_indices]
    pca_matrix = pca_matrix - np.mean(pca_matrix, axis=0)
    u, singular_values, _ = np.linalg.svd(pca_matrix, full_matrices=False)
    pc_count = min(int(parameters.get("principal_components", 3)), max(0, u.shape[1] - 1))
    scores = u[:, :max(2, pc_count)] * singular_values[:max(2, pc_count)]
    pca_variance = (singular_values ** 2) / max(float(np.sum(singular_values ** 2)), 1e-12)

    covariates = np.ones((y.size, 1), dtype=float)
    covariate_names = ["intercept"]
    if pc_count:
        covariates = np.column_stack([covariates, scores[:, :pc_count]])
        covariate_names.extend([f"PC{index + 1}" for index in range(pc_count)])
    if covariate_path:
        cov_rows, cov_headers = _read_table(covariate_path)
        extra_covariates, extra_names = _as_numeric_covariates(cov_rows, cov_headers, pairs)
        if extra_covariates.size:
            covariates = np.column_stack([covariates, extra_covariates])
            covariate_names.extend(extra_names)
    if y.size <= covariates.shape[1] + 3:
        raise LocalGwasError("样本数不足以校正 PCA/协变量。")

    kinship = (pca_matrix @ pca_matrix.T) / float(pca_matrix.shape[1])
    eigvals, eigvecs = np.linalg.eigh(kinship)
    eigvals = np.maximum(eigvals, 0.0)
    h2 = _null_reml_h2(y, covariates, eigvals, eigvecs)
    variance = h2 * eigvals + (1.0 - h2)
    weights = 1.0 / np.sqrt(np.maximum(variance, 1e-8))
    y_weighted = weights * (eigvecs.T @ y)
    cov_weighted = weights[:, None] * (eigvecs.T @ covariates)
    cov_gram_inverse = np.linalg.pinv(cov_weighted.T @ cov_weighted)
    y_residual = y_weighted - cov_weighted @ (cov_gram_inverse @ (cov_weighted.T @ y_weighted))
    residual_variance = max(float(y_residual @ y_residual) / max(1, y.size - covariates.shape[1]), 1e-12)

    beta = np.zeros(standardized.shape[1], dtype=float)
    pvalues = np.ones(standardized.shape[1], dtype=float)
    for start in range(0, standardized.shape[1], 512):
        stop = min(start + 512, standardized.shape[1])
        marker_weighted = weights[:, None] * (eigvecs.T @ standardized[:, start:stop])
        marker_residual = marker_weighted - cov_weighted @ (cov_gram_inverse @ (cov_weighted.T @ marker_weighted))
        denominator = np.sum(marker_residual * marker_residual, axis=0)
        denominator = np.maximum(denominator, 1e-12)
        beta_block = (y_residual @ marker_residual) / denominator
        zscore = beta_block / np.sqrt(residual_variance / denominator)
        beta[start:stop] = beta_block
        pvalues[start:stop] = _normal_cdf_tail(zscore)

    output_dir.mkdir(parents=True, exist_ok=True)
    pca_path = output_dir / "pca.png"
    manhattan_path = output_dir / "manhattan.png"
    qq_path = output_dir / "qq.png"
    qc_path = output_dir / "qc_summary.json"
    pca_scores_path = output_dir / "pca_scores.tsv"
    association_path = output_dir / "gwas_results.tsv"
    candidates_path = output_dir / "candidate_genes.tsv"
    bonferroni = 0.05 / len(variants)
    _plot_pca(pca_path, scores, pairs)
    _plot_manhattan(manhattan_path, variants, pvalues, bonferroni)
    _plot_qq(qq_path, pvalues)
    _write_tsv(pca_scores_path, ["FID", "IID"] + [f"PC{index + 1}" for index in range(scores.shape[1])], [[pair[0], pair[1], *[f"{value:.8g}" for value in scores[row_index]]] for row_index, pair in enumerate(pairs)])
    _write_tsv(association_path, ["CHR", "SNP", "BP", "A1", "A2", "BETA", "P"], [[variant["chr"], variant["snp"], variant["pos"], variant["a1"], variant["a2"], f"{beta[index]:.8g}", f"{pvalues[index]:.8g}"] for index, variant in enumerate(variants)])
    annotations, annotation_note = _load_gff3_gene_annotations(os.getenv("RICE_GFF3_PATH", "").strip())
    candidate_rows = _candidate_rows(
        variants,
        pvalues,
        beta,
        int(parameters.get("candidate_window_kb", 100)),
        bonferroni,
        annotations,
        annotation_note,
    )
    _write_tsv(candidates_path, ["CHR", "LEAD_SNP", "BP", "A1", "A2", "P", "BETA", "WINDOW_START", "WINDOW_END", "CANDIDATE_GENE", "NOTE"], candidate_rows)
    qc_summary = {
        "workflow": "rice_gwas_local_p3d_v1",
        "input_samples": original_samples,
        "input_variants": original_variants,
        "qc_samples": int(sample_keep.sum()),
        "matched_phenotype_samples": int(y.size),
        "qc_variants": int(len(variants)),
        "maf_threshold": maf_cutoff,
        "snp_missing_threshold": snp_missing_cutoff,
        "sample_missing_threshold": sample_missing_cutoff,
        "pca_markers": int(kinship_marker_count),
        "pca_explained_variance": [float(value) for value in pca_variance[:max(2, pc_count)]],
        "covariates": covariate_names,
        "estimated_h2_null_model": h2,
        "bonferroni_threshold": bonferroni,
        "significant_variant_count": int(np.sum(pvalues <= bonferroni)),
        "candidate_window_kb": int(parameters.get("candidate_window_kb", 100)),
        "reference_assembly": str(parameters.get("reference_assembly", "IRGSP-1.0")),
        "candidate_annotation": annotation_note,
    }
    qc_path.write_text(json.dumps(qc_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(work_dir, ignore_errors=True)
    files = [
        {"key": "qc_summary", "title": "质控与模型摘要", "file_name": qc_path.name, "mime_type": "application/json"},
        {"key": "pca_plot", "title": "PCA 图", "file_name": pca_path.name, "mime_type": "image/png"},
        {"key": "manhattan_plot", "title": "Manhattan 图", "file_name": manhattan_path.name, "mime_type": "image/png"},
        {"key": "qq_plot", "title": "QQ 图", "file_name": qq_path.name, "mime_type": "image/png"},
        {"key": "candidate_table", "title": "显著位点与候选基因表", "file_name": candidates_path.name, "mime_type": "text/tab-separated-values"},
        {"key": "association_results", "title": "完整 GWAS 结果", "file_name": association_path.name, "mime_type": "text/tab-separated-values"},
        {"key": "pca_scores", "title": "PCA 样本得分", "file_name": pca_scores_path.name, "mime_type": "text/tab-separated-values"},
    ]
    for item in files:
        item["size_bytes"] = (output_dir / item["file_name"]).stat().st_size
    return {"summary": qc_summary, "files": files}
