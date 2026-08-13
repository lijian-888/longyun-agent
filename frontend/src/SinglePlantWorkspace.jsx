import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  Dna,
  FileCheck2,
  FileSpreadsheet,
  FlaskConical,
  GitCompareArrows,
  Leaf,
  LoaderCircle,
  MapPin,
  Microscope,
  QrCode,
  RefreshCw,
  Search,
  ShieldCheck,
  Sprout,
  Upload,
  X,
} from "lucide-react";
import { jsonRequest, request } from "./api";

const STATUS_LABELS = {
  candidate: "候选",
  retained: "保留",
  observed: "继续观察",
  eliminated: "淘汰",
  promoted: "晋级",
};

const DATA_STATUS_LABELS = { draft: "草稿", published: "已发布", archived: "已归档" };

function withProject(path, projectId) {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}project_id=${encodeURIComponent(projectId)}`;
}

const TRAIT_PRESETS = [
  { code: "plant_height", name: "株高", unit: "cm" },
  { code: "tiller_number", name: "有效分蘖数", unit: "个" },
  { code: "panicle_length", name: "穗长", unit: "cm" },
  { code: "thousand_grain_weight", name: "千粒重", unit: "g" },
  { code: "leaf_blast_score", name: "叶瘟等级", unit: "级" },
];

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function StatusBadge({ status }) {
  return <span className={`sp-status ${status || "candidate"}`}>{STATUS_LABELS[status] || status || "候选"}</span>;
}

function EvidenceCount({ icon: Icon, value, label }) {
  return <span className={Number(value) ? "has-evidence" : ""}><Icon size={14} /><b>{value || 0}</b>{label}</span>;
}

function EmptyState({ icon: Icon = Sprout, title, detail }) {
  return <div className="sp-empty"><Icon size={30} /><strong>{title}</strong><span>{detail}</span></div>;
}

function latestTraitMap(detail) {
  const result = {};
  (detail?.observations || []).forEach((item) => {
    if (result[item.trait_code]) return;
    result[item.trait_code] = {
      name: item.trait_name,
      value: item.value_numeric ?? item.value_text ?? "—",
      unit: item.unit || "",
      stage: item.observation_stage,
    };
  });
  return result;
}

function ComparisonDialog({ details, onClose }) {
  const traits = useMemo(() => {
    const collected = new Map();
    details.forEach((detail) => Object.entries(latestTraitMap(detail)).forEach(([code, trait]) => {
      if (!collected.has(code)) collected.set(code, trait.name || code);
    }));
    return [...collected.entries()];
  }, [details]);

  return <div className="sp-modal-backdrop" onMouseDown={onClose}>
    <section className="sp-comparison-dialog" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><p>单株横向比较</p><h2>同材料候选单株证据对照</h2><span>只呈现平台已经记录的表型、基因型和选择证据，不对缺失值进行推断。</span></div><button className="icon-button" type="button" onClick={onClose}><X size={17} /></button></header>
      <div className="sp-comparison-scroll"><table><thead><tr><th>比较维度</th>{details.map((detail) => <th key={detail.sample.id}>{detail.sample.sample_code}<small>{detail.sample.generation_label || "世代未填"}</small></th>)}</tr></thead><tbody>
        <tr><td>当前状态</td>{details.map((detail) => <td key={detail.sample.id}><StatusBadge status={detail.sample.selection_status} /></td>)}</tr>
        <tr><td>试验位置</td>{details.map((detail) => <td key={detail.sample.id}>{detail.sample.trial_code ? `${detail.sample.trial_code} · ${detail.sample.plot_no}` : "未关联试验"}</td>)}</tr>
        <tr><td>表型证据</td>{details.map((detail) => <td key={detail.sample.id}>{detail.evidence_counts.observations} 条</td>)}</tr>
        <tr><td>基因型证据</td>{details.map((detail) => <td key={detail.sample.id}>{detail.evidence_counts.genotype_mappings} 条</td>)}</tr>
        <tr><td>选育记录</td>{details.map((detail) => <td key={detail.sample.id}>{detail.evidence_counts.selection_records} 条</td>)}</tr>
        {traits.map(([code, name]) => <tr key={code}><td>{name}</td>{details.map((detail) => {
          const trait = latestTraitMap(detail)[code];
          return <td key={detail.sample.id}>{trait ? <><strong>{trait.value} {trait.unit}</strong><small>{trait.stage}</small></> : "—"}</td>;
        })}</tr>)}
      </tbody></table></div>
    </section>
  </div>;
}

function SelectionForm({ sampleId, projectId, onSaved, onNotice }) {
  const [form, setForm] = useState({ decision: "retained", selection_criterion: "", evidence_summary: "", selection_site: "" });
  const [saving, setSaving] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!form.selection_criterion.trim()) {
      onNotice("请填写本次选择依据。");
      return;
    }
    setSaving(true);
    try {
      await request(withProject(`/api/single-plants/${sampleId}/selection-decisions`, projectId), jsonRequest("POST", {
        ...form,
        selection_site: form.selection_site.trim() || null,
        evidence_summary: form.evidence_summary.trim() || null,
        selection_year: new Date().getFullYear(),
      }));
      setForm((current) => ({ ...current, selection_criterion: "", evidence_summary: "" }));
      onNotice("选育决策已记录；原始表型和基因型证据没有被修改。");
      await onSaved();
    } catch (error) {
      onNotice(error.message);
    } finally {
      setSaving(false);
    }
  }

  return <form className="sp-decision-form" onSubmit={submit}>
    <div className="sp-section-heading"><div><span>形成决策</span><h3>记录本轮选育判断</h3></div><ShieldCheck size={18} /></div>
    <div className="sp-decision-options">
      {["retained", "observed", "eliminated", "promoted"].map((value) => <label key={value} className={form.decision === value ? "active" : ""}><input type="radio" name="decision" value={value} checked={form.decision === value} onChange={(event) => setForm({ ...form, decision: event.target.value })} />{STATUS_LABELS[value]}</label>)}
    </div>
    <label>选择依据<textarea value={form.selection_criterion} onChange={(event) => setForm({ ...form, selection_criterion: event.target.value })} placeholder="例如：成熟期株高适中、抗病等级达到目标，结合基因型证据保留。" /></label>
    <label>证据摘要<textarea value={form.evidence_summary} onChange={(event) => setForm({ ...form, evidence_summary: event.target.value })} placeholder="填写本次实际核对的表型、照片、标记或专家意见。" /></label>
    <label>选择地点<input value={form.selection_site} onChange={(event) => setForm({ ...form, selection_site: event.target.value })} placeholder="可选" /></label>
    <button className="primary-button" type="submit" disabled={saving}>{saving ? <LoaderCircle size={16} className="spin" /> : <ClipboardCheck size={16} />}保存选育决策</button>
  </form>;
}

function SinglePlantDetail({ detail, loading, projectId, onReload, onNotice }) {
  if (loading) return <div className="sp-detail-loading"><LoaderCircle size={20} className="spin" />正在读取单株证据链</div>;
  if (!detail) return <EmptyState icon={Microscope} title="选择一株查看证据" detail="点击上方单株记录，可查看表型、基因型与历次选育决策。" />;
  const { sample, observations, genotype_mappings: mappings, selection_history: selections, evidence_counts: counts } = detail;
  return <section className="sp-detail">
    <header className="sp-detail-header"><div><span>{sample.program_code} · {sample.material_code}</span><h2>{sample.sample_code}</h2><p>{sample.material_name} · {sample.generation_label || "世代未填"}{sample.plant_no ? ` · 第 ${sample.plant_no} 株` : ""}</p></div><div><StatusBadge status={sample.selection_status} /><small>{DATA_STATUS_LABELS[sample.data_status] || sample.data_status}</small></div></header>
    <div className="sp-evidence-strip">
      <EvidenceCount icon={Leaf} value={counts.observations} label="条表型" />
      <EvidenceCount icon={Dna} value={counts.genotype_mappings} label="条基因映射" />
      <EvidenceCount icon={ClipboardCheck} value={counts.selection_records} label="次选择" />
      <span><MapPin size={14} /><b>{sample.plot_no || "—"}</b>{sample.site_name || "未关联基地"}</span>
    </div>
    <div className="sp-detail-grid">
      <div className="sp-evidence-column">
        <section className="sp-evidence-panel"><div className="sp-section-heading"><div><span>多时期表型</span><h3>田间调查记录</h3></div><Leaf size={18} /></div>
          {observations.length ? <div className="sp-table-scroll"><table><thead><tr><th>时期</th><th>性状</th><th>观测值</th><th>质量</th><th>记录时间</th></tr></thead><tbody>{observations.map((item) => <tr key={item.id}><td>{item.observation_stage}</td><td>{item.trait_name}<small>{item.trait_code}</small></td><td><strong>{item.value_numeric ?? item.value_text ?? "—"} {item.unit}</strong></td><td>{item.quality_status}</td><td>{formatDate(item.observed_at)}<small>v{item.data_version}</small></td></tr>)}</tbody></table></div> : <EmptyState icon={Leaf} title="暂无单株表型" detail="由数据处理人员或田间管理员按生育时期录入。" />}
        </section>
        <section className="sp-evidence-panel"><div className="sp-section-heading"><div><span>分子证据</span><h3>FID/IID 基因型映射</h3></div><Dna size={18} /></div>
          {mappings.length ? <div className="sp-genotype-list">{mappings.map((item) => <article key={item.id}><Dna size={17} /><div><strong>{item.fid} / {item.iid}</strong><span>{item.asset_title} · v{item.version_number} · {item.reference_assembly}</span></div><em>{item.version_status}</em></article>)}</div> : <EmptyState icon={Dna} title="尚未绑定基因型样本" detail="在“基因型导入与质控”中把 FID/IID 精确关联到该单株。" />}
        </section>
      </div>
      <aside className="sp-decision-column">
        <section className="sp-selection-history"><div className="sp-section-heading"><div><span>可追溯记录</span><h3>选育历史</h3></div><RefreshCw size={17} /></div>{selections.length ? selections.map((item) => <article key={item.id}><div><StatusBadge status={item.selection_decision} /><time>{item.selection_year || "年份未填"} · {item.selection_site || "地点未填"}</time></div><strong>{item.selection_criterion}</strong>{item.evidence_summary && <p>{item.evidence_summary}</p>}<small>{item.recorded_by || "记录人未填"} · {formatDate(item.created_at)}</small></article>) : <p className="sp-history-empty">尚未形成单株级选育决策。</p>}</section>
        <SelectionForm sampleId={sample.id} projectId={projectId} onSaved={onReload} onNotice={onNotice} />
      </aside>
    </div>
  </section>;
}

export function SinglePlantResearchWorkspace({ onNotice, projectId = "" }) {
  const [keyword, setKeyword] = useState("");
  const [materials, setMaterials] = useState([]);
  const [material, setMaterial] = useState(null);
  const [samples, setSamples] = useState([]);
  const [sampleId, setSampleId] = useState("");
  const [detail, setDetail] = useState(null);
  const [loadingMaterials, setLoadingMaterials] = useState(true);
  const [loadingSamples, setLoadingSamples] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [selectedForCompare, setSelectedForCompare] = useState([]);
  const [comparisonDetails, setComparisonDetails] = useState([]);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    if (!projectId) {
      setMaterials([]);
      setMaterial(null);
      setSamples([]);
      setDetail(null);
      setLoadingMaterials(false);
      return undefined;
    }
    const timer = setTimeout(async () => {
      setLoadingMaterials(true);
      try {
        const rows = await request(withProject(`/api/genotype-assets/materials?keyword=${encodeURIComponent(keyword)}`, projectId));
        setMaterials(rows);
        setMaterial((current) => rows.some((item) => item.id === current?.id) ? current : rows[0] || null);
      } catch (error) {
        onNotice(error.message);
      } finally {
        setLoadingMaterials(false);
      }
    }, 180);
    return () => clearTimeout(timer);
  }, [keyword, onNotice, projectId]);

  async function loadSamples(preferredSampleId = "") {
    if (!material || !projectId) return;
    setLoadingSamples(true);
    try {
      const rows = await request(withProject(`/api/materials/${material.id}/single-plants`, projectId));
      setSamples(rows);
      setSampleId((current) => preferredSampleId || (rows.some((item) => item.id === current) ? current : rows[0]?.id || ""));
      setSelectedForCompare((current) => current.filter((id) => rows.some((item) => item.id === id)));
    } catch (error) {
      onNotice(error.message);
    } finally {
      setLoadingSamples(false);
    }
  }

  useEffect(() => { void loadSamples(); }, [material?.id, projectId]);

  async function loadDetail(id = sampleId) {
    if (!id || !projectId) {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    try {
      setDetail(await request(withProject(`/api/single-plants/${id}`, projectId)));
    } catch (error) {
      onNotice(error.message);
    } finally {
      setLoadingDetail(false);
    }
  }

  useEffect(() => { void loadDetail(sampleId); }, [sampleId, projectId]);

  function chooseMaterial(item) {
    setMaterial(item);
    setSampleId("");
    setDetail(null);
    setSelectedForCompare([]);
  }

  function toggleCompare(id) {
    setSelectedForCompare((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length >= 4 ? current : [...current, id]);
  }

  async function compare() {
    if (selectedForCompare.length < 2) {
      onNotice("请至少勾选 2 株、最多 4 株进行横向比较。");
      return;
    }
    setComparing(true);
    try {
      setComparisonDetails(await Promise.all(selectedForCompare.map((id) => request(withProject(`/api/single-plants/${id}`, projectId)))));
    } catch (error) {
      onNotice(error.message);
    } finally {
      setComparing(false);
    }
  }

  const retained = samples.filter((item) => item.selection_status === "retained" || item.selection_status === "promoted").length;
  const withPhenotype = samples.filter((item) => Number(item.observation_count)).length;
  const withGenotype = samples.filter((item) => Number(item.genotype_count)).length;

  if (!projectId) return <EmptyState icon={ShieldCheck} title="请先选择课题" detail="单株、表型、基因型映射和选育决策均按课题隔离。" />;

  return <div className="single-plant-workspace">
    <section className="sp-hero"><div><p>单株级育种证据链</p><h2>从材料进入每一株候选植株</h2><span>统一查看单株身份、多时期表型、FID/IID 基因型映射和历次选择决策。</span></div><div className="sp-hero-boundary"><ShieldCheck size={18} /><span>选育决策单独留痕，不覆盖原始观测；缺失证据明确显示为“暂无”。</span></div></section>
    <div className="sp-browser">
      <aside className="sp-material-panel"><div className="sp-material-search"><Search size={15} /><input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索材料编号或名称" /></div><div className="sp-material-title"><strong>育种材料</strong><span>{materials.length} 个候选</span></div><div className="sp-material-list">{loadingMaterials ? <span className="sp-list-loading"><LoaderCircle size={15} className="spin" />正在读取材料</span> : materials.map((item) => <button type="button" className={material?.id === item.id ? "active" : ""} key={item.id} onClick={() => chooseMaterial(item)}><Sprout size={15} /><span><strong>{item.material_code}</strong><small>{item.material_name}</small></span><ArrowRight size={14} /></button>)}</div></aside>
      <main className="sp-browser-main">
        <header className="sp-material-header"><div><span>{material?.material_code || "尚未选择材料"}</span><h3>{material?.material_name || "请选择左侧育种材料"}</h3><p>{material?.material_type || ""}</p></div><button className="secondary-button" type="button" onClick={() => loadSamples(sampleId)} disabled={!material || loadingSamples}><RefreshCw size={15} className={loadingSamples ? "spin" : ""} />刷新</button></header>
        <div className="sp-summary-cards"><article><span>单株总数</span><strong>{samples.length}</strong><small>当前材料</small></article><article><span>保留 / 晋级</span><strong>{retained}</strong><small>选择结果</small></article><article><span>已有表型</span><strong>{withPhenotype}</strong><small>单株覆盖</small></article><article><span>已有基因型</span><strong>{withGenotype}</strong><small>FID/IID 关联</small></article></div>
        <section className="sp-sample-list"><div className="sp-sample-list-head"><div><strong>候选单株</strong><span>勾选 2–4 株可横向比较</span></div><button className="secondary-button" type="button" onClick={compare} disabled={selectedForCompare.length < 2 || comparing}>{comparing ? <LoaderCircle size={15} className="spin" /> : <GitCompareArrows size={15} />}比较已选（{selectedForCompare.length}）</button></div>
          {loadingSamples ? <div className="sp-list-loading"><LoaderCircle size={17} className="spin" />正在读取单株</div> : samples.length ? <div className="sp-table-scroll"><table><thead><tr><th>比较</th><th>单株编号</th><th>世代 / 株号</th><th>试验位置</th><th>证据</th><th>当前状态</th></tr></thead><tbody>{samples.map((item) => <tr className={sampleId === item.id ? "active" : ""} key={item.id} onClick={() => setSampleId(item.id)}><td><input type="checkbox" aria-label={`选择 ${item.sample_code} 比较`} checked={selectedForCompare.includes(item.id)} onClick={(event) => event.stopPropagation()} onChange={() => toggleCompare(item.id)} /></td><td><strong>{item.sample_code}</strong><small>{DATA_STATUS_LABELS[item.data_status]}</small></td><td>{item.generation_label || "—"}{item.plant_no ? ` · ${item.plant_no} 株` : ""}</td><td>{item.trial_code ? <>{item.trial_code}<small>{item.plot_no}</small></> : "—"}</td><td><div className="sp-mini-evidence"><span title="表型"><Leaf size={13} />{item.observation_count}</span><span title="基因型"><Dna size={13} />{item.genotype_count}</span><span title="选择记录"><ClipboardCheck size={13} />{item.selection_count}</span></div></td><td><StatusBadge status={item.selection_status} /></td></tr>)}</tbody></table></div> : <EmptyState title="该材料还没有单株记录" detail="请由数据处理人员先导入单株主表，再录入田间表型和基因型映射。" />}
        </section>
        <SinglePlantDetail detail={detail} loading={loadingDetail} projectId={projectId} onNotice={onNotice} onReload={async () => { await loadSamples(sampleId); await loadDetail(sampleId); }} />
      </main>
    </div>
    {comparisonDetails.length > 0 && <ComparisonDialog details={comparisonDetails} onClose={() => setComparisonDetails([])} />}
  </div>;
}

export function SinglePlantImportWorkspace({ onNotice, projectId = "" }) {
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState("create_only");
  const [preview, setPreview] = useState(null);
  const [published, setPublished] = useState(null);
  const [busy, setBusy] = useState("");
  const inputRef = useRef(null);

  function chooseFile(selected) {
    setFile(selected || null);
    setPreview(null);
    setPublished(null);
  }

  async function send(action) {
    if (!projectId) {
      onNotice("请先选择单株数据归属课题。");
      return;
    }
    if (!file) {
      onNotice("请先选择单株导入 .xlsx 文件。");
      return;
    }
    setBusy(action);
    try {
      const data = new FormData();
      data.append("file", file);
      const result = await request(withProject(`/api/single-plants/import/${action}?mode=${mode}`, projectId), { method: "POST", body: data });
      if (action === "preview") {
        setPreview(result);
        setPublished(null);
        onNotice(result.can_publish ? "预检通过，可以正式发布。" : `预检发现 ${result.invalid_count} 条问题，请修正 Excel 后重新上传。`);
      } else {
        setPublished(result);
        setPreview(null);
        onNotice(`已发布 ${result.created_count} 株，更新 ${result.updated_count} 株。`);
      }
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  return <div className="sp-import-workspace">
    <section className="sp-import-hero"><div><p>单株主数据入口</p><h2>先预检，再原子发布</h2><span>上传文件仅在本次请求内解析；预检不会写入数据库，存在任何错误时整批禁止发布。</span></div><FileSpreadsheet size={42} /></section>
    <div className="sp-import-layout"><section className="panel sp-upload-panel"><div className="sp-section-heading"><div><span>步骤 1</span><h3>选择 Excel 文件</h3></div><Upload size={18} /></div>
      <button type="button" className={file ? "sp-file-drop selected" : "sp-file-drop"} onClick={() => inputRef.current?.click()}><FileSpreadsheet size={28} /><strong>{file?.name || "选择单株主表 .xlsx"}</strong><span>{file ? `${(file.size / 1024).toFixed(1)} KB · 点击可重新选择` : "文件不会作为附件存进项目或知识库"}</span></button><input ref={inputRef} hidden type="file" accept=".xlsx" onChange={(event) => chooseFile(event.target.files?.[0])} />
      <label className="sp-import-mode">导入方式<select value={mode} onChange={(event) => { setMode(event.target.value); setPreview(null); }}><option value="create_only">仅新增：遇到重复单株即阻断</option><option value="upsert">更新模式：存在则更新，不存在则新增</option></select></label>
      <button className="primary-button" type="button" onClick={() => send("preview")} disabled={!projectId || !file || Boolean(busy)}>{busy === "preview" ? <LoaderCircle size={16} className="spin" /> : <FileCheck2 size={16} />}执行数据预检</button>
    </section>
    <aside className="panel sp-import-guide"><div className="sp-section-heading"><div><span>填写约束</span><h3>工作表最低字段</h3></div><ShieldCheck size={18} /></div><ol><li><b>育种项目编号</b><span>必须已经存在于项目主数据。</span></li><li><b>材料编号</b><span>材料必须已经加入对应育种项目。</span></li><li><b>单株编号</b><span>同一项目内唯一，建议可打印成二维码。</span></li><li><b>试验编号 + 小区号</b><span>录入田间表型时必须关联；多处理时补充处理编号。</span></li></ol><div className="sp-import-note"><AlertTriangle size={16} /><span>单株身份、材料、小区不一致时不会“猜测匹配”，需要回到源文件修正。</span></div></aside></div>
    {preview && <section className="panel sp-preview-panel"><header><div><p>步骤 2 · 预检结果</p><h3>{preview.can_publish ? "全部通过，可正式发布" : "存在阻断问题"}</h3></div><div className="sp-preview-metrics"><span><b>{preview.row_count}</b>总行数</span><span className="good"><b>{preview.valid_count}</b>通过</span><span className={preview.invalid_count ? "bad" : ""}><b>{preview.invalid_count}</b>问题</span></div></header><div className="sp-table-scroll"><table><thead><tr><th>Excel 行</th><th>动作</th><th>项目</th><th>材料</th><th>单株编号</th><th>试验 / 小区</th><th>检查结果</th></tr></thead><tbody>{preview.rows.map((item) => <tr className={item.valid ? "" : "invalid"} key={item.row_number}><td>{item.row_number}</td><td>{item.action === "create" ? "新增" : "更新"}</td><td>{item.record.program_code}</td><td>{item.record.material_code}</td><td><strong>{item.record.sample_code}</strong></td><td>{item.record.trial_code ? `${item.record.trial_code} / ${item.record.plot_no}` : "—"}</td><td>{item.valid ? <span className="sp-valid"><CheckCircle2 size={14} />通过</span> : <ul className="sp-issue-list">{item.issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>}</td></tr>)}</tbody></table></div><footer><span>{preview.can_publish ? "发布将再次执行同样的校验，并在一个事务中完成。" : "请修正原 Excel 后重新预检。"}</span><button className="primary-button" type="button" onClick={() => send("publish")} disabled={!preview.can_publish || Boolean(busy)}>{busy === "publish" ? <LoaderCircle size={16} className="spin" /> : <CheckCircle2 size={16} />}正式发布单株主数据</button></footer></section>}
    {published && <section className="panel sp-published-card"><CheckCircle2 size={28} /><div><p>发布完成</p><h3>新增 {published.created_count} 株，更新 {published.updated_count} 株</h3><span>科研人员现在可以在“单株管理”中查看这些记录。</span></div></section>}
  </div>;
}

export function SinglePlantFieldWorkspace({ onNotice, projectId = "" }) {
  const [keyword, setKeyword] = useState("");
  const [results, setResults] = useState([]);
  const [sample, setSample] = useState(null);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [receipt, setReceipt] = useState(null);
  const [form, setForm] = useState({ observation_stage: "成熟期", trait_code: "plant_height", trait_name: "株高", unit: "cm", value_numeric: "", value_text: "", quality_status: "passed" });

  async function search(event) {
    event?.preventDefault();
    if (!keyword.trim()) return;
    if (!projectId) {
      onNotice("请先选择田间调查所属课题。");
      return;
    }
    setSearching(true);
    setReceipt(null);
    try {
      const rows = await request(withProject(`/api/single-plants/lookup?keyword=${encodeURIComponent(keyword.trim())}`, projectId));
      setResults(rows);
      setSample(rows[0] || null);
      if (!rows.length) onNotice("没有找到匹配的单株编号。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setSearching(false);
    }
  }

  function setTrait(code) {
    const trait = TRAIT_PRESETS.find((item) => item.code === code);
    setForm({ ...form, trait_code: trait.code, trait_name: trait.name, unit: trait.unit });
  }

  async function save(event) {
    event.preventDefault();
    if (!sample?.trial_entry_id) {
      onNotice("该单株尚未关联试验小区，不能录入田间表型。");
      return;
    }
    if (form.value_numeric === "" && !form.value_text.trim()) {
      onNotice("数值和文本观测至少填写一项。");
      return;
    }
    setSaving(true);
    try {
      const result = await request(withProject(`/api/single-plants/${sample.id}/observations`, projectId), jsonRequest("POST", {
        ...form,
        value_numeric: form.value_numeric === "" ? null : Number(form.value_numeric),
        value_text: form.value_text.trim() || null,
      }));
      setReceipt(result);
      setForm((current) => ({ ...current, value_numeric: "", value_text: "" }));
      onNotice("单株表型已保存，并记录调查任务、调查单元与数据版本。 ");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setSaving(false);
    }
  }

  return <div className="sp-field-workspace"><section className="sp-field-hero"><div><p>田间单株调查</p><h2>扫码定位，按时期留痕</h2><span>领导端不需要到田间重新调查；现场记录会回到单株证据链，并保留版本和记录人。</span></div><QrCode size={42} /></section>
    <div className="sp-field-layout"><section className="panel sp-field-search"><div className="sp-section-heading"><div><span>步骤 1</span><h3>定位单株</h3></div><Search size={18} /></div><form onSubmit={search}><div><QrCode size={18} /><input autoFocus value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="扫描或输入单株编号" /></div><button className="primary-button" type="submit" disabled={searching}>{searching ? <LoaderCircle size={16} className="spin" /> : <Search size={16} />}查找</button></form>{results.length > 1 && <div className="sp-field-results">{results.map((item) => <button type="button" className={sample?.id === item.id ? "active" : ""} key={item.id} onClick={() => setSample(item)}><strong>{item.sample_code}</strong><span>{item.program_code} · {item.material_code}</span></button>)}</div>}
      {sample ? <article className="sp-field-sample"><div><Sprout size={20} /><span><strong>{sample.sample_code}</strong><small>{sample.material_name} · {sample.generation_label || "世代未填"}</small></span><StatusBadge status={sample.selection_status} /></div><dl><div><dt>育种项目</dt><dd>{sample.program_code}</dd></div><div><dt>试验</dt><dd>{sample.trial_code || "未关联"}</dd></div><div><dt>小区</dt><dd>{sample.plot_no || "未关联"}</dd></div><div><dt>基地</dt><dd>{sample.site_name || "未关联"}</dd></div></dl>{!sample.trial_entry_id && <p className="sp-field-warning"><AlertTriangle size={15} />需要数据处理人员先补齐试验小区关联。</p>}</article> : <EmptyState icon={QrCode} title="等待扫描单株" detail="支持完整单株编号、部分编号或二维码中保存的单株 ID。" />}</section>
      <form className="panel sp-observation-form" onSubmit={save}><div className="sp-section-heading"><div><span>步骤 2</span><h3>录入表型</h3></div><Leaf size={18} /></div><label>调查时期<select value={form.observation_stage} onChange={(event) => setForm({ ...form, observation_stage: event.target.value })}><option>苗期</option><option>分蘖期</option><option>拔节期</option><option>抽穗期</option><option>成熟期</option></select></label><label>性状<select value={form.trait_code} onChange={(event) => setTrait(event.target.value)}>{TRAIT_PRESETS.map((item) => <option key={item.code} value={item.code}>{item.name}（{item.unit}）</option>)}</select></label><div className="sp-value-fields"><label>数值<input type="number" step="any" value={form.value_numeric} onChange={(event) => setForm({ ...form, value_numeric: event.target.value })} placeholder="测量值" /></label><label>文本描述<input value={form.value_text} onChange={(event) => setForm({ ...form, value_text: event.target.value })} placeholder="数值无法表达时填写" /></label></div><label>质量状态<select value={form.quality_status} onChange={(event) => setForm({ ...form, quality_status: event.target.value })}><option value="passed">通过</option><option value="warning">需复核</option><option value="pending">待确认</option><option value="rejected">不采用</option></select></label><button className="primary-button" type="submit" disabled={!sample?.trial_entry_id || saving}>{saving ? <LoaderCircle size={16} className="spin" /> : <CheckCircle2 size={16} />}保存单株表型</button>{receipt && <div className="sp-observation-receipt"><CheckCircle2 size={17} /><span><strong>保存成功 · v{receipt.data_version}</strong><small>调查单元：{receipt.survey_unit_code}</small></span></div>}</form></div>
  </div>;
}
