import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  FileArchive,
  FileSpreadsheet,
  LoaderCircle,
  RefreshCw,
  Upload,
} from "lucide-react";
import { request } from "./api";

const statusLabels = {
  parsing: "正在解析",
  ready_for_review: "待数据处理员核验",
  published: "已发布入库",
  failed: "解析失败",
};

function number(value) {
  return value === undefined || value === null ? "-" : String(value);
}

function StatusBadge({ status }) {
  return <span className={`trial-package-status ${status}`}>{statusLabels[status] || status}</span>;
}

export default function TrialPackageWorkspace({ onNotice, projects = [], selectedProjectId, setSelectedProjectId }) {
  const inputRef = useRef(null);
  const [batches, setBatches] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [publishing, setPublishing] = useState(false);

  async function load(preferredId) {
    setLoading(true);
    try {
      if (!selectedProjectId) {
        setBatches([]);
        setSelected(null);
        return;
      }
      const query = `project_id=${encodeURIComponent(selectedProjectId)}`;
      const rows = await request(`/api/trial-packages?${query}`);
      setBatches(rows);
      const currentId = preferredId || selected?.id;
      const next = rows.find((item) => item.id === currentId) || rows[0] || null;
      if (next) {
        const detail = await request(`/api/trial-packages/${next.id}?${query}`);
        setSelected(detail);
      } else {
        setSelected(null);
      }
    } catch (error) {
      onNotice(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [selectedProjectId]);

  useEffect(() => {
    if (selected?.parse_status !== "parsing") return undefined;
    const timer = window.setInterval(() => void load(selected.id), 3000);
    return () => window.clearInterval(timer);
  }, [selected?.id, selected?.parse_status]);

  async function selectBatch(batch) {
    try {
      setSelected(await request(`/api/trial-packages/${batch.id}?project_id=${encodeURIComponent(selectedProjectId)}`));
    } catch (error) {
      onNotice(error.message);
    }
  }

  async function uploadPackage(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await request(`/api/trial-packages/upload?project_id=${encodeURIComponent(selectedProjectId)}`, { method: "POST", body: form });
      setSelected(result);
      await load(result.id);
      onNotice(result.parse_status === "ready_for_review"
        ? "资料包已完成本地解析，请核对来源、字段换算、关联数量和提醒后再发布入库。"
        : "资料包已保存，但解析未完成，请查看完整错误说明。"
      );
    } catch (error) {
      onNotice(error.message);
    } finally {
      setUploading(false);
    }
  }

  async function publish() {
    if (!selected || selected.parse_status !== "ready_for_review") return;
    setPublishing(true);
    try {
      const result = await request(`/api/trial-packages/${selected.id}/publish?project_id=${encodeURIComponent(selectedProjectId)}`, { method: "POST" });
      setSelected(result);
      await load(result.id);
      const counts = result.published_counts || {};
      const dossierNote = result.simulated_breeding_dossiers
        ? ` 已为 ${number(result.simulated_breeding_dossiers)} 个演示候选材料建立模拟选育档案，可在隆耘 Agent 育种智能体中指定材料生成“品种选育报告（审定辅助草稿）”。`
        : "";
      onNotice(`已发布入库：${number(counts.trial_count)} 个试验、${number(counts.entry_count)} 条参试记录、${number(counts.observation_count)} 条表型观测。科研人员现在可在隆耘 Agent 育种智能体中查询。${dossierNote}`);
    } catch (error) {
      onNotice(error.message);
    } finally {
      setPublishing(false);
    }
  }

  const summary = selected?.parse_summary || {};
  const preview = selected?.preview || {};
  const validation = selected?.validation_report || {};
  const validationPassed = validation.overall_status === "passed";
  const canPublish = selected?.parse_status === "ready_for_review" && validationPassed;

  return <div className="trial-package-workspace">
    <section className="trial-package-header">
      <div>
        <p>区域试验数据治理闭环</p>
        <h2>区域试验资料包</h2>
        <span>上传三年多点的原始 Excel 文件包，平台保留原件、解析关联、统一单位，并在确认后写入试验级标准数据。</span>
      </div>
      <div className="trial-package-header-actions">
        <select value={selectedProjectId || ""} onChange={(event) => setSelectedProjectId?.(event.target.value)} disabled={!projects.length} aria-label="当前课题">
          {!projects.length && <option value="">暂无可用课题</option>}
          {projects.map((project) => <option key={project.id} value={project.id}>{project.project_name}</option>)}
        </select>
        <button className="icon-button" type="button" title="刷新资料包状态" onClick={() => load(selected?.id)} disabled={loading}><RefreshCw size={17} className={loading ? "spin" : ""} /></button>
        <button className="primary-button" type="button" onClick={() => inputRef.current?.click()} disabled={uploading || !selectedProjectId}>
          {uploading ? <LoaderCircle size={16} className="spin" /> : <Upload size={16} />}
          上传 ZIP 资料包
        </button>
        <input ref={inputRef} hidden type="file" accept=".zip" onChange={uploadPackage} />
      </div>
    </section>

    <section className="trial-package-guidance">
      <FileArchive size={19} />
      <div><strong>第一版资料包结构</strong><span>按年份放入材料与小区布局、环境与土壤检测、管理记录、农艺品质记录四类 Excel。默认按“材料 × 施氮随机区组设计”核验，原始 ZIP 不被改写，解析出的每条记录都保留文件、工作表和行号。</span></div>
    </section>

    <section className="trial-package-layout">
      <aside className="trial-package-list">
        <div className="trial-package-list-title"><strong>已上传资料包</strong><span>{batches.length} 份</span></div>
        {loading && !batches.length && <div className="trial-package-empty"><LoaderCircle size={17} className="spin" />正在读取资料包</div>}
        {!loading && !batches.length && <div className="trial-package-empty"><FileArchive size={20} /><strong>还没有区域试验资料包</strong><span>请由数据处理员上传 ZIP 文件包开始治理。</span></div>}
        {batches.map((batch) => <button type="button" key={batch.id} className={`trial-package-list-item ${selected?.id === batch.id ? "active" : ""}`} onClick={() => selectBatch(batch)}>
          <FileSpreadsheet size={17} />
          <span><strong>{batch.display_name}</strong><small>{batch.created_at ? new Date(batch.created_at).toLocaleString("zh-CN") : ""}</small></span>
          <StatusBadge status={batch.parse_status} />
        </button>)}
      </aside>

      <section className="trial-package-detail">
        {!selected ? <div className="trial-package-detail-empty"><ClipboardCheck size={28} /><h3>等待导入区域试验资料包</h3><p>导入后，这里会展示字段匹配、单位换算、材料参试关联、质量提醒和待发布结果。</p></div> : <>
          <div className="trial-package-detail-head">
            <div><p>资料包解析与发布</p><h3>{selected.display_name}</h3><span>{selected.archive_name}</span></div>
            <StatusBadge status={selected.parse_status} />
          </div>

          {selected.error_message && <div className="trial-package-error"><AlertTriangle size={18} /><div><strong>解析未完成</strong><span>{selected.error_message}</span></div></div>}

          {selected.parse_status !== "failed" && <>
            <div className="trial-package-metrics">
              <Metric label="原始文件" value={summary.source_file_count} note="ZIP 内已识别的 Excel" />
              <Metric label="试验环境" value={summary.trial_count} note="年份 × 试验点" />
              <Metric label="材料参试" value={summary.entry_count} note="已关联的小区重复" />
              <Metric label="表型观测" value={summary.observation_count} note="长表标准观测值" />
            </div>

            {validation.template && <section className={`trial-design-validation ${validationPassed ? "passed" : "blocked"}`}>
              <div className="trial-design-validation-head"><div><ClipboardCheck size={18} /><span><strong>随机区组设计核验</strong><small>{validation.template}</small></span></div><StatusBadge status={validationPassed ? "published" : "failed"} /></div>
              <div className="trial-design-summary"><span>已核验 {validation.trial_results?.length || 0} 个试验环境</span><span>阻断问题 {validation.blocking_issues?.length || 0} 项</span><span>补充提醒 {validation.warnings?.length || 0} 项</span></div>
              {!validationPassed && <div className="trial-design-problems">{(validation.blocking_issues || []).slice(0, 6).map((issue) => <p key={issue}><AlertTriangle size={15} />{issue}</p>)}</div>}
              <details className="trial-design-detail"><summary>查看各试验完整性</summary><div>{(validation.trial_results || []).map((item) => <article key={item.trial_key}><strong>{item.trial_year} · {item.site_name}</strong><span>{item.block_count} 个区组 · {item.material_count} 个材料 · {item.treatment_count} 个处理 · {item.observed_entry_count}/{item.expected_entry_count} 个组合</span><em className={item.status}>{item.status === "passed" ? "组合完整" : item.issues?.join("；")}</em></article>)}</div></details>
            </section>}

            <div className="trial-package-sections">
              <section className="trial-package-section">
                <h4>解析来源与关联结果</h4>
                <div className="trial-source-grid">{(preview.source_files || []).map((file) => <div key={`${file.relative_path}-${file.sheet_name}`}><FileSpreadsheet size={16} /><span><strong>{file.file_name}</strong><small>{file.source_role} · {file.sheet_name} · {file.row_count} 行</small></span></div>)}</div>
                <div className="trial-entity-line"><strong>材料：</strong>{(preview.materials || []).map((item) => item.material_name).join("、") || "-"}</div>
                <div className="trial-entity-line"><strong>试验：</strong>{(preview.trials || []).map((item) => `${item.trial_year} ${item.site_name}`).join("、") || "-"}</div>
              </section>

              <section className="trial-package-section">
                <h4>入库前治理结果</h4>
                <div className="trial-check-list">
                  <p><CheckCircle2 size={16} />以“材料 + 试验 + 处理 + 重复 + 小区”为参试记录主键，已关联 {number(summary.entry_count)} 条记录。</p>
                  <p><CheckCircle2 size={16} />产量统一为 kg/亩，施氮和产量的 kg/ha 来源会按规则换算；原始值仍保留。</p>
                  <p><CheckCircle2 size={16} />环境、土壤和管理记录会分别关联到同年同点试验，不会覆盖材料表型。</p>
                </div>
                {preview.entry_preview?.length > 0 && <div className="trial-entry-preview"><strong>参试记录预览</strong>{preview.entry_preview.slice(0, 5).map((item) => <span key={`${item.trial_key}-${item.plot_no}`}>{item.year} · {item.site_name} · {item.raw_material_name} · {item.treatment_name} · {item.plot_no} · {item.traits.length} 个表型</span>)}</div>}
              </section>
            </div>

            {selected.warnings?.length > 0 && <section className="trial-package-warnings"><div><AlertTriangle size={18} /><strong>需要核对的提醒（{selected.warnings.length}）</strong></div>{selected.warnings.slice(0, 12).map((warning) => <p key={warning}>{warning}</p>)}</section>}

            <footer className="trial-package-publish-bar">
              <div><strong>{selected.parse_status === "published" ? "资料包已写入试验级标准数据" : validationPassed ? "确认后才会正式入库" : "请先处理随机区组核验阻断项"}</strong><span>{selected.parse_status === "published" ? "隆耘 Agent 育种智能体只读取已发布数据，可对该资料包回答同试验比较、稳定性、环境、管理、权衡和表现变化问题。" : validationPassed ? "确认发布会写入试验、环境、处理、参试和表型观测表；不覆盖原始 ZIP。" : "当前资料可以保留在暂存区查看，但不能作为正式统计分析的数据基础。"}</span></div>
              {selected.parse_status === "ready_for_review" && <button className="primary-button" type="button" onClick={publish} disabled={publishing || !canPublish}><ClipboardCheck size={16} />{publishing ? "正在发布入库" : canPublish ? "确认发布入库" : "核验未通过，不能发布"}</button>}
            </footer>
          </>}
        </>}
      </section>
    </section>
  </div>;
}

function Metric({ label, value, note }) {
  return <div className="trial-package-metric"><span>{label}</span><strong>{number(value)}</strong><small>{note}</small></div>;
}
