import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Dna,
  Download,
  FileArchive,
  FileSpreadsheet,
  FileUp,
  Link2,
  LoaderCircle,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { authorizedFetch, request } from "./api";

const CHUNK_BYTES = 10 * 1024 * 1024;
const MAX_FILE_BYTES = 5 * 1024 * 1024 * 1024;

const STATUS_LABEL = {
  awaiting_upload: "待上传",
  created: "待上传",
  uploading: "正在上传",
  queued: "等待质控",
  processing: "正在质控",
  awaiting_mapping: "待确认材料映射",
  reference_review_required: "待核验参考版本",
  analysis_ready: "已发布，可用于 GWAS",
  failed: "处理失败",
};

function formatBytes(value) {
  const size = Number(value || 0);
  if (!size) return "-";
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceLabel(value) {
  return value === "plink_zip" ? "PLINK 三件套 ZIP" : "VCF / VCF.GZ";
}

function statusTone(status) {
  if (status === "analysis_ready") return "ready";
  if (status === "failed" || status === "reference_review_required") return "warning";
  if (["queued", "processing", "uploading"].includes(status)) return "running";
  return "pending";
}

function downloadBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

async function downloadProtected(path, fallbackName, onNotice) {
  try {
    const response = await authorizedFetch(path);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || "下载失败。");
    }
    const disposition = response.headers.get("content-disposition") || "";
    const matched = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    downloadBlob(await response.blob(), matched ? decodeURIComponent(matched[1]) : fallbackName);
  } catch (error) {
    onNotice(error.message);
  }
}

function QcTemplateCard() {
  return <section className="genotype-template-card">
    <div><p>当前已发布模板</p><h3>水稻常规育种材料 QC v1.0</h3><span>管理员维护的只读版本；本次运行将完整写入参数、原始文件哈希和处理记录。</span></div>
    <ul>
      <li><b>SNP 缺失率</b><span>≤ 5%</span></li>
      <li><b>样本缺失率</b><span>≤ 5%</span></li>
      <li><b>MAF</b><span>≥ 0.05</span></li>
      <li><b>杂合率异常</b><span>|Z| &gt; 3 提示</span></li>
      <li><b>HWE</b><span>仅诊断，不自动过滤</span></li>
    </ul>
  </section>;
}

function MappingRow({ mapping, assetId, versionId, editable, onSaved, onNotice }) {
  const [editing, setEditing] = useState(false);
  // A mapping is a manual identity decision. Never seed the picker with the
  // previous material code, otherwise clearing a mapping makes it look as if
  // the researcher can only select that same material again.
  const [keyword, setKeyword] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) return undefined;
    const timer = setTimeout(async () => {
      try {
        const rows = await request(`/api/genotype-assets/materials?keyword=${encodeURIComponent(keyword)}`);
        setSuggestions(rows);
      } catch (error) {
        onNotice(error.message);
      }
    }, 180);
    return () => clearTimeout(timer);
  }, [editing, keyword, onNotice]);

  function startEditing() {
    if (!editable) return;
    setKeyword("");
    setSuggestions([]);
    setEditing(true);
  }

  async function save(material) {
    if (!editable) return;
    setBusy(true);
    try {
      const result = await request(`/api/genotype-assets/${assetId}/versions/${versionId}/mappings/${encodeURIComponent(mapping.fid)}/${encodeURIComponent(mapping.iid)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ material_id: material?.id || null, note: "科研人员确认映射" }),
      });
      if (!material) {
        setKeyword("");
        setSuggestions([]);
      }
      setEditing(false);
      // The mapping endpoint returns the refreshed version directly.
      // Keeping the full object here prevents the active-version state from
      // becoming undefined after a researcher confirms a material mapping.
      onSaved(result);
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy(false);
    }
  }

  return <tr>
    <td><strong>{mapping.fid}</strong><small>{mapping.iid}</small></td>
    <td>{mapping.sample_name || "-"}</td>
    <td>
      {editing && editable ? <div className="genotype-material-picker">
        <input autoFocus value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="输入材料编码、名称或别名" />
        <div className="genotype-material-options">
          {suggestions.map((item) => <button type="button" key={item.id} onClick={() => save(item)} disabled={busy}><strong>{item.material_code}</strong><span>{item.material_name}{item.aliases ? ` · ${item.aliases}` : ""}</span></button>)}
          {!suggestions.length && <span>未找到已建档材料。请提交数据治理申请，不会在此处直接新建材料主档。</span>}
        </div>
      </div> : mapping.material_code ? <span className="mapping-material"><CheckCircle2 size={14} />{mapping.material_code} · {mapping.material_name}</span> : <span className="mapping-unresolved">尚未确认</span>}
    </td>
    <td>{mapping.suggestion_reason || (mapping.material_code ? "人工确认" : "需要映射")}</td>
    <td>{editable
      ? <button type="button" className="inline-action" onClick={() => editing ? save(null) : startEditing()} disabled={busy}>{busy ? <LoaderCircle size={14} className="spin" /> : editing ? "清除映射" : mapping.material_code ? "更正" : "选择材料"}</button>
      : <span className="mapping-locked">已发布锁定</span>}
    </td>
  </tr>;
}

export default function GenotypeAssetsWorkspace({ onNotice }) {
  const [assets, setAssets] = useState([]);
  const [activeKey, setActiveKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [uploadState, setUploadState] = useState(null);
  const [busy, setBusy] = useState("");
  const [governanceOpen, setGovernanceOpen] = useState(false);
  const [createForm, setCreateForm] = useState({ title: "", source_type: "vcf", population_type: "stable_breeding", reference_assembly: "IRGSP-1.0" });
  const [governanceForm, setGovernanceForm] = useState({ request_type: "material_master", description: "" });
  const fileInput = useRef(null);
  const mappingInput = useRef(null);

  const active = useMemo(() => assets.find((item) => `${item.asset_id}:${item.id}` === activeKey) || assets[0] || null, [assets, activeKey]);
  const activeStatus = active?.status || "created";
  const activeMappings = active?.mappings || [];
  const unresolvedCount = activeMappings.filter((item) => !item.material_id).length;
  const duplicateCount = active?.mapping_summary?.duplicate_material_count || 0;
  const isFormalAnalysis = activeStatus === "analysis_ready";

  async function load(preferred = "", silent = false) {
    if (!silent) setLoading(true);
    try {
      const rows = await request("/api/genotype-assets");
      setAssets(rows);
      setActiveKey((current) => preferred || current || (rows[0] ? `${rows[0].asset_id}:${rows[0].id}` : ""));
    } catch (error) {
      onNotice(error.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!assets.some((item) => ["queued", "processing", "uploading"].includes(item.status))) return undefined;
    const timer = setInterval(() => void load("", true), 3500);
    return () => clearInterval(timer);
  }, [assets]);

  async function createAndUpload(event) {
    event.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) {
      onNotice("请选择 VCF、VCF.GZ 或包含 .bed/.bim/.fam 的 ZIP 文件。");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      onNotice("首版单个基因型文件上限为 5 GB。请拆分或通过内网数据治理通道处理。");
      return;
    }
    setBusy("create");
    try {
      // A network or browser interruption can leave a newly created asset in
      // awaiting_upload. Reuse that unfinished asset on retry so the user does
      // not accumulate duplicate empty assets.
      const pendingAsset = assets.find((item) => (
        item.status === "awaiting_upload"
        && item.title === createForm.title.trim()
        && item.source_format === createForm.source_type
        && item.reference_assembly === createForm.reference_assembly
        && item.population_type === createForm.population_type
      ));
      const asset = pendingAsset || await request("/api/genotype-assets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...createForm, source_format: createForm.source_type, source_type: undefined }),
      });
      const completed = await uploadFile(asset.asset_id, file);
      setCreating(false);
      setCreateForm({ title: "", source_type: "vcf", population_type: "stable_breeding", reference_assembly: "IRGSP-1.0" });
      if (fileInput.current) fileInput.current.value = "";
      await load(asset.asset_id + ":" + completed.version_id);
      onNotice("原始基因型文件已进入私有处理队列。系统会依次完成格式统一、质控和样本材料映射建议。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
      setUploadState(null);
    }
  }

  async function uploadFile(assetId, file) {
    const sessionKey = `genotype-upload:${assetId}`;
    const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_BYTES));
    let upload = null;
    const stored = sessionStorage.getItem(sessionKey);
    if (stored) {
      try {
        const candidate = JSON.parse(stored);
        if (candidate.fileName === file.name && candidate.fileSize === file.size && candidate.totalChunks === totalChunks) {
          upload = await request(`/api/genotype-assets/${assetId}/uploads/${candidate.uploadId}`);
        }
      } catch {
        sessionStorage.removeItem(sessionKey);
      }
    }
    if (!upload) {
      upload = await request(`/api/genotype-assets/${assetId}/uploads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_name: file.name, total_bytes: file.size, total_chunks: totalChunks }),
      });
    }
    // Earlier prototype sessions use ``upload_id`` while new sessions expose
    // the REST-style ``id`` as well. Normalize once before building URLs.
    const uploadId = upload.id || upload.upload_id;
    if (!uploadId) throw new Error("上传会话创建失败，未返回会话标识。请重新选择文件后上传。");
    sessionStorage.setItem(sessionKey, JSON.stringify({ uploadId, fileName: file.name, fileSize: file.size, totalChunks }));
    const received = new Set(upload.received_chunks || []);
    setUploadState({ name: file.name, current: received.size, total: totalChunks });
    for (let index = 0; index < totalChunks; index += 1) {
      if (received.has(index)) continue;
      const data = new FormData();
      data.append("file", file.slice(index * CHUNK_BYTES, Math.min(file.size, (index + 1) * CHUNK_BYTES)), file.name);
      const response = await authorizedFetch(`/api/genotype-assets/${assetId}/uploads/${uploadId}/chunks/${index}`, { method: "PUT", body: data });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `第 ${index + 1} 个上传分片失败。`);
      }
      setUploadState({ name: file.name, current: index + 1, total: totalChunks });
    }
    const completed = await request(`/api/genotype-assets/${assetId}/uploads/${uploadId}/complete`, { method: "POST" });
    sessionStorage.removeItem(sessionKey);
    return completed;
  }

  function updateActive(version) {
    setAssets((rows) => rows.map((row) => row.id === version.id ? version : row));
    setActiveKey(`${version.asset_id}:${version.id}`);
  }

  async function uploadMapping(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !active) return;
    if (activeStatus === "analysis_ready") {
      onNotice("当前版本已发布并锁定。请先生成材料映射修订版，再批量更正映射。");
      return;
    }
    setBusy("mapping");
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await request(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/mappings/import`, { method: "POST", body: form });
      updateActive(result.version);
      onNotice(`已处理样本映射表：成功更新 ${result.applied} 条，未识别条目仍保留在待处理区。`);
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function publish() {
    if (!active) return;
    setBusy("publish");
    try {
      const result = await request(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/publish`, { method: "POST" });
      updateActive(result);
      onNotice("质控版本已发布为分析就绪版本，可直接在连续性状 GWAS 中选择。原始文件仍不会对浏览器开放下载。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function createRevision() {
    if (!active) return;
    setBusy("revision");
    try {
      const result = await request(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/mapping-revision`, { method: "POST" });
      updateActive(result);
      onNotice("已生成新的材料映射修订版。原发布版本保持不变，新的修订版需再次人工发布。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function submitGovernance(event) {
    event.preventDefault();
    if (!active || !governanceForm.description.trim()) return;
    setBusy("governance");
    try {
      await request(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/governance-requests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(governanceForm),
      });
      setGovernanceForm({ request_type: "material_master", description: "" });
      setGovernanceOpen(false);
      onNotice("数据治理申请已提交。待处理员核验后会建立材料主档或给出规范化建议；原始基因型文件不会因此开放给其他人。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  return <div className="genotype-workspace">
    <section className="genotype-hero">
      <div><p>私有基因型数据治理</p><h2>基因型导入与水稻专用质控</h2><span>把 VCF 或 PLINK 三件套处理为可追溯、可人工确认的分析版本，再交给 GWAS 使用。</span></div>
      <div className="genotype-privacy"><ShieldCheck size={18} /><span>原始基因型只在内网私有存储与后端计算中使用，不提供浏览器下载。</span></div>
    </section>

    <section className="genotype-steps"><span className="done"><b>1</b>导入文件</span><span><b>2</b>统一格式与质控</span><span><b>3</b>确认样本材料映射</span><span><b>4</b>发布给 GWAS</span></section>

    <section className="genotype-layout">
      <aside className="genotype-assets-list">
        <div className="genotype-list-head"><div><p>我的私有资产</p><h3>基因型版本</h3></div><button type="button" title="刷新列表" onClick={() => void load()}><RefreshCw size={16} /></button></div>
        <button type="button" className="primary-button genotype-create-trigger" onClick={() => setCreating((value) => !value)}><Upload size={15} />导入基因型</button>
        {creating && <form className="genotype-create-form" onSubmit={createAndUpload}>
          <label>资产名称<input required value={createForm.title} onChange={(event) => setCreateForm({ ...createForm, title: event.target.value })} placeholder="例如：2025 区试材料 SNP 数据" /></label>
          <label>导入格式<select value={createForm.source_type} onChange={(event) => setCreateForm({ ...createForm, source_type: event.target.value })}><option value="vcf">VCF / VCF.GZ</option><option value="plink_zip">PLINK 三件套 ZIP</option></select></label>
          <label>材料群体类型<select value={createForm.population_type} onChange={(event) => setCreateForm({ ...createForm, population_type: event.target.value })}><option value="stable_breeding">稳定育种材料</option><option value="segregating">分离群体</option><option value="natural_germplasm">自然种质群体</option><option value="unknown">暂不确定</option></select></label>
          <label>参考基因组<select value={createForm.reference_assembly} onChange={(event) => setCreateForm({ ...createForm, reference_assembly: event.target.value })}><option>IRGSP-1.0</option></select></label>
          <label>选择文件<input ref={fileInput} type="file" required accept={createForm.source_type === "vcf" ? ".vcf,.gz" : ".zip"} /></label>
          <small>支持断点续传，单文件最大 5 GB。VCF 的 .tbi 索引可一并保留但不是首版必需项。</small>
          <button className="primary-button" disabled={busy === "create"}>{busy === "create" ? <LoaderCircle size={15} className="spin" /> : <FileUp size={15} />}{uploadState ? `上传 ${uploadState.current}/${uploadState.total}` : "开始私有导入"}</button>
        </form>}
        <div className="genotype-asset-items">
          {loading && <span className="loading-line"><LoaderCircle size={15} className="spin" />加载中</span>}
          {!loading && !assets.length && <div className="genotype-empty-list"><Dna size={22} /><span>尚未导入基因型资产。</span></div>}
          {assets.map((item) => <button type="button" key={item.id || item.asset_id} onClick={() => setActiveKey(`${item.asset_id}:${item.id}`)} className={`${active?.id === item.id ? "active" : ""} ${statusTone(item.status)}`}><Dna size={16} /><span><strong>{item.title}</strong><small>v{item.version_number} · {sourceLabel(item.source_format)}</small></span><em>{STATUS_LABEL[item.status] || item.status}</em></button>)}
        </div>
      </aside>

      <main className="genotype-detail">
        {!active ? <div className="genotype-empty-detail"><Dna size={34} /><h3>从一份基因型文件开始</h3><p>科研人员只需选择格式、群体类型和参考版本。转换、质控、审计与结果包会由平台在后端完成。</p></div> : <>
          <header className="genotype-detail-head"><div><p>私有资产 · {sourceLabel(active.source_format)}</p><h3>{active.title}</h3><span>版本 v{active.version_number} · {active.reference_assembly} · {active.population_type_label || active.population_type}</span></div><span className={`genotype-status ${statusTone(activeStatus)}`}>{["queued", "processing"].includes(activeStatus) && <LoaderCircle size={15} className="spin" />}{STATUS_LABEL[activeStatus] || activeStatus}</span></header>

          {active.error_message && <section className="genotype-alert error"><AlertTriangle size={18} /><div><strong>本次处理未完成</strong><span>{active.error_message}</span></div></section>}
          {activeStatus === "reference_review_required" && <section className="genotype-alert warning"><AlertTriangle size={18} /><div><strong>存在未确认参考坐标</strong><span>非标准染色体命名或未确认 contig 不会被静默删除，也不能直接进入 GWAS。请提交数据治理申请后再发布。</span></div><button type="button" onClick={() => setGovernanceOpen(true)}>提交治理申请</button></section>}
          {["created", "uploading", "queued", "processing"].includes(activeStatus) && <section className="genotype-processing"><LoaderCircle size={20} className="spin" /><div><strong>{activeStatus === "processing" ? "正在执行水稻专用质控" : "正在准备私有处理任务"}</strong><span>{active.job?.progress_label || "页面会自动更新；可继续浏览其他工作台。"}</span></div></section>}

          <section className="genotype-summary-grid">
            <article><span>原始样本</span><strong>{active.qc_summary?.input_sample_count ?? "-"}</strong><small>统一后的 PLINK 样本数</small></article>
            <article><span>原始 SNP</span><strong>{active.qc_summary?.input_variant_count?.toLocaleString?.() ?? "-"}</strong><small>转换后可追溯计数</small></article>
            <article><span>质控后 SNP</span><strong>{active.qc_summary?.qc_variant_count?.toLocaleString?.() ?? "-"}</strong><small>MAF/缺失率过滤后</small></article>
            <article><span>材料映射</span><strong>{active.mapping_summary?.mapped ?? 0}/{active.mapping_summary?.total ?? 0}</strong><small>一个样本只对应一个材料</small></article>
          </section>

          <QcTemplateCard />

          {active.report_available && <section className="genotype-downloads"><div><p>可追溯研究产物</p><h4>{isFormalAnalysis ? "正式质控报告与分析结果包" : "预质控报告"}</h4><span>{isFormalAnalysis
            ? "材料映射已完整确认并人工发布。结果包包含正式 PDF、样本/SNP 质控汇总、已确认映射表、处理工作簿和审计信息；不包含可直接下载的原始基因型。"
            : `当前仍有 ${unresolvedCount} 个样本待确认材料映射。预质控报告仅用于核验样本/SNP 质量和映射，不可作为 GWAS 或正式分析结论；正式结果包将在全部映射确认并人工发布后生成。`}
          </span></div><div><button type="button" className="secondary-button" onClick={() => downloadProtected(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/artifacts/report`, `${active.title}-水稻基因型${isFormalAnalysis ? "正式" : "预"}质控报告-v${active.version_number}.pdf`, onNotice)}><FileSpreadsheet size={15} />{isFormalAnalysis ? "下载正式 QC 报告" : "下载预质控报告"}</button>{isFormalAnalysis && active.package_available && <button type="button" className="primary-button" onClick={() => downloadProtected(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/artifacts/package`, `${active.title}-水稻基因型正式质控结果包-v${active.version_number}.zip`, onNotice)}><FileArchive size={15} />下载正式结果包</button>}</div></section>}

          {activeMappings.length > 0 && <section className="genotype-mapping-section">
            <header><div><p>关键人工确认环节</p><h3>样本与材料映射</h3><span>系统按材料编码、名称和别名给出建议。科研人员确认后，样本如 `SEQ2025_081` 才能对应平台材料档案如 `A-08`。</span></div><div className="mapping-counts"><span className={unresolvedCount ? "warning" : "ready"}>{unresolvedCount ? `${unresolvedCount} 个待确认` : "映射已完整"}</span>{duplicateCount > 0 && <span className="warning">{duplicateCount} 个重复材料</span>}</div></header>
            {activeStatus === "analysis_ready" && <div className="mapping-publish ready mapping-revision-guidance"><span><strong>当前版本已发布并锁定，不能直接更正材料映射。</strong> 请先生成材料映射修订版；新版本可重新选择材料，原版本及已关联的 GWAS 仍保留完整审计记录。</span><div><button type="button" className="secondary-button" onClick={() => downloadProtected(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/phenotype-template`, `${active.title}-连续性状表型模板.xlsx`, onNotice)}><FileSpreadsheet size={15} />下载专用表型模板</button><button type="button" className="inline-action" disabled={busy === "revision"} onClick={createRevision}>{busy === "revision" ? <LoaderCircle size={14} className="spin" /> : <Link2 size={14} />}生成映射修订版</button></div></div>}
              <div className="mapping-actions"><button type="button" className="secondary-button" onClick={() => downloadProtected(`/api/genotype-assets/${active.asset_id}/versions/${active.id}/mapping-template`, `${active.title}-样本映射模板.csv`, onNotice)}><Download size={15} />下载映射模板</button><button type="button" className="secondary-button" onClick={() => mappingInput.current?.click()} disabled={activeStatus === "analysis_ready" || busy === "mapping"} title={activeStatus === "analysis_ready" ? "已发布版本不可修改，请先生成映射修订版" : undefined}>{busy === "mapping" ? <LoaderCircle size={15} className="spin" /> : <Upload size={15} />}批量导入映射</button><input ref={mappingInput} hidden type="file" accept=".csv" onChange={uploadMapping} /><button type="button" className="inline-action" onClick={() => setGovernanceOpen(true)}><Send size={14} />未建档/冲突，提交治理</button></div>
            <div className="mapping-table-wrap"><table><thead><tr><th>VCF/PLINK 样本</th><th>原始样本名</th><th>平台材料档案</th><th>辅助依据</th><th>操作</th></tr></thead><tbody>{activeMappings.slice(0, 150).map((mapping) => <MappingRow key={`${mapping.fid}-${mapping.iid}`} mapping={mapping} assetId={active.asset_id} versionId={active.id} editable={activeStatus !== "analysis_ready"} onSaved={updateActive} onNotice={onNotice} />)}</tbody></table>{activeMappings.length > 150 && <p className="mapping-limit">为避免长表影响操作，当前展示前 150 个样本；请下载映射模板批量处理其余样本。</p>}</div>
            {activeStatus === "awaiting_mapping" && <div className="mapping-publish"><span>发布前校验：必须全部映射，且不能有两个样本映射到同一材料。发布后形成不可覆盖的分析版本。</span><button type="button" className="primary-button" disabled={busy === "publish" || unresolvedCount > 0 || duplicateCount > 0} onClick={publish}>{busy === "publish" ? <LoaderCircle size={15} className="spin" /> : <CheckCircle2 size={15} />}人工确认并发布给 GWAS</button></div>}
          </section>}

          {governanceOpen && <form className="genotype-governance" onSubmit={submitGovernance}><div><p>数据治理申请</p><h4>把异常留给可追溯的处理流程</h4><span>适用于材料尚未建档、样本映射冲突、参考坐标异常或表型字段/单位不合格。该申请不会自动向其他账号暴露原始基因型。</span></div><select value={governanceForm.request_type} onChange={(event) => setGovernanceForm({ ...governanceForm, request_type: event.target.value })}><option value="material_master">材料主档新建或核验</option><option value="mapping_conflict">样本材料映射冲突</option><option value="reference_review">参考坐标/染色体命名核验</option><option value="phenotype_governance">表型数据治理</option></select><textarea value={governanceForm.description} onChange={(event) => setGovernanceForm({ ...governanceForm, description: event.target.value })} placeholder="简要说明需要核验的样本、材料、字段或异常原因" required /><div><button type="button" className="secondary-button" onClick={() => setGovernanceOpen(false)}>取消</button><button className="primary-button" disabled={busy === "governance"}>{busy === "governance" ? <LoaderCircle size={15} className="spin" /> : <Send size={15} />}提交申请</button></div></form>}
        </>}
      </main>
    </section>
  </div>;
}
