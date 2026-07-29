import { useEffect, useMemo, useState } from "react";
import { Archive, BarChart3, Dna, Download, FileJson, FileText, FlaskConical, LoaderCircle, RefreshCw, Trash2 } from "lucide-react";
import { authorizedFetch, request } from "./api";

const RESULT_TYPES = [
  { value: "all", label: "全部", icon: Archive },
  { value: "pdf_report", label: "PDF 报告", icon: FileText },
  { value: "chart_png", label: "图表", icon: BarChart3 },
  { value: "statistics_json", label: "统计结果", icon: FileJson },
  { value: "gwas_result_zip", label: "GWAS", icon: FlaskConical },
  { value: "genotype_qc_package", label: "基因型质控", icon: Dna },
];

function formatSize(bytes) {
  if (!bytes) return "-";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value));
}

function startDownload(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName || "research-result";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ResultsLibrary({ onNotice }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [activeType, setActiveType] = useState("all");

  async function loadResults() {
    setLoading(true);
    try {
      setItems(await request("/api/research/results"));
    } catch (error) {
      onNotice(error.message || "无法读取结果库。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadResults(); }, []);

  const filtered = useMemo(() => activeType === "all"
    ? items
    : items.filter((item) => item.result_type === activeType), [activeType, items]);
  const counts = useMemo(() => Object.fromEntries(RESULT_TYPES.slice(1).map((type) => [
    type.value, items.filter((item) => item.result_type === type.value).length,
  ])), [items]);

  async function downloadResult(item) {
    setBusyId(item.id);
    try {
      const response = await authorizedFetch(`/api/research/results/${item.id}/download`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "无法下载该研究产物。");
      }
      startDownload(await response.blob(), item.file_name);
    } catch (error) {
      onNotice(error.message || "无法下载该研究产物。");
    } finally {
      setBusyId("");
    }
  }

  async function deleteResult(item) {
    if (!window.confirm(`确认删除“${item.title}”吗？此操作只删除当前账号的结果库副本。`)) return;
    setBusyId(item.id);
    try {
      await request(`/api/research/results/${item.id}`, { method: "DELETE" });
      setItems((current) => current.filter((entry) => entry.id !== item.id));
      onNotice("研究产物已从结果库删除。");
    } catch (error) {
      onNotice(error.message || "无法删除该研究产物。");
    } finally {
      setBusyId("");
    }
  }

  return <section className="results-library">
    <header className="results-header">
      <div>
        <p>隆耘 Agent 育种智能体 / 研究产物</p>
        <h2>结果库</h2>
        <span>保存本账号在助手中生成的可追溯报告、图表和结构化统计结果。</span>
      </div>
      <button className="icon-button" type="button" title="刷新结果库" onClick={() => void loadResults()} disabled={loading}><RefreshCw size={17} className={loading ? "spin" : ""} /></button>
    </header>

    <div className="results-summary" aria-label="研究产物统计">
      <article><FileText size={17} /><span>PDF 报告</span><strong>{counts.pdf_report || 0}</strong></article>
      <article><BarChart3 size={17} /><span>分析图表</span><strong>{counts.chart_png || 0}</strong></article>
      <article><FileJson size={17} /><span>统计结果</span><strong>{counts.statistics_json || 0}</strong></article>
      <article><FlaskConical size={17} /><span>GWAS 分析结果</span><strong>{counts.gwas_result_zip || 0}</strong></article>
    </div>

    <div className="results-toolbar">
      <div className="results-type-tabs" role="tablist" aria-label="研究产物类型">
        {RESULT_TYPES.map((type) => {
          const Icon = type.icon;
          const total = type.value === "all" ? items.length : counts[type.value] || 0;
          return <button type="button" role="tab" key={type.value} className={activeType === type.value ? "active" : ""} onClick={() => setActiveType(type.value)}><Icon size={15} />{type.label}<span>{total}</span></button>;
        })}
      </div>
      <span>{filtered.length} 项研究产物</span>
    </div>

    <section className="results-list" aria-live="polite">
      {loading ? <div className="results-empty"><LoaderCircle size={22} className="spin" /><strong>正在读取结果库</strong></div> : filtered.length ? filtered.map((item) => {
        const config = RESULT_TYPES.find((type) => type.value === item.result_type) || RESULT_TYPES[0];
        const Icon = config.icon;
        return <article className="result-item" key={item.id}>
          <div className={`result-icon ${item.result_type}`}><Icon size={20} /></div>
          <div className="result-item-main">
            <div className="result-item-title"><strong>{item.title}</strong><span>{item.result_type_label}</span></div>
            <p>{item.summary || "本次研究产物已保存。"}</p>
            <small>{item.file_name} · {formatSize(item.size_bytes)} · {formatTime(item.created_at)}{item.analysis_run_id ? ` · 分析编号 ${item.analysis_run_id.slice(0, 8)}` : ""}</small>
          </div>
          <div className="result-item-actions">
            <button className="icon-button" type="button" title="下载研究产物" disabled={busyId === item.id} onClick={() => void downloadResult(item)}><Download size={16} /></button>
            <button className="icon-button danger" type="button" title="删除研究产物" disabled={busyId === item.id} onClick={() => void deleteResult(item)}><Trash2 size={16} /></button>
          </div>
        </article>;
      }) : <div className="results-empty"><Archive size={25} /><strong>结果库还没有研究产物</strong><span>在隆耘 Agent 育种智能体中生成 PDF 报告，或完成一次正式统计分析后，相关产物会自动保存在这里。</span></div>}
    </section>
  </section>;
}
