import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Database, FileSpreadsheet, Link2, Upload } from "lucide-react";
import { jsonRequest, request } from "./api";

const DOMAINS = [
  { value: "germplasm", label: "种质主数据", effect: "缺少后，试验、表型和基因型文件中的材料无法稳定关联。" },
  { value: "trial", label: "试验与小区", effect: "缺少后，表型无法定位到年份、地点、处理、重复和小区。" },
  { value: "phenotype", label: "表型观测", effect: "缺少后，不能形成田间统计和材料性状证据。" },
  { value: "environment", label: "环境指标", effect: "缺少后，不能解释基因型×环境差异和跨点稳定性。" },
  { value: "management", label: "栽培管理", effect: "缺少后，施肥、灌溉等处理效应只能做有限解释。" },
];

function normalized(value) {
  return String(value || "").trim().toLowerCase().replace(/[\s_\-（）()\[\]]/g, "");
}

function suggestedField(column, fields) {
  const source = normalized(column);
  return fields.find((field) => {
    const candidates = [field.field_name, field.target_field, field.field_code?.split(".").at(-1), ...(field.aliases || [])];
    return candidates.some((candidate) => normalized(candidate) === source);
  })?.id || "";
}

function Step({ number, title, active, complete }) {
  return <div className={`real-intake-step${active ? " active" : ""}${complete ? " complete" : ""}`}>
    <span>{complete ? <CheckCircle2 size={15} /> : number}</span><strong>{title}</strong>
  </div>;
}

export default function InstitutionDataIntake({ projects, selectedProjectId, setSelectedProjectId, onChanged, onNotice }) {
  const [domain, setDomain] = useState("germplasm");
  const [semanticFields, setSemanticFields] = useState([]);
  const [batch, setBatch] = useState(null);
  const [profile, setProfile] = useState(null);
  const [mappings, setMappings] = useState([]);
  const [bindings, setBindings] = useState([]);
  const [validation, setValidation] = useState(null);
  const [issues, setIssues] = useState([]);
  const [publishResult, setPublishResult] = useState(null);
  const [busy, setBusy] = useState("");
  const inputRef = useRef(null);

  const domainInfo = DOMAINS.find((item) => item.value === domain) || DOMAINS[0];
  const requiredFields = useMemo(() => semanticFields.filter((field) => field.is_required), [semanticFields]);
  const mappedFieldIds = useMemo(() => new Set(mappings.filter((item) => item.mapping_action === "map").map((item) => item.semantic_field_id)), [mappings]);
  const boundFieldCodes = useMemo(() => new Set(bindings.filter((item) => item.field_code && item.value !== "").map((item) => item.field_code)), [bindings]);
  const missingRequired = requiredFields.filter((field) => !mappedFieldIds.has(field.id) && !boundFieldCodes.has(field.field_code));

  useEffect(() => {
    request(`/api/data-spine/semantic-fields?data_domain=${encodeURIComponent(domain)}`)
      .then((rows) => setSemanticFields(rows))
      .catch((error) => onNotice(error.message));
    setBatch(null);
    setProfile(null);
    setMappings([]);
    setBindings([]);
    setValidation(null);
    setIssues([]);
    setPublishResult(null);
  }, [domain]);

  async function upload(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !selectedProjectId) return;
    setBusy("upload");
    try {
      const created = await request(
        `/api/data-spine/projects/${encodeURIComponent(selectedProjectId)}/import-batches`,
        jsonRequest("POST", { display_name: file.name, data_domain: domain, notes: "真实机构数据接入" }),
      );
      const body = new FormData();
      body.append("file", file);
      const uploaded = await request(`/api/data-spine/import-batches/${created.id}/files`, { method: "POST", body });
      const nextProfile = uploaded.profile || { columns: [], sample_rows: [], row_count: 0 };
      setBatch(created);
      setProfile(nextProfile);
      setMappings((nextProfile.columns || []).map((column) => {
        const fieldId = suggestedField(column, semanticFields);
        return { source_column: column, semantic_field_id: fieldId || null, mapping_action: fieldId ? "map" : "preserve", transform_rule: {} };
      }));
      setValidation(null);
      setIssues([]);
      setPublishResult(null);
      onNotice(`已安全保存并解析 ${file.name}，共 ${nextProfile.row_count || 0} 行。请确认字段语义后再校验。`);
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  function updateMapping(index, field, value) {
    setMappings((current) => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      if (field === "mapping_action" && value !== "map") return { ...item, mapping_action: value, semantic_field_id: null };
      if (field === "semantic_field_id") return { ...item, semantic_field_id: value || null, mapping_action: value ? "map" : "preserve" };
      return { ...item, [field]: value };
    }));
  }

  async function saveAndValidate() {
    if (!batch) return;
    setBusy("validate");
    try {
      const bindingContext = Object.fromEntries(bindings.filter((item) => item.field_code && item.value !== "").map((item) => [item.field_code, item.value]));
      await request(`/api/data-spine/import-batches/${batch.id}/mapping`, jsonRequest("PUT", {
        profile_name: `${domainInfo.label}-${new Date().toLocaleDateString("zh-CN")}`,
        mappings,
        binding_context: bindingContext,
      }));
      const result = await request(`/api/data-spine/import-batches/${batch.id}/validate`, { method: "POST" });
      const problemRows = await request(`/api/data-spine/import-batches/${batch.id}/issues?limit=500`);
      setValidation(result);
      setIssues(problemRows);
      onNotice(result.failed_count
        ? `校验完成：${result.passed_count} 行通过，${result.failed_count} 行因缺字段或关联失败未通过。`
        : `校验完成：${result.passed_count} 行全部通过，可直接发布。`);
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function publish() {
    if (!batch || validation?.failed_count) return;
    setBusy("publish");
    try {
      const result = await request(`/api/data-spine/import-batches/${batch.id}/publish`, { method: "POST" });
      setPublishResult(result);
      onNotice(`发布完成：${result.published_count || validation?.passed_count || 0} 行已进入正式关联数据。`);
      await onChanged?.();
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  return <div className="real-intake page-stack">
    <section className="panel real-intake-hero">
      <div><p>正式机构数据接入</p><h2>任意列名，先映射语义，再形成可关联业务数据</h2><span>原始文件保留在当前机构的对象存储中；只有通过字段映射、稳定编号关联和逐行校验的数据，才会进入智能体可用数据层。</span></div>
      <div className="real-intake-project"><label>当前课题</label><select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.project_name}</option>)}</select></div>
    </section>

    <section className="real-intake-steps">
      <Step number="1" title="选择数据" active={!profile} complete={Boolean(profile)} />
      <Step number="2" title="字段映射" active={Boolean(profile) && !validation} complete={Boolean(validation)} />
      <Step number="3" title="关联校验" active={Boolean(validation) && !publishResult} complete={Boolean(publishResult)} />
      <Step number="4" title="正式发布" active={Boolean(publishResult)} complete={Boolean(publishResult)} />
    </section>

    <section className="panel">
      <div className="real-intake-heading"><div><p>01 · 数据类型与文件</p><h3>{domainInfo.label}</h3><span>{domainInfo.effect}</span></div><Database size={27} /></div>
      <div className="real-domain-grid">{DOMAINS.map((item) => <button key={item.value} type="button" className={domain === item.value ? "active" : ""} onClick={() => setDomain(item.value)}><strong>{item.label}</strong><span>{item.effect}</span></button>)}</div>
      {!projects.length ? <div className="empty-pending">当前机构没有课题。请让字段管理员先创建课题并分配成员。</div> : <div className="real-upload-row"><button className="primary-button" type="button" disabled={Boolean(busy)} onClick={() => inputRef.current?.click()}><Upload size={16} />{busy === "upload" ? "正在解析…" : "上传 CSV / TSV / Excel / JSON"}</button><input ref={inputRef} hidden type="file" accept=".csv,.tsv,.xlsx,.xls,.json" onChange={upload} /><span>单文件最多按数据类型限制为 250–500 MB；原始列不会因未映射而丢失。</span></div>}
    </section>

    {profile && <section className="panel">
      <div className="real-intake-heading"><div><p>02 · 字段映射</p><h3>{batch?.display_name}</h3><span>共 {profile.row_count || 0} 行、{profile.columns?.length || 0} 个源字段。无法归入核心语义的字段可选择“保留扩展”，无需强行删除。</span></div><FileSpreadsheet size={27} /></div>
      <div className="table-scroll"><table className="real-mapping-table"><thead><tr><th>机构原始字段</th><th>处理方式</th><th>平台语义字段</th><th>要求</th></tr></thead><tbody>{mappings.map((item, index) => {
        const semantic = semanticFields.find((field) => field.id === item.semantic_field_id);
        return <tr key={item.source_column}><td><strong>{item.source_column}</strong><small>{String(profile.sample_rows?.[0]?.record?.[item.source_column] ?? "暂无示例")}</small></td><td><select value={item.mapping_action} onChange={(event) => updateMapping(index, "mapping_action", event.target.value)}><option value="map">映射为标准语义</option><option value="preserve">保留为机构扩展</option><option value="ignore">本次忽略</option></select></td><td>{item.mapping_action === "map" ? <select value={item.semantic_field_id || ""} onChange={(event) => updateMapping(index, "semantic_field_id", event.target.value)}><option value="">请选择语义字段</option>{semanticFields.map((field) => <option key={field.id} value={field.id}>{field.field_name} · {field.field_code}</option>)}</select> : <span className="real-preserve-note">{item.mapping_action === "preserve" ? "原值进入扩展字段，不参与核心关联" : "仅保留在原始文件"}</span>}</td><td>{semantic?.is_required ? <b className="real-required">必需</b> : "—"}</td></tr>;
      })}</tbody></table></div>

      <div className="real-binding-box"><div><strong>整份文件共用的固定值</strong><span>例如文件中没有“试验年份”，但整份表都属于 2025 年，可在这里补一次，不必修改原文件。</span></div>{bindings.map((binding, index) => <div className="real-binding-row" key={index}><select value={binding.field_code} onChange={(event) => setBindings((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, field_code: event.target.value } : row))}><option value="">选择语义字段</option>{semanticFields.map((field) => <option value={field.field_code} key={field.id}>{field.field_name}</option>)}</select><input value={binding.value} placeholder="固定值" onChange={(event) => setBindings((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, value: event.target.value } : row))}/><button className="text-button danger" type="button" onClick={() => setBindings((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}>删除</button></div>)}<button className="secondary-button" type="button" onClick={() => setBindings((rows) => [...rows, { field_code: "", value: "" }])}>增加固定字段</button></div>

      {missingRequired.length > 0 && <div className="real-warning"><AlertTriangle size={17}/><div><strong>仍缺少 {missingRequired.length} 个必需语义字段</strong><span>{missingRequired.map((field) => field.field_name).join("、")}。可映射源字段或填写整表固定值；否则对应行不会发布。</span></div></div>}
      <button className="primary-button" type="button" disabled={Boolean(busy)} onClick={saveAndValidate}><Link2 size={16}/>{busy === "validate" ? "正在关联校验…" : "保存映射并校验关联"}</button>
    </section>}

    {validation && <section className="panel">
      <div className="real-intake-heading"><div><p>03 · 真实关联校验</p><h3>{validation.failed_count ? "存在待处理问题" : "全部记录可以发布"}</h3><span>只有同时找到真实材料、试验、处理、重复和小区的数据才算“有关联”，仅上传文件不计入功能就绪。</span></div>{validation.failed_count ? <AlertTriangle size={27}/> : <CheckCircle2 size={27}/>}</div>
      <div className="real-validation-summary"><span><b>{validation.staging_row_count}</b>总行数</span><span className="passed"><b>{validation.passed_count}</b>通过</span><span className={validation.failed_count ? "failed" : ""}><b>{validation.failed_count}</b>未通过</span></div>
      {issues.length > 0 && <div className="table-scroll"><table><thead><tr><th>源行</th><th>问题代码</th><th>原始字段/值</th><th>如何处理</th></tr></thead><tbody>{issues.map((issue) => <tr key={issue.id}><td>{issue.source_row_number ?? "—"}</td><td>{issue.error_code}</td><td>{issue.source_column || "—"} {issue.raw_value ? `：${issue.raw_value}` : ""}</td><td>{issue.detail}</td></tr>)}</tbody></table></div>}
      <button className="primary-button" type="button" disabled={Boolean(busy) || Boolean(validation.failed_count) || Boolean(publishResult)} onClick={publish}>{busy === "publish" ? "正在发布…" : publishResult ? "已发布" : "确认发布通过记录"}</button>
    </section>}
  </div>;
}
