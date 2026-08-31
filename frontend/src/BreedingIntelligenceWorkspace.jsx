import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Download, FileSearch, LoaderCircle, Scale, ShieldCheck } from "lucide-react";
import { authorizedFetch, jsonRequest, request } from "./api";

const SECTION_LABELS = {
  basic: "种质基本信息",
  aliases: "别名",
  pedigree: "系谱",
  phenotype: "表型与试验观测",
  environment: "关联环境",
  genotype: "基因型",
  literature: "相关资料",
};

function DataTable({ rows }) {
  const normalized = (rows || []).filter((row) => row !== null && row !== undefined).map((row) => (
    typeof row === "object" ? row : { value: row }
  ));
  const columns = [...new Set(normalized.flatMap((row) => Object.keys(row)))].filter((key) => key !== "text").slice(0, 10);
  if (!normalized.length) return <p className="intelligence-empty">当前没有可用记录。</p>;
  return <div className="intelligence-table-wrap"><table className="intelligence-table"><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{normalized.slice(0, 50).map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{row[column] === null || row[column] === undefined ? "—" : typeof row[column] === "object" ? JSON.stringify(row[column]) : String(row[column])}</td>)}</tr>)}</tbody></table></div>;
}

async function download(path, fileName) {
  const response = await authorizedFetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "下载失败");
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function BreedingIntelligenceWorkspace({ onNotice }) {
  const [tab, setTab] = useState("analysis");
  const [materials, setMaterials] = useState([]);
  const [selectedMaterial, setSelectedMaterial] = useState("");
  const [selectedParents, setSelectedParents] = useState([]);
  const [goal, setGoal] = useState("在现有证据范围内兼顾产量、稳定性、抗倒伏、抗病和品质");
  const [constraints, setConstraints] = useState("");
  const [rule, setRule] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [recommendation, setRecommendation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [materialRows, ruleValue] = await Promise.all([
          request("/api/research/intelligence/materials"),
          request("/api/research/intelligence/recommendation-rules"),
        ]);
        setMaterials(materialRows);
        setRule(ruleValue);
        if (materialRows.length) setSelectedMaterial(materialRows[0].material_key);
      } catch (requestError) {
        setError(requestError.message || "无法读取当前课题的种质证据。");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const weightLabels = useMemo(() => ({
    yield: "产量", stability: "稳定性", lodging: "抗倒伏", disease: "抗病",
    complementarity: "株高/生育期互补", quality: "品质", pedigree: "系谱风险", genotype: "基因型差异",
  }), []);

  async function runAnalysis() {
    if (!selectedMaterial) return;
    setRunning(true); setError(""); setAnalysis(null);
    try {
      setAnalysis(await request("/api/research/intelligence/material-analysis", jsonRequest("POST", { material_key: selectedMaterial })));
    } catch (requestError) {
      setError(requestError.message);
    } finally { setRunning(false); }
  }

  async function runRecommendation() {
    if (selectedParents.length < 2) { setError("请至少选择 2 个不同的候选亲本。"); return; }
    setRunning(true); setError(""); setRecommendation(null);
    try {
      setRecommendation(await request("/api/research/intelligence/parent-recommendations", jsonRequest("POST", {
        candidate_keys: selectedParents,
        breeding_goal: goal,
        constraints: constraints.split(/[，,；;]/).map((item) => item.trim()).filter(Boolean),
      })));
    } catch (requestError) {
      setError(requestError.message);
    } finally { setRunning(false); }
  }

  function toggleParent(key) {
    setSelectedParents((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  }

  if (loading) return <div className="workspace-loading"><LoaderCircle className="spin" size={20} />正在读取种质证据…</div>;

  return <section className="intelligence-workspace">
    <header className="workspace-hero"><div><span>任务 4–5 · 证据驱动</span><h2>种质解析与亲本组合辅助推荐</h2><p>仅汇总当前课题真实导入的数据；缺失内容不推断，推荐结果不是确定性预测。</p></div><ShieldCheck size={30} /></header>
    <div className="workspace-tabs"><button className={tab === "analysis" ? "active" : ""} onClick={() => setTab("analysis")}><FileSearch size={16} />种质综合解析</button><button className={tab === "recommendation" ? "active" : ""} onClick={() => setTab("recommendation")}><Scale size={16} />亲本辅助推荐</button></div>
    {error && <div className="workspace-error"><AlertTriangle size={17} />{error}</div>}

    {tab === "analysis" ? <>
      <section className="workspace-card control-card"><label>选择当前课题种质材料<select value={selectedMaterial} onChange={(event) => setSelectedMaterial(event.target.value)}>{materials.map((item) => <option key={item.material_key} value={item.material_key}>{item.material_key} · {item.name}</option>)}</select></label><button className="primary-button" onClick={runAnalysis} disabled={!selectedMaterial || running}>{running ? <LoaderCircle className="spin" size={16} /> : <FileSearch size={16} />}生成综合解析</button></section>
      {!materials.length && <div className="workspace-empty">当前课题尚未导入种质资源，请先由数据处理员完成机构数据导入。</div>}
      {analysis && <>
        <section className="workspace-card analysis-summary"><div><small>综合解析运行 {analysis.run_id}</small><h3>{analysis.material_name}（{analysis.material_key}）</h3><p>{analysis.summary}</p></div><button onClick={() => download(`/api/research/intelligence/material-analysis/${analysis.run_id}/report.pdf`, `${analysis.material_key}-综合解析.pdf`).catch((downloadError) => onNotice?.(downloadError.message))}><Download size={16} />下载 PDF</button></section>
        {analysis.missing_categories?.length > 0 && <section className="workspace-warning"><AlertTriangle size={18} /><div><strong>缺失数据</strong><span>{analysis.missing_categories.join("、")}。系统未对这些内容生成结论。</span></div></section>}
        {Object.entries(analysis.sections || {}).map(([key, section]) => <section className="workspace-card evidence-section" key={key}><header><h3>{SECTION_LABELS[key] || key}</h3><span className={section.available ? "status-ready" : "status-missing"}>{section.available ? `${section.records?.length || 0} 条证据` : "缺失"}</span></header>{section.available ? <DataTable rows={section.records} /> : <p className="intelligence-empty">当前课题未导入或未关联此类数据。</p>}</section>)}
        <section className="workspace-card"><h3>不确定性、异常与来源</h3><ul className="evidence-list">{analysis.uncertainties?.map((item) => <li key={item}>{item}</li>)}</ul><details><summary>查看 {analysis.sources?.length || 0} 条可追溯来源</summary><DataTable rows={analysis.sources} /></details></section>
      </>}
    </> : <>
      <section className="workspace-card recommendation-form"><div><h3>候选亲本</h3><p>至少选择 2 个，系统将枚举组合并按实际存在的证据维度排序。</p><div className="material-check-grid">{materials.map((item) => <label key={item.material_key}><input type="checkbox" checked={selectedParents.includes(item.material_key)} onChange={() => toggleParent(item.material_key)} /><span><strong>{item.name}</strong><small>{item.material_key}</small></span></label>)}</div></div><label>育种目标<textarea value={goal} onChange={(event) => setGoal(event.target.value)} /></label><label>限制条件（逗号分隔）<input value={constraints} onChange={(event) => setConstraints(event.target.value)} placeholder="例如：避免共同亲本，优先抗倒伏" /></label>{rule && <details><summary>当前课题评分权重（规则版本 {rule.version}）</summary><div className="weight-grid">{Object.entries(rule.weights).map(([key, value]) => <span key={key}>{weightLabels[key] || key}<strong>{value}%</strong></span>)}</div><p>{rule.rule_note}</p></details>}<button className="primary-button" onClick={runRecommendation} disabled={running || selectedParents.length < 2}>{running ? <LoaderCircle className="spin" size={16} /> : <Scale size={16} />}生成辅助推荐</button></section>
      {recommendation && <section className="workspace-card recommendation-results"><header><div><span className="auxiliary-label">辅助推荐</span><h3>{recommendation.title}</h3><p>{recommendation.disclaimer}</p></div><div className="download-actions"><button onClick={() => download(`/api/research/intelligence/parent-recommendations/${recommendation.run_id}/report.pdf`, "亲本组合辅助推荐.pdf").catch((downloadError) => onNotice?.(downloadError.message))}><Download size={15} />PDF</button><button onClick={() => download(`/api/research/intelligence/parent-recommendations/${recommendation.run_id}/result.csv`, "亲本组合辅助推荐.csv").catch((downloadError) => onNotice?.(downloadError.message))}><Download size={15} />CSV</button></div></header>{recommendation.recommendations.map((item, index) => <article className="recommendation-item" key={`${item.female_parent.material_key}-${item.male_parent.material_key}`}><div className="recommendation-rank">#{index + 1}</div><div><h4>{item.female_parent.name} × {item.male_parent.name}</h4><p><strong>{item.score} 分</strong><span className={`confidence confidence-${item.confidence}`}>可信程度 {item.confidence}</span><span>证据覆盖 {Math.round(item.evidence_coverage * 100)}%</span></p><dl><dt>推荐理由</dt><dd>{item.recommendation_reasons.join("；")}</dd><dt>风险</dt><dd>{item.risks.join("；")}</dd><dt>数据缺口</dt><dd>{item.data_gaps.join("；")}</dd></dl><details><summary>使用的维度、规则与证据</summary><pre>{JSON.stringify({ dimension_scores: item.dimension_scores, evidence: item.evidence }, null, 2)}</pre></details></div></article>)}</section>}
    </>}
  </section>;
}
