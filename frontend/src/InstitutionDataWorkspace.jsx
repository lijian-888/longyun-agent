import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Database, FileSearch, HardDriveUpload, Link2, Upload } from "lucide-react";
import { request } from "./api";

const MIME_ACCEPT = {
  germplasm: ".csv,.xlsx,.json",
  pedigree: ".csv,.xlsx,.json",
  phenotype: ".csv,.xlsx,.json",
  environment: ".csv,.xlsx,.json",
  genotype: ".vcf,.vcf.gz,.zip",
  literature: ".pdf,.docx,.txt",
};

function bytes(value) {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(0, value || 0)} B`;
}

export default function InstitutionDataWorkspace({ onNotice }) {
  const [contracts, setContracts] = useState(null);
  const [config, setConfig] = useState(null);
  const [imports, setImports] = useState([]);
  const [datasetType, setDatasetType] = useState("germplasm");
  const [mapping, setMapping] = useState("");
  const [busy, setBusy] = useState(false);
  const [importFeedback, setImportFeedback] = useState(null);
  const [traceKey, setTraceKey] = useState("");
  const [trace, setTrace] = useState(null);
  const fileRef = useRef(null);

  async function load() {
    try {
      const [nextContracts, nextConfig, nextImports] = await Promise.all([
        request("/api/institution-data/contracts"),
        request("/api/institution-data/config"),
        request("/api/institution-data/imports"),
      ]);
      setContracts(nextContracts);
      setConfig(nextConfig);
      setImports(nextImports);
    } catch (error) {
      onNotice(error.message);
    }
  }

  useEffect(() => { load(); }, []);

  const selectedContract = useMemo(
    () => contracts?.datasets?.find((item) => item.id === datasetType),
    [contracts, datasetType],
  );

  async function upload(event) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      onNotice("请选择待导入文件。");
      return;
    }
    const compressedGenotype = datasetType === "genotype" && (/\.vcf\.gz$/i.test(file.name) || /\.zip$/i.test(file.name));
    const limit = compressedGenotype ? contracts.limits.genotype_archive_bytes : contracts.limits.regular_bytes;
    if (file.size > limit) {
      onNotice(`${file.name} 超过 ${compressedGenotype ? "2GB" : "200MB"} 上限。`);
      return;
    }
    const form = new FormData();
    form.append("dataset_type", datasetType);
    form.append("file", file);
    if (mapping.trim()) form.append("field_mapping", mapping.trim());
    setBusy(true);
    setImportFeedback(null);
    try {
      const result = await request("/api/institution-data/imports", { method: "POST", body: form });
      setImportFeedback({ kind: result.issue_count ? "warning" : "success", result });
      onNotice(result.issue_count
        ? `已导入 ${result.entity_count} 个实体，发现 ${result.issue_count} 个需要处理的问题。`
        : `已导入 ${result.entity_count} 个实体，原始文件和结构化数据均已安全落库。`);
      fileRef.current.value = "";
      await load();
    } catch (error) {
      setImportFeedback({ kind: "error", message: error.message });
      onNotice(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function searchTrace(event) {
    event.preventDefault();
    if (!traceKey.trim()) return;
    try {
      setTrace(await request(`/api/institution-data/trace/${encodeURIComponent(traceKey.trim())}`));
    } catch (error) {
      setTrace(null);
      onNotice(error.message);
    }
  }

  return <div className="page-stack institution-data-page">
    <section className="panel institution-data-intro">
      <div className="panel-title-row"><div><span className="eyebrow">机构独立数据平面</span><h2>六类育种数据统一导入</h2><p>原始文件进入当前机构私有 Bucket，结构化数据进入当前机构独立业务数据库。</p></div><Database size={28} /></div>
      {config && <div className="institution-plane-grid"><div><span>MinIO Bucket</span><strong>{config.minio_bucket}</strong></div><div><span>业务数据库</span><strong>{config.business_database}</strong></div><div><span>访问策略</span><strong>{config.access_policy?.effect === "private" ? "机构私有" : config.access_policy?.effect}</strong></div><div><span>状态</span><strong>{config.status}</strong></div></div>}
    </section>

    <section className="panel institution-upload-panel">
      <div className="panel-title-row"><div><h3><HardDriveUpload size={19} />上传并导入</h3><p>可使用标准列名，或填写“原始列名 → 标准字段” JSON 映射。</p></div></div>
      <form className="institution-upload-form" onSubmit={upload}>
        <label>数据类型<select value={datasetType} onChange={(event) => { setDatasetType(event.target.value); setImportFeedback(null); if (fileRef.current) fileRef.current.value = ""; }}>{contracts?.datasets?.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <label>文件<input ref={fileRef} type="file" accept={MIME_ACCEPT[datasetType]} /></label>
        <label className="institution-mapping-field">可选字段映射<textarea value={mapping} onChange={(event) => setMapping(event.target.value)} placeholder={'{"原材料号":"germplasm_id","名称":"name"}'} /></label>
        <div className="institution-contract-note"><strong>允许格式：{selectedContract?.formats?.join("、") || "-"}</strong><span>常规文件 ≤200MB；VCF.GZ/PLINK ZIP ≤2GB</span>{selectedContract?.standard_fields?.length > 0 && <small>标准字段：{selectedContract.standard_fields.join("、")}</small>}</div>
        <button className="primary-button" type="submit" disabled={busy}><Upload size={16} />{busy ? "正在上传与导入…" : "开始导入"}</button>
        {importFeedback && <div className={`institution-import-feedback ${importFeedback.kind}`}>
          {importFeedback.kind === "error"
            ? <><strong>导入未完成</strong><span>{importFeedback.message}</span></>
            : <><strong>{importFeedback.kind === "success" ? "导入完成" : "导入完成，但需要处理数据问题"}</strong><span>结构化实体：{importFeedback.result.entity_count}；问题：{importFeedback.result.issue_count}；数据库：{importFeedback.result.structured_database}</span>{importFeedback.result.issues?.slice(0, 20).map((issue) => <span key={issue.id} className="feedback-issue"><AlertTriangle size={14} />{issue.message} 受影响：{issue.affected_features?.join("、")}</span>)}</>}
        </div>}
      </form>
    </section>

    <section className="panel institution-trace-panel">
      <div className="panel-title-row"><div><h3><FileSearch size={19} />按实体标识追溯</h3><p>仅查询当前机构和当前课题下的实体、关系、原始批次和异常。</p></div></div>
      <form className="institution-trace-form" onSubmit={searchTrace}><input value={traceKey} onChange={(event) => setTraceKey(event.target.value)} placeholder="例如 HNNF-G001" /><button className="secondary-button" type="submit"><Link2 size={16} />查询关联</button></form>
      {trace && <div className="institution-trace-result"><div><strong>实体 {trace.entities.length}</strong>{trace.entities.map((item) => <code key={`${item.entity_type}-${item.entity_key}`}>{item.entity_type}:{item.entity_key}</code>)}</div><div><strong>关系 {trace.relations.length}</strong>{trace.relations.map((item, index) => <span key={`${item.relation_type}-${index}`}>{item.source_entity_key} —{item.relation_type}→ {item.target_entity_key} <em>{item.status}</em></span>)}</div><div><strong>问题 {trace.issues.length}</strong>{trace.issues.map((item, index) => <span className="trace-issue" key={`${item.issue_type}-${index}`}><AlertTriangle size={14} />{item.message}；受影响：{item.affected_features?.join("、")}</span>)}</div></div>}
    </section>

    <section className="panel institution-import-history"><div className="panel-title-row"><div><h3>导入批次</h3><p>Bucket 对象、文件哈希、结构化数量和异常数量均可追溯。</p></div></div><div className="table-scroll"><table><thead><tr><th>数据类型</th><th>文件</th><th>大小</th><th>结构化实体</th><th>问题</th><th>状态</th></tr></thead><tbody>{imports.map((item) => <tr key={item.id}><td>{contracts?.datasets?.find((entry) => entry.id === item.dataset_type)?.label || item.dataset_type}</td><td title={`${item.object_bucket}/${item.object_key}`}>{item.source_file_name}<small>{item.file_sha256?.slice(0, 12)}</small></td><td>{bytes(item.file_size_bytes)}</td><td>{item.row_count}</td><td className={item.issue_count ? "issue-count" : ""}>{item.issue_count}</td><td>{item.status}</td></tr>)}</tbody></table></div>{!imports.length && <div className="empty-pending">当前课题还没有机构数据导入批次。</div>}</section>
  </div>;
}
