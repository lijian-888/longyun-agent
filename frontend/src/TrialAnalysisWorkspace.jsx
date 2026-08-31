import { useEffect, useState } from "react";
import { AlertTriangle, BarChart3, Download, LoaderCircle, ShieldCheck } from "lucide-react";
import { authorizedFetch, jsonRequest, request } from "./api";

const METHODS = [
  ["same_trial", "同一试验材料比较（ANOVA + Tukey）"],
  ["stability", "多年多点高产稳产分析"],
  ["environment", "环境因子关联分析"],
  ["management", "材料 × 管理措施分析"],
  ["tradeoff", "产量、抗倒伏与品质权衡"],
  ["decline", "材料表现异常证据拆解"],
];

async function download(path, name) {
  const response = await authorizedFetch(path);
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "下载失败");
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url);
}

function ResultTable({ rows }) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(0, 12);
  return <div className="intelligence-table-wrap"><table className="intelligence-table"><thead><tr>{columns.map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>{rows.slice(0, 100).map((row, index) => <tr key={index}>{columns.map((key) => <td key={key}>{typeof row[key] === "object" ? JSON.stringify(row[key]) : String(row[key] ?? "—")}</td>)}</tr>)}</tbody></table></div>;
}

export default function TrialAnalysisWorkspace({ onNotice }) {
  const [packages, setPackages] = useState([]);
  const [form, setForm] = useState({ package_id: "", analysis_type: "stability", year: "", site_name: "", material_code: "", treatment_code: "" });
  const [result, setResult] = useState(null);
  const [chartUrl, setChartUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    request("/api/research/trial-analysis/packages").then((rows) => {
      setPackages(rows); if (rows.length) setForm((current) => ({ ...current, package_id: rows[0].id }));
    }).catch((requestError) => setError(requestError.message)).finally(() => setLoading(false));
  }, []);
  useEffect(() => () => { if (chartUrl) URL.revokeObjectURL(chartUrl); }, [chartUrl]);

  async function run() {
    setRunning(true); setError(""); setResult(null);
    if (chartUrl) { URL.revokeObjectURL(chartUrl); setChartUrl(""); }
    try {
      const value = await request("/api/research/trial-analysis/run", jsonRequest("POST", { ...form, year: form.year ? Number(form.year) : null }));
      setResult(value);
      const chart = await authorizedFetch(`/api/research/trial-analysis/runs/${value.analysis_run_id}/chart.png`);
      if (chart.ok) setChartUrl(URL.createObjectURL(await chart.blob()));
    } catch (requestError) { setError(requestError.message); } finally { setRunning(false); }
  }

  if (loading) return <div className="workspace-loading"><LoaderCircle className="spin" size={20} />正在读取已发布试验资料包…</div>;
  return <section className="intelligence-workspace">
    <header className="workspace-hero"><div><span>任务 6 · 受控统计</span><h2>田间试验数据自动分析</h2><p>按试验设计和当前数据条件选择经过审查的统计方法；条件不满足时明确阻断，不强行生成结果。</p></div><ShieldCheck size={30} /></header>
    {error && <div className="workspace-error"><AlertTriangle size={17} />{error}</div>}
    <section className="workspace-card trial-analysis-form"><label>已发布试验资料包<select value={form.package_id} onChange={(event) => setForm({ ...form, package_id: event.target.value })}><option value="">请选择</option>{packages.map((item) => <option value={item.id} key={item.id}>{item.package_name} · {item.trial_count} 个试验 · {item.observation_count} 条观测</option>)}</select></label><label>分析方法<select value={form.analysis_type} onChange={(event) => setForm({ ...form, analysis_type: event.target.value })}>{METHODS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><div className="parameter-grid"><label>年份<input type="number" value={form.year} onChange={(event) => setForm({ ...form, year: event.target.value })} placeholder="同试验比较时必填" /></label><label>地点<input value={form.site_name} onChange={(event) => setForm({ ...form, site_name: event.target.value })} placeholder="同试验比较时必填" /></label><label>材料编码<input value={form.material_code} onChange={(event) => setForm({ ...form, material_code: event.target.value })} placeholder="异常拆解时必填" /></label><label>处理编码<input value={form.treatment_code} onChange={(event) => setForm({ ...form, treatment_code: event.target.value })} placeholder="可选" /></label></div><button className="primary-button" disabled={!form.package_id || running} onClick={run}>{running ? <LoaderCircle className="spin" size={16} /> : <BarChart3 size={16} />}执行质量检查与分析</button></section>
    {!packages.length && <div className="workspace-empty">当前课题没有已发布的区域试验资料包。请先在数据处理工作台导入并通过设计校验后发布。</div>}
    {result && <>
      <section className="workspace-card analysis-summary"><div><small>统计运行 {result.analysis_run_id} · {result.engine} · {result.analysis_version}</small><h3>{result.title}</h3><p>{result.model_formula}</p></div><button onClick={() => download(`/api/research/trial-analysis/runs/${result.analysis_run_id}/report.pdf`, "田间试验自动分析报告.pdf").catch((downloadError) => onNotice?.(downloadError.message))}><Download size={16} />下载 PDF</button></section>
      <section className="workspace-card"><h3>分析参数与执行记录</h3><dl className="run-metadata"><dt>输入问题</dt><dd>{result.question}</dd><dt>筛选参数</dt><dd><code>{JSON.stringify(result.filters)}</code></dd><dt>来源试验</dt><dd>{result.source_trial_ids?.join("、") || "—"}</dd><dt>输入记录数</dt><dd>{result.source_record_count}</dd></dl></section>
      {chartUrl && <section className="workspace-card analysis-chart"><h3>统计图表</h3><img src={chartUrl} alt="根据统计结果生成的图表" /></section>}
      <section className="workspace-card"><h3>统计结果</h3><ResultTable rows={result.records || result.materials || result.results || result.comparisons} /><details><summary>查看完整结构化结果</summary><pre>{JSON.stringify(result, null, 2)}</pre></details></section>
      {result.quality_check && <section className="workspace-card"><h3>数据质量检查</h3><p>{result.quality_check.method}</p><div className="weight-grid"><span>输入记录<strong>{result.quality_check.record_count}</strong></span><span>缺失值<strong>{result.quality_check.missing_value_count}</strong></span><span>疑似异常值<strong>{result.quality_check.outlier_count}</strong></span><span>结构问题<strong>{result.quality_check.structure_issue_count}</strong></span></div>{result.quality_check.outliers?.length > 0 && <details><summary>查看疑似异常值及原始定位</summary><ResultTable rows={result.quality_check.outliers} /></details>}{result.quality_check.structure_issues?.length > 0 && <ul className="evidence-list">{result.quality_check.structure_issues.map((item) => <li key={item}>{item}</li>)}</ul>}<p>{result.quality_check.note}</p></section>}
      <section className="workspace-warning"><AlertTriangle size={18} /><div><strong>方法限制与异常说明</strong><span>{result.limitations}</span></div></section>
    </>}
  </section>;
}
