import { useEffect, useMemo, useRef, useState } from "react";
import * as XLSX from "xlsx";
import { toPng } from "html-to-image";
import {
  BarChart3,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Database,
  Download,
  FilePlus2,
  FileSpreadsheet,
  FileText,
  FlaskConical,
  FolderInput,
  Gauge,
  Info,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Upload,
  UserRoundCog,
  UsersRound,
  X,
} from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { authorizedFetch, jsonRequest, request } from "./api";
import { keycloak } from "./auth";
import KnowledgeLibrary from "./KnowledgeLibrary";
import TrialPackageWorkspace from "./TrialPackageWorkspace";

const roles = [
  { id: "processor", label: "数据处理员", user: "数据处理员-张三", icon: ClipboardCheck, description: "导入、规则、质检、逐条审核与发布" },
  { id: "researcher", label: "科研人员", user: "科研人员-王研究员", icon: FlaskConical, description: "只查询有权课题的已发布数据、对比和下载报告" },
  { id: "admin", label: "字段管理员", user: "字段管理员-陈工", icon: UserRoundCog, description: "维护课题成员、字段模板、规则和公共知识" },
];

const traitLabels = {
  plant_height: "株高(cm)",
  thousand_grain_weight: "千粒重(g)",
  yield_per_mu: "亩产(kg/亩)",
  leaf_blast_score: "叶瘟等级",
  growth_duration: "全生育期(天)",
};

const DEFAULT_COMPARISON_FIELDS = ["plant_height", "thousand_grain_weight", "yield_per_mu", "leaf_blast_score"];
const CHART_COLORS = ["#19765d", "#d4a72c", "#5d7fbc", "#c87050", "#7d9c61", "#8a6c9d"];

function App({ user, accessRole = "data_processor", platformContext, onProjectChange }) {
  const roleId = accessRole === "field_admin" ? "admin" : "processor";
  const [page, setPage] = useState(accessRole === "field_admin" ? "projects" : "workbench");
  const [dashboard, setDashboard] = useState({});
  const [workbench, setWorkbench] = useState({ sources: [], pending_observations: [] });
  const [genotypeGovernanceRequests, setGenotypeGovernanceRequests] = useState([]);
  const [rules, setRules] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [templateRequests, setTemplateRequests] = useState([]);
  const [templateVersionId, setTemplateVersionId] = useState("");
  const [catalog, setCatalog] = useState([]);
  const [manageVarieties, setManageVarieties] = useState([]);
  const [researchRows, setResearchRows] = useState([]);
  const [filters, setFilters] = useState({ q: "", height_max: "", grain_weight_min: "", blast_max: "" });
  const [selected, setSelected] = useState([]);
  const [preview, setPreview] = useState(null);
  const [url, setUrl] = useState("");
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState(null);
  const [detail, setDetail] = useState(null);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const fileInput = useRef(null);
  const chartRef = useRef(null);
  const selectedRole = roles.find((item) => item.id === roleId) || roles[0];
  const actorName = user?.display_name || user?.username || selectedRole.user;
  const role = { ...selectedRole, user: actorName };

  async function loadBase() {
    try {
      if (roleId === "processor") {
        const [summary, board, traitCatalog, editableVarieties, templateList, governanceRows] = await Promise.all([
          request("/api/dashboard"),
          request("/api/workbench"),
          request("/api/catalog"),
          request("/api/manage/varieties"),
          request("/api/templates"),
          request("/api/genotype-governance-requests"),
        ]);
        setDashboard(summary);
        setWorkbench(board);
        setCatalog(traitCatalog.traits);
        setManageVarieties(editableVarieties);
        const intakeTemplates = templateList.filter((template) => template.intake_supported);
        setTemplates(intakeTemplates);
        setGenotypeGovernanceRequests(governanceRows);
        setTemplateVersionId((current) => intakeTemplates.some((template) => template.current_version_id === current) ? current : intakeTemplates[0]?.current_version_id || "");
      } else {
        const [summary, ruleList, traitCatalog, templateList, requests] = await Promise.all([
          request("/api/dashboard"),
          request("/api/rules"),
          request("/api/catalog"),
          request("/api/templates"),
          request("/api/template-change-requests"),
        ]);
        setDashboard(summary);
        setRules(ruleList);
        setCatalog(traitCatalog.traits);
        setTemplates(templateList);
        setTemplateRequests(requests);
      }
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function searchResearch(nextFilters = filters) {
    const params = new URLSearchParams();
    Object.entries(nextFilters).forEach(([key, value]) => value !== "" && params.set(key, value));
    try {
      const rows = await request(`/api/varieties?${params.toString()}`);
      setResearchRows(rows);
      setSelected((current) => current.filter((id) => rows.some((item) => item.id === id)));
    } catch (error) {
      setMessage(error.message);
    }
  }

  useEffect(() => {
    loadBase();
  }, [roleId]);

  useEffect(() => {
    setPage(accessRole === "field_admin" ? "projects" : "workbench");
  }, [accessRole]);

  async function uploadFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const data = new FormData();
    data.append("file", file);
    try {
      const result = await request(`/api/imports/upload?template_version_id=${encodeURIComponent(templateVersionId)}&actor=${encodeURIComponent(role.user)}`, { method: "POST", body: data });
      setPreview(result);
      setTemplateVersionId(result.source.template_version_id || templateVersionId);
      setMessage(result.template?.auto_switched
        ? `已根据文件表头自动切换为“${result.template.name} ${result.template.version}”，并完成解析。`
        : `已解析 ${file.name}，请核对候选字段后创建待处理草稿。`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      event.target.value = "";
    }
  }

  async function importUrl() {
    if (!url.trim()) return;
    try {
      const result = await request("/api/imports/url", jsonRequest("POST", { url, template_version_id: templateVersionId, actor: role.user }));
      setPreview(result);
      setMessage(result.source.parsing_status === "partial"
        ? "网页已读取，但发现无法可靠识别的图形字符；相关字段已保留原文，需人工核对后再创建草稿。"
        : "网页读取成功。仅解析该单个页面；请核对后再创建草稿。");
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function resolveGenotypeGovernanceRequest(requestId, status, resolutionNote) {
    try {
      const result = await request(`/api/genotype-governance-requests/${requestId}`, jsonRequest("PATCH", {
        status,
        resolution_note: resolutionNote,
      }));
      setGenotypeGovernanceRequests((rows) => rows.map((item) => item.id === requestId ? { ...item, ...result, resolved_by: role.user } : item));
      setMessage(status === "resolved" ? "治理申请已处理完成，原始基因型文件仍保持私有。" : "已更新治理申请状态，科研人员可在其私有工作区查看处理意见。");
    } catch (error) {
      setMessage(error.message);
    }
  }

  function updatePreviewCandidate(candidateIndex, field, value) {
    setPreview((current) => {
      const next = structuredClone(current);
      next.candidates[candidateIndex][field] = value;
      return next;
    });
  }

  function importPayload(candidate) {
    return {
      ...candidate,
      aliases: typeof candidate.aliases === "string"
        ? candidate.aliases.split(/[、,，]/).map((item) => item.trim()).filter(Boolean)
        : candidate.aliases || [],
      actor: role.user,
    };
  }

  async function commitPreview(candidateIndex = 0) {
    const candidate = preview?.candidates?.[candidateIndex];
    if (!candidate) return;
    try {
      const result = await request(`/api/imports/${preview.source.id}/commit`, jsonRequest("POST", importPayload(candidate)));
      setPreview(null);
      setMessage(`已创建 ${result.observations.length} 条待处理记录${result.skipped_duplicates?.length ? `，跳过 ${result.skipped_duplicates.length} 条重复记录` : ""}。请在审核列表中逐条确认或修改。`);
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function commitAllPreview() {
    const candidates = (preview?.candidates || []).filter((item) => item.variety_name?.trim());
    if (!candidates.length) {
      setMessage("没有可创建的记录，请先补充品种名称。");
      return;
    }
    try {
      let createdCount = 0;
      let skippedCount = 0;
      for (const candidate of candidates) {
        const result = await request(`/api/imports/${preview.source.id}/commit`, jsonRequest("POST", importPayload(candidate)));
        createdCount += result.observations.length;
        skippedCount += result.skipped_duplicates?.length || 0;
      }
      setPreview(null);
      setMessage(`已处理 ${candidates.length} 个品种：新建 ${createdCount} 条待处理记录${skippedCount ? `，跳过 ${skippedCount} 条重复记录` : ""}。`);
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function saveObservation() {
    if (!editing) return;
    try {
      await request(`/api/observations/${editing.id}`, jsonRequest("PATCH", { value_numeric: editing.observation_type === "numeric" ? Number(editing.value_numeric) : null, value_text: editing.observation_type === "text" ? editing.value_text : null, unit: editing.unit, original_value: editing.original_value, review_comment: editing.review_comment || "数据处理员核对后修改", actor: role.user }));
      setEditing(null);
      setMessage("表型记录已更新，修改前后值和原因已写入来源审核历史。 ");
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function publish(ids) {
    try {
      const result = await request("/api/observations/publish", jsonRequest("POST", { observation_ids: ids, actor: role.user }));
      setMessage(`已发布 ${result.published.length} 条；${result.blocked.length} 条仍留在待处理区。`);
      await loadBase();
      await searchResearch();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function createRule(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const ruleType = String(form.get("rule_type") || "quality");
    const optionalNumber = (field) => form.get(field) === "" ? undefined : Number(form.get(field));
    const config = ruleType === "quality"
      ? { trait_code: form.get("trait_code"), min: optionalNumber("min_value"), max: optionalNumber("max_value") }
      : ruleType === "mapping"
        ? { source_field: form.get("source_field"), target_trait_code: form.get("target_trait_code") }
        : ruleType === "unit"
          ? { trait_code: form.get("trait_code"), source_unit: form.get("source_unit"), target_unit: form.get("target_unit"), operation: form.get("unit_operation"), factor: optionalNumber("unit_factor") }
          : ruleType === "publish"
            ? { field: form.get("publish_field"), required: true }
            : ruleType === "name"
              ? { pattern: form.get("name_pattern") }
              : { trait_code: form.get("trait_code"), source_text: form.get("source_text"), standard_value: form.get("standard_value") };
    if (ruleType === "quality" && (config.min === undefined && config.max === undefined)) {
      setMessage("质量校验至少填写最小值或最大值。 ");
      return;
    }
    try {
      await request("/api/rules", jsonRequest("POST", { rule_code: form.get("rule_code"), rule_name: form.get("rule_name"), rule_type: form.get("rule_type"), severity: form.get("severity"), config, change_reason: form.get("change_reason"), created_by: role.user }));
      formElement.reset();
      setMessage("已新增规则版本；历史规则未被覆盖。 ");
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function retireRule(ruleId) {
    try {
      await request(`/api/rules/${ruleId}/retire?actor=${encodeURIComponent(role.user)}`, { method: "POST" });
      setMessage("规则已停用，只影响后续新导入数据。 ");
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function createTemplateVersion(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const optionalNumber = (name) => form.get(name) === "" ? null : Number(form.get(name));
    try {
      await request(`/api/templates/${form.get("template_id")}/versions`, jsonRequest("POST", {
        change_summary: form.get("change_summary"), action: form.get("action"), field_code: form.get("field_code"), field_name: form.get("field_name"), field_kind: form.get("field_kind"), category: form.get("category"), unit: form.get("unit"), aliases: String(form.get("aliases") || "").split(/[、,，]/).map((item) => item.trim()).filter(Boolean), required: form.get("required") === "on", min_value: optionalNumber("min_value"), max_value: optionalNumber("max_value"), severity: form.get("severity"), request_id: form.get("request_id") || null, actor: role.user,
      }));
      setMessage("已发布模板新版本；变更说明和字段快照已保留。数据员可选择新版本重新处理待处理来源。 ");
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function submitFieldRequest(sourceId, field, sampleValue) {
    try {
      await request("/api/template-change-requests", jsonRequest("POST", { source_review_id: sourceId, source_field: field, sample_value: sampleValue, request_note: "导入模板未识别该字段，请管理员判断是否纳入标准模板。", actor: role.user }));
      setMessage(`已将“${field}”提交管理员处理。`);
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function reprocessSource(sourceId) {
    try {
      const result = await request(`/api/imports/${sourceId}/reprocess?actor=${encodeURIComponent(role.user)}`, { method: "POST" });
      setPreview(result);
      setTemplateVersionId(result.source.template_version_id || templateVersionId);
      setMessage("已按管理员发布的最新模板重新解析，请确认后创建待处理记录。 ");
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function createManualRecord(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const trait = catalog.find((item) => item.code === form.get("trait_code"));
      const raw = String(form.get("original_value") || "");
      const numericMatch = raw.match(/-?\d+(?:\.\d+)?/);
      await request("/api/manual/record", jsonRequest("POST", { variety_id: form.get("variety_id") || null, variety_name: form.get("variety_name"), aliases: String(form.get("aliases") || "").split(/[、,，]/).map((item) => item.trim()).filter(Boolean), variety_type: form.get("variety_type") || null, trait_code: form.get("trait_code"), value_numeric: numericMatch ? Number(numericMatch[0]) : null, value_text: numericMatch ? null : raw, unit: trait?.unit || "", original_value: raw, source_reference: form.get("source_reference"), source_note: form.get("source_note"), actor: role.user }));
      event.currentTarget.reset();
      setMessage("已保存手工补录数据，现已进入待处理区，可逐条审核后发布。 ");
      await loadBase();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function openDetail(id) {
    try {
      setDetail(await request(`/api/varieties/${id}`));
    } catch (error) {
      setMessage(error.message);
    }
  }

  function toggleSelection(id) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length >= 5 ? current : [...current, id]);
  }

  function downloadCsv() {
    const rows = researchRows.map((item) => ({ 品种名称: item.variety_name, 别名: item.alias_names.join("、"), 株高_cm: item.traits.plant_height ?? "", 千粒重_g: item.traits.thousand_grain_weight ?? "", 亩产_kg每亩: item.traits.yield_per_mu ?? "", 叶瘟等级: item.traits.leaf_blast_score ?? "", 数据状态: item.data_status }));
    const text = XLSX.utils.sheet_to_csv(XLSX.utils.json_to_sheet(rows));
    downloadBlob(new Blob([`\uFEFF${text}`], { type: "text/csv;charset=utf-8" }), "水稻表型查询结果.csv");
  }

  function downloadXlsx() {
    const rows = researchRows.map((item) => ({ 品种名称: item.variety_name, 别名: item.alias_names.join("、"), 株高_cm: item.traits.plant_height ?? "", 千粒重_g: item.traits.thousand_grain_weight ?? "", 亩产_kg每亩: item.traits.yield_per_mu ?? "", 叶瘟等级: item.traits.leaf_blast_score ?? "", 生育期_天: item.traits.growth_duration ?? "" }));
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(rows), "查询结果");
    XLSX.writeFile(workbook, "水稻表型查询结果.xlsx");
  }

  async function downloadPng() {
    if (!chartRef.current) return;
    try {
      const dataUrl = await toPng(chartRef.current, { backgroundColor: "#ffffff", pixelRatio: 2 });
      const link = document.createElement("a");
      link.href = dataUrl;
      link.download = "水稻表型对比图.png";
      link.click();
    } catch (error) {
      setMessage(`图片导出失败：${error.message}`);
    }
  }

  async function downloadPdf() {
    try {
      const response = await authorizedFetch("/api/reports/pdf", jsonRequest("POST", { filters, rows: researchRows }));
      if (!response.ok) throw new Error("PDF报告生成失败");
      downloadBlob(await response.blob(), "水稻表型查询与分析报告.pdf");
    } catch (error) {
      setMessage(error.message);
    }
  }

  const navItems = roleId === "processor"
    ? [{ id: "workbench", label: "数据处理工作台", icon: FolderInput }, { id: "trial-packages", label: "区域试验数据导入", icon: FileSpreadsheet }, { id: "manual", label: "手工补录", icon: FilePlus2 }, { id: "genotype-governance", label: "基因型治理申请", icon: FlaskConical }]
    : roleId === "researcher"
      ? [{ id: "research", label: "科研查询与分析", icon: Search }]
    : [{ id: "projects", label: "课题与账号", icon: UsersRound }, { id: "templates", label: "标准模板管理", icon: ShieldCheck }, { id: "rules", label: "规则版本与字段映射", icon: SlidersHorizontal }, { id: "knowledge", label: "公共知识库", icon: BookOpen }, { id: "overview", label: "系统概览", icon: LayoutDashboard }];

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark brand-logo-mark" aria-hidden="true"><img src="/brand/longyun-agent-logo.png" alt="" /></div>
        <div><strong>隆耘 Agent</strong><span>水稻育种研究与数据治理</span></div>
      </div>
      <WorkspaceContext platformContext={platformContext} onProjectChange={onProjectChange} />
      <nav>{navItems.map((item) => <button key={item.id} className={page === item.id ? "nav-item active" : "nav-item"} onClick={() => { setPage(item.id); setAccountMenuOpen(false); }}><item.icon size={18} />{item.label}<ChevronRight size={15} /></button>)}</nav>
      <div className="sidebar-bottom">
        {accountMenuOpen && <div className="sidebar-account-menu">
          <div><strong>{role.user}</strong><span>{role.label} · 已验证登录</span></div>
          <button type="button" onClick={() => keycloak.logout({ redirectUri: window.location.origin })}><LogOut size={15} />退出登录</button>
        </div>}
        <button type="button" className={accountMenuOpen ? "sidebar-account expanded" : "sidebar-account"} onClick={() => setAccountMenuOpen((open) => !open)} aria-expanded={accountMenuOpen}>
          <UsersRound size={17} /><span><strong>{role.user}</strong><small>{role.label}</small></span><ChevronDown size={16} />
        </button>
        <div className="local-note"><ShieldCheck size={18} /><div><strong>{platformContext?.institution?.name || "海南南繁"}</strong><span>单机构环境 · 课题与私人会话独立隔离</span></div></div>
      </div>
    </aside>
    <main className="main">
      <header className="topbar"><div><p>{platformContext?.institution?.name || "海南南繁"} · {platformContext?.projects?.find((item) => item.id === platformContext.active_project_id)?.project_name || "课题工作区"}</p><h1>{navItems.find((item) => item.id === page)?.label || "隆耘平台"}</h1></div><div className="top-actions"><button className="icon-button" title="刷新数据" onClick={loadBase}><RefreshCw size={18} /></button></div></header>
      {message && <div className="toast"><Info size={17} /><span>{message}</span><button onClick={() => setMessage("")}><X size={16} /></button></div>}
      {page === "workbench" && <Workbench dashboard={dashboard} workbench={workbench} preview={preview} url={url} setUrl={setUrl} fileInput={fileInput} uploadFile={uploadFile} importUrl={importUrl} templates={templates} templateVersionId={templateVersionId} setTemplateVersionId={setTemplateVersionId} updatePreviewCandidate={updatePreviewCandidate} commitPreview={commitPreview} commitAllPreview={commitAllPreview} submitFieldRequest={submitFieldRequest} reprocessSource={reprocessSource} setEditing={setEditing} publish={publish} onOpenTrialPackages={() => setPage("trial-packages")} />}
      {page === "trial-packages" && <TrialPackageWorkspace onNotice={setMessage} />}
      {page === "genotype-governance" && <GenotypeGovernanceQueue requests={genotypeGovernanceRequests} onResolve={resolveGenotypeGovernanceRequest} />}
      {page === "rules" && <Rules rules={rules} catalog={catalog} actor={role.user} createRule={createRule} retireRule={retireRule} readOnly={false} />}
      {page === "templates" && <TemplateCenter templates={templates} requests={templateRequests} createTemplateVersion={createTemplateVersion} />}
      {page === "manual" && <ManualEntry createManualRecord={createManualRecord} varieties={manageVarieties} catalog={catalog} />}
      {page === "research" && <Research filters={filters} setFilters={setFilters} searchResearch={searchResearch} rows={researchRows} selected={selected} toggleSelection={toggleSelection} openDetail={openDetail} downloadCsv={downloadCsv} downloadXlsx={downloadXlsx} downloadPng={downloadPng} downloadPdf={downloadPdf} chartRef={chartRef} catalog={catalog} />}
      {page === "knowledge" && <KnowledgeLibrary adminMode onNotice={setMessage} />}
      {page === "overview" && <Overview dashboard={dashboard} rules={rules} />}
      {page === "projects" && <ProjectAdministration platformContext={platformContext} onProjectChange={onProjectChange} onNotice={setMessage} />}
    </main>
    {editing && <EditModal observation={editing} setObservation={setEditing} onClose={() => setEditing(null)} onSave={saveObservation} />}
    {detail && <DetailDrawer detail={detail} onClose={() => setDetail(null)} />}
  </div>;
}

function WorkspaceContext({ platformContext, onProjectChange }) {
  const projects = platformContext?.projects || [];
  return <section className="workspace-context" aria-label="当前海南南繁课题">
    <span>{platformContext?.institution?.name || "海南南繁"}</span>
    <label>当前课题<select value={platformContext?.active_project_id || ""} onChange={(event) => onProjectChange?.(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.project_name}</option>)}</select></label>
  </section>;
}

function ProjectAdministration({ platformContext, onProjectChange, onNotice }) {
  const [projects, setProjects] = useState(platformContext?.projects || []);
  const [accounts, setAccounts] = useState([]);
  const [members, setMembers] = useState([]);
  const [audits, setAudits] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState(platformContext?.active_project_id || "");
  const [candidateUsername, setCandidateUsername] = useState("");

  async function loadDirectory() {
    try {
      const [projectRows, accountRows, auditRows] = await Promise.all([
        request("/api/projects"), request("/api/accounts"), request("/api/permission-audits"),
      ]);
      setProjects(projectRows);
      setAccounts(accountRows);
      setAudits(auditRows);
      setSelectedProjectId((current) => projectRows.some((item) => item.id === current) ? current : projectRows[0]?.id || "");
    } catch (error) {
      onNotice(error.message);
    }
  }

  async function loadMembers(projectId) {
    if (!projectId) return setMembers([]);
    try {
      setMembers(await request(`/api/projects/${projectId}/members`));
    } catch (error) {
      onNotice(error.message);
    }
  }

  useEffect(() => { void loadDirectory(); }, []);
  useEffect(() => { void loadMembers(selectedProjectId); }, [selectedProjectId]);

  async function createProject(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const project = await request("/api/projects", jsonRequest("POST", {
        project_code: form.get("project_code"), project_name: form.get("project_name"), description: form.get("description") || "",
      }));
      event.currentTarget.reset();
      onNotice(`已建立课题“${project.project_name}”。`);
      await onProjectChange(project.id);
    } catch (error) {
      onNotice(error.message);
    }
  }

  async function addMember() {
    if (!candidateUsername || !selectedProjectId) return;
    try {
      await request(`/api/projects/${selectedProjectId}/members/${encodeURIComponent(candidateUsername)}`, jsonRequest("PUT", { member_role: "researcher" }));
      setCandidateUsername("");
      await Promise.all([loadMembers(selectedProjectId), loadDirectory()]);
      onNotice("科研人员已加入当前课题，权限操作已记录。 ");
    } catch (error) {
      onNotice(error.message);
    }
  }

  async function removeMember(username) {
    try {
      await request(`/api/projects/${selectedProjectId}/members/${encodeURIComponent(username)}`, { method: "DELETE" });
      await Promise.all([loadMembers(selectedProjectId), loadDirectory()]);
      onNotice("已移除课题成员，历史会话仍归原账号且不会转给其他账号。 ");
    } catch (error) {
      onNotice(error.message);
    }
  }

  async function toggleAccount(account) {
    try {
      await request(`/api/accounts/${encodeURIComponent(account.username)}`, jsonRequest("PATCH", { active: !account.active }));
      await loadDirectory();
      onNotice(`账号 ${account.username} 已${account.active ? "停用" : "启用"}。`);
    } catch (error) {
      onNotice(error.message);
    }
  }

  const selectedProject = projects.find((item) => item.id === selectedProjectId);
  const availableResearchers = accounts.filter((account) => account.business_role === "researcher" && account.active && !members.some((member) => member.username === account.username));
  const roleNames = { researcher: "科研人员", data_processor: "数据处理员", field_admin: "字段管理员" };
  const actionNames = { project_created: "创建课题", project_updated: "修改课题", project_member_added: "添加成员", project_member_updated: "修改成员", project_member_removed: "移除成员", account_activated: "启用账号", account_deactivated: "停用账号" };
  return <div className="page-stack project-admin-page">
    <section className="panel"><PanelTitle icon={UsersRound} title="海南南繁课题与账号目录" note="机构固定为海南南繁。科研人员按课题成员关系访问；数据处理员和字段管理员按岗位进入课题，所有变更均留痕。" />
      <div className="project-admin-grid"><form className="project-create-form" onSubmit={createProject}><h3>建立课题</h3><input name="project_code" required placeholder="课题编号，例如 HNNF-2026-01" /><input name="project_name" required placeholder="课题名称" /><textarea name="description" placeholder="研究目标或数据范围" /><button className="primary-button" type="submit"><Plus size={16} />建立课题</button></form>
      <div className="project-member-panel"><h3>课题成员</h3><label>管理课题<select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>{projects.map((project) => <option value={project.id} key={project.id}>{project.project_code} · {project.project_name}</option>)}</select></label><div className="member-add-row"><select value={candidateUsername} onChange={(event) => setCandidateUsername(event.target.value)}><option value="">选择科研人员</option>{availableResearchers.map((account) => <option value={account.username} key={account.username}>{account.display_name} · {account.username}</option>)}</select><button className="secondary-button" type="button" disabled={!candidateUsername} onClick={addMember}>加入课题</button></div><div className="member-list">{members.length ? members.map((member) => <div key={member.id}><span><strong>{member.display_name}</strong><small>{member.username}</small></span><button className="text-button danger" onClick={() => removeMember(member.username)}>移除</button></div>) : <p>当前课题尚未配置科研人员。</p>}</div><small className="project-access-note">当前：{selectedProject?.project_name || "—"}；成员只能读取本课题数据及自己的私人会话。</small></div></div>
    </section>
    <section className="panel"><PanelTitle icon={UserRoundCog} title="三类业务账号" note="身份和角色由 Keycloak 统一认证；这里维护海南南繁应用访问状态，不保存或重置密码。" /><div className="table-scroll"><table><thead><tr><th>账号</th><th>姓名</th><th>业务角色</th><th>身份绑定</th><th>应用状态</th><th>操作</th></tr></thead><tbody>{accounts.map((account) => <tr key={account.username}><td>{account.username}</td><td>{account.display_name}</td><td>{roleNames[account.business_role] || account.business_role}</td><td>{account.identity_bound ? "已登录绑定" : "待首次登录"}</td><td>{account.active ? "启用" : "停用"}</td><td><button className={`text-button ${account.active ? "danger" : ""}`} onClick={() => toggleAccount(account)}>{account.active ? "停用" : "启用"}</button></td></tr>)}</tbody></table></div></section>
    <section className="panel"><PanelTitle icon={ListChecks} title="权限操作记录" note="记录课题创建、成员变更和账号启停，便于验收与追溯。" /><div className="table-scroll"><table><thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>课题</th></tr></thead><tbody>{audits.length ? audits.map((audit) => <tr key={audit.id}><td>{new Date(audit.created_at).toLocaleString("zh-CN")}</td><td>{audit.actor_name}</td><td>{actionNames[audit.action] || audit.action}</td><td>{audit.target_id}</td><td>{projects.find((project) => project.id === audit.project_id)?.project_name || "全局账号"}</td></tr>) : <EmptyRow colSpan={5} text="暂无权限变更记录。" />}</tbody></table></div></section>
  </div>;
}

function Workbench({ dashboard, workbench, preview, url, setUrl, fileInput, uploadFile, importUrl, templates, templateVersionId, setTemplateVersionId, updatePreviewCandidate, commitPreview, commitAllPreview, submitFieldRequest, reprocessSource, setEditing, publish, onOpenTrialPackages }) {
  return <div className="page-stack">
    <section className="metric-grid">
      <Metric label="品种记录" value={dashboard.varieties ?? "-"} note="标准名称与别名可检索" icon={Database} />
      <Metric label="已发布表型" value={dashboard.published ?? "-"} note="科研查询页可见" icon={CheckCircle2} tone="good" />
      <Metric label="待处理记录" value={dashboard.pending ?? "-"} note="按品种展开、审核和发布" icon={ListChecks} tone="warn" />
      <Metric label="阻断异常" value={dashboard.blocked ?? "-"} note="不会进入正式查询结果" icon={ShieldCheck} tone="danger" />
    </section>
    <section className="panel import-panel"><div className="panel-title-row"><PanelTitle icon={FolderInput} title="选择标准模板后导入" note="一个导入任务只使用一套模板；通过校验的数据仍需数据处理员确认后才会进入对应正式表。" /><button className="secondary-button" type="button" onClick={onOpenTrialPackages}><FileSpreadsheet size={16} />导入区域试验资料包</button></div>
      <div className="template-picker"><label>处理标准模板<select value={templateVersionId} onChange={(event) => setTemplateVersionId(event.target.value)}>{templates.map((template) => <option key={template.current_version_id} value={template.current_version_id}>{template.template_name} · {template.current_version} · 写入 {template.target_table}</option>)}</select></label></div>
      <div className="import-actions"><div className="url-box"><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="粘贴单个品种详情页 URL" /><button className="secondary-button" onClick={importUrl}>读取网页</button></div><span>或</span><button className="primary-button" onClick={() => fileInput.current?.click()}><Upload size={16} />上传 HTML / PDF / Excel / CSV</button><input ref={fileInput} hidden type="file" accept=".html,.htm,.pdf,.xlsx,.xls,.csv" onChange={uploadFile} /></div>
      <div className="source-pills">{workbench.sources.slice(0, 3).map((source) => source.template_version !== "-" ? <button key={source.id} title="按最新模板重新处理此原始来源" onClick={() => reprocessSource(source.id)}><FileText size={14} />{source.source_name} · {source.template_version}</button> : <span key={source.id}><FileText size={14} />{source.source_name} · 历史来源</span>)}</div>
    </section>
    {preview && <ImportPreview preview={preview} updatePreviewCandidate={updatePreviewCandidate} commitPreview={commitPreview} commitAllPreview={commitAllPreview} submitFieldRequest={submitFieldRequest} />}
    <PendingReviewGroups observations={workbench.pending_observations} setEditing={setEditing} publish={publish} />
  </div>;
}

function GenotypeGovernanceQueue({ requests, onResolve }) {
  const [drafts, setDrafts] = useState({});
  const labels = {
    material_master: "材料主档补充",
    mapping_conflict: "样本映射冲突",
    reference_review: "参考坐标确认",
    phenotype_governance: "表型数据治理",
  };
  const statusLabels = {
    submitted: "待受理",
    accepted: "处理中",
    needs_info: "需补充信息",
    resolved: "已处理",
  };
  return <div className="page-stack genotype-governance-page">
    <section className="panel genotype-governance-intro">
      <PanelTitle icon={FlaskConical} title="基因型数据治理申请" note="这里只处理材料主档、样本映射、参考版本和表型治理事项。科研人员的原始 VCF、PLINK 文件、文件路径和哈希均不会展示或下载。" />
      <div className="governance-stat-row"><span>待处理 {requests.filter((item) => item.status === "submitted" || item.status === "needs_info").length} 项</span><span>处理中 {requests.filter((item) => item.status === "accepted").length} 项</span><span>已处理 {requests.filter((item) => item.status === "resolved").length} 项</span></div>
    </section>
    <section className="panel genotype-governance-list">
      {!requests.length && <div className="empty-pending">当前没有基因型数据治理申请。</div>}
      {requests.map((item) => {
        const draft = drafts[item.id] || { status: item.status === "submitted" ? "accepted" : item.status, note: item.resolution_note || "" };
        return <article className="genotype-governance-card" key={item.id}>
          <header><div><strong>{labels[item.request_type] || item.request_type}</strong><small>{item.asset_title} {item.version_number ? `· v${item.version_number}` : ""}</small></div><Status value={item.status} label={statusLabels[item.status] || item.status} /></header>
          <p>{item.description}</p>
          <div className="governance-samples"><span>受影响样本</span>{item.affected_samples?.length ? item.affected_samples.map((sample) => <code key={sample}>{sample}</code>) : <em>未指定</em>}</div>
          {item.status !== "resolved" ? <div className="governance-resolution">
            <select value={draft.status} onChange={(event) => setDrafts((all) => ({ ...all, [item.id]: { ...draft, status: event.target.value } }))}>
              <option value="accepted">受理并处理中</option>
              <option value="needs_info">请科研人员补充信息</option>
              <option value="resolved">已处理完成</option>
            </select>
            <textarea value={draft.note} onChange={(event) => setDrafts((all) => ({ ...all, [item.id]: { ...draft, note: event.target.value } }))} placeholder="填写处理意见；将同步回科研人员的私有工作区。" />
            <button className="primary-button" type="button" disabled={!draft.note.trim()} onClick={() => onResolve(item.id, draft.status, draft.note)}>保存处理意见</button>
          </div> : <div className="governance-resolved"><strong>处理意见</strong><p>{item.resolution_note || "已处理"}</p><small>{item.resolved_by || "数据处理员"} 已完成此申请</small></div>}
        </article>;
      })}
    </section>
  </div>;
}

function PendingReviewGroups({ observations, setEditing, publish }) {
  const [expandedVarieties, setExpandedVarieties] = useState([]);
  const groups = useMemo(() => {
    const grouped = new Map();
    observations.forEach((item) => {
      const key = item.variety_id || item.variety_name;
      if (!grouped.has(key)) grouped.set(key, { id: key, name: item.variety_name, records: [] });
      grouped.get(key).records.push(item);
    });
    return [...grouped.values()].sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
  }, [observations]);
  const allExpanded = groups.length > 0 && groups.every((group) => expandedVarieties.includes(group.id));
  const toggleGroup = (id) => setExpandedVarieties((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleAll = () => setExpandedVarieties(allExpanded ? [] : groups.map((group) => group.id));

  return <section className="panel pending-review-panel"><div className="panel-title-row"><PanelTitle icon={ClipboardCheck} title="待处理与逐条审核" note="按品种分别审核和发布；异常记录不会自动清空或发布。" />{groups.length > 0 && <button className="secondary-button" onClick={toggleAll}>{allExpanded ? "收起全部品种" : "展开全部品种"}</button>}</div>
    {groups.length ? <div className="review-groups">{groups.map((group) => {
      const isExpanded = expandedVarieties.includes(group.id);
      const publishable = group.records.filter((item) => item.quality_status !== "blocked");
      const blockedCount = group.records.length - publishable.length;
      return <section className="review-group" key={group.id}><header><button className="review-group-toggle" onClick={() => toggleGroup(group.id)} aria-expanded={isExpanded}><span className="review-group-chevron">{isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}</span><span><strong>{group.name}</strong><small>{group.records.length} 条待处理记录 · 可发布 {publishable.length} 条{blockedCount ? ` · 阻断 ${blockedCount} 条` : ""}</small></span></button><button className="primary-button" disabled={!publishable.length} onClick={() => publish(publishable.map((item) => item.id))}>发布该品种可通过记录</button></header>{isExpanded && <div className="table-scroll"><table><thead><tr><th>性状</th><th>标准值</th><th>原始值</th><th>质量状态</th><th>问题</th><th>操作</th></tr></thead><tbody>{group.records.map((item) => <tr key={item.id}><td>{item.trait_name}</td><td>{item.value_numeric ?? item.value_text ?? "-"} {item.unit}</td><td>{item.original_value}</td><td><Status value={item.quality_status} /></td><td>{item.issues.map((issue) => issue.message).join("；") || "通过基础校验"}</td><td><button className="text-button" onClick={() => setEditing(item)}>编辑</button>{item.quality_status !== "blocked" && <button className="text-button" onClick={() => publish([item.id])}>发布本条</button>}</td></tr>)}</tbody></table></div>}</section>;
    })}</div> : <div className="empty-pending">当前没有待处理记录。导入数据或手工补录后会按品种显示在这里。</div>}
  </section>;
}

function ImportPreview({ preview, updatePreviewCandidate, commitPreview, commitAllPreview, submitFieldRequest }) {
  const [activeCandidateIndex, setActiveCandidateIndex] = useState(0);
  const candidates = preview.candidates || [];
  const candidateIndex = Math.min(activeCandidateIndex, Math.max(candidates.length - 1, 0));
  const candidate = candidates[candidateIndex] || { observations: [] };
  const basicFields = [
    ["variety_type", "品种类型"], ["breeding_unit", "选育单位"], ["approval_number", "审定编号"],
    ["approval_year", "审定年份"], ["suitable_region", "适宜区域"], ["female_parent", "母本"], ["male_parent", "父本"],
  ];
  return <section className="panel preview-panel"><PanelTitle icon={FileSpreadsheet} title="原始内容与解析结果对照" note="先保留原始内容，再确认字段与标准值。基础信息和表型数据会一并创建为待处理草稿。" />
    {candidates.length > 1 && <label className="candidate-selector">Excel 记录<select value={candidateIndex} onChange={(event) => setActiveCandidateIndex(Number(event.target.value))}>{candidates.map((item, index) => <option key={`${item.variety_name}-${index}`} value={index}>第 {index + 2} 行 · {item.variety_name || "未识别品种名称"}</option>)}</select></label>}
    <div className="split-review"><div className="raw-pane"><span className="eyebrow">原始来源 · {preview.source.source_type}</span><pre>{preview.source.raw_text.slice(0, 5000) || "未提取到文字"}</pre></div><div className="parsed-pane"><span className="eyebrow">解析候选</span>{candidate.parser_warnings?.map((warning) => <div className="parser-warning" key={warning}><Info size={15} />{warning}</div>)}{candidate.unmapped_fields?.length > 0 && <div className="unmapped-fields"><strong>未识别字段</strong>{candidate.unmapped_fields.map((item) => <button key={item.field} className="secondary-button" onClick={() => submitFieldRequest(preview.source.id, item.field, item.sample_value)}>{item.field}：提交管理员处理</button>)}</div>}<label>品种名称<input value={candidate.variety_name || ""} onChange={(event) => updatePreviewCandidate(candidateIndex, "variety_name", event.target.value)} placeholder="无法识别时可手工填写" /></label><label>别名（用顿号或逗号分隔）<input value={Array.isArray(candidate.aliases) ? candidate.aliases.join("、") : candidate.aliases || ""} onChange={(event) => updatePreviewCandidate(candidateIndex, "aliases", event.target.value)} /></label><div className="basic-info-grid">{basicFields.map(([field, label]) => <label key={field}>{label}<input value={candidate[field] || ""} onChange={(event) => updatePreviewCandidate(candidateIndex, field, event.target.value)} placeholder="未识别时可补充" /></label>)}</div><div className="candidate-list">{candidate.observations.map((item, index) => <div key={`${item.trait_code}-${index}`}><strong>{item.trait_name}</strong><span>{item.value_numeric ?? item.value_text} {item.unit}</span><small>{item.original_value}</small>{item.conversion_suggestion && <small className={item.requires_confirmation ? "conversion-suggestion needs-confirmation" : "conversion-suggestion"}>{item.requires_confirmation ? "待确认：" : "换算："}{item.conversion_suggestion}</small>}</div>)}</div><div className="preview-actions">{candidates.length > 1 ? <button className="primary-button" onClick={commitAllPreview}><ClipboardCheck size={16} />创建全部 {candidates.length} 个品种待处理草稿</button> : <button className="primary-button" onClick={() => commitPreview(candidateIndex)}><ClipboardCheck size={16} />创建待处理草稿</button>}{candidates.length > 1 && <button className="secondary-button" onClick={() => commitPreview(candidateIndex)}>仅创建当前第 {candidateIndex + 2} 行</button>}</div></div></div>
  </section>;
}

function TemplateCenter({ templates, requests, createTemplateVersion }) {
  const structuredOrder = ["germplasm_master", "pedigree_relationship", "field_trial_package", "single_plant_master", "genotype_dataset", "knowledge_document"];
  const structuredTemplates = templates.filter((item) => item.template_group === "structured_governance").sort((left, right) => structuredOrder.indexOf(left.template_code) - structuredOrder.indexOf(right.template_code));
  const compatibilityTemplates = templates.filter((item) => item.template_group !== "structured_governance");
  const defaultTemplate = structuredTemplates[0] || templates[0];
  const [selectedTemplateId, setSelectedTemplateId] = useState(defaultTemplate?.id || "");
  const selectedTemplate = templates.find((item) => item.id === selectedTemplateId) || defaultTemplate;
  useEffect(() => {
    if (defaultTemplate && !templates.some((item) => item.id === selectedTemplateId)) setSelectedTemplateId(defaultTemplate.id);
  }, [templates, selectedTemplateId, defaultTemplate]);
  const pendingRequests = requests.filter((item) => item.status === "pending");
  const kindLabel = { identifier: "业务标识", basic: "基础字段", attribute: "属性字段", trait: "性状字段" };
  const templateCards = (items) => items.map((item) => <button key={item.id} className={selectedTemplate?.id === item.id ? "template-card selected" : "template-card"} onClick={() => setSelectedTemplateId(item.id)}><span>{item.data_scope}</span><strong>{item.template_name}</strong><small>{item.current_version} · 目标表：{item.target_table}</small><p>{item.description}</p></button>);
  return <div className="page-stack template-admin-page">
    <section className="panel template-admin-intro"><PanelTitle icon={ShieldCheck} title="六套结构化模板已恢复" note="模板统一服务海南南繁，用于管理语义字段、别名、必填规则和版本；具体业务数据仍按当前课题归属。" /><div className="template-group-stats"><span>结构化治理模板 <strong>{structuredTemplates.length}</strong></span><span>兼容导入模板 <strong>{compatibilityTemplates.length}</strong></span></div></section>
    <section className="template-group"><header><strong>结构化治理模板</strong><span>种质、系谱、试验、单株、基因型与知识文献</span></header><div className="template-grid">{templateCards(structuredTemplates)}</div></section>
    {compatibilityTemplates.length > 0 && <details className="template-compatibility"><summary>兼容导入模板 · {compatibilityTemplates.length} 套（现有网页、Excel 与 CSV 导入继续使用）</summary><div className="template-grid">{templateCards(compatibilityTemplates)}</div></details>}
    {selectedTemplate && <section className="panel"><PanelTitle icon={SlidersHorizontal} title={`${selectedTemplate.template_name} · ${selectedTemplate.current_version}`} note={selectedTemplate.change_summary || "已发布模板"} />
      <div className="template-meta"><span>目标数据表：<strong>{selectedTemplate.target_table}</strong></span><span>标准字段：<strong>{selectedTemplate.fields.length}</strong> 个</span></div>
      <div className="field-chip-list">{selectedTemplate.fields.map((field) => <span key={field.code} title={`内部编号：${field.code}；别名：${field.aliases?.join("、") || "无"}`}><small>{kindLabel[field.kind] || "字段"}</small>{field.name}{field.unit ? ` (${field.unit})` : ""}{field.required ? " *" : ""}</span>)}</div>
      <form key={selectedTemplate.id} className="template-version-form" onSubmit={createTemplateVersion}><input type="hidden" name="template_id" value={selectedTemplate.id} /><select name="request_id" defaultValue=""><option value="">不关联字段申请</option>{pendingRequests.filter((item) => item.template_id === selectedTemplate.id).map((item) => <option key={item.id} value={item.id}>{item.source_field} · 示例：{item.sample_value}</option>)}</select><select name="action" defaultValue="add_field"><option value="add_field">新增标准字段</option><option value="update_field">更新已有字段</option></select><select name="field_kind" defaultValue={selectedTemplate.intake_supported ? "trait" : "attribute"}><option value="identifier">业务标识</option><option value="basic">基础字段</option><option value="attribute">属性字段</option><option value="trait">性状字段</option></select><input name="field_code" placeholder="内部字段编号（更新字段时必填）" /><input name="field_name" required placeholder="标准语义字段名，例如 材料来源" /><input name="category" defaultValue={selectedTemplate.intake_supported ? "扩展性状" : "扩展字段"} placeholder="字段分类" /><input name="unit" placeholder="标准单位（无单位可留空）" /><input name="aliases" placeholder="原始列名别名，用逗号分隔" /><label><input name="required" type="checkbox" />设为必填字段</label><input name="min_value" type="number" step="any" placeholder="最小值（仅数值字段）" /><input name="max_value" type="number" step="any" placeholder="最大值（仅数值字段）" /><select name="severity" defaultValue="warning"><option value="warning">超范围预警</option><option value="block">超范围阻断</option></select><input name="change_summary" required placeholder="本次修改说明及影响范围" /><button className="primary-button" type="submit"><Plus size={16} />发布新版本</button></form>
    </section>}
    <section className="panel"><PanelTitle icon={ListChecks} title="数据员提交的字段处理申请" note="管理员决定是否纳入某个模板；发布新版本时可关联申请并自动标记为已处理。" /><div className="table-scroll"><table><thead><tr><th>模板</th><th>原始字段</th><th>示例值</th><th>提交人</th><th>状态</th></tr></thead><tbody>{requests.map((item) => <tr key={item.id}><td>{item.template_name}</td><td>{item.source_field}</td><td>{item.sample_value || "-"}</td><td>{item.submitted_by}</td><td><Status value={item.status} /></td></tr>)}</tbody></table></div></section>
  </div>;
}

function Rules({ rules, catalog, createRule, retireRule, readOnly }) {
  const [ruleType, setRuleType] = useState("quality");
  const [selectedTrait, setSelectedTrait] = useState("plant_height");
  const formRef = useRef(null);
  const trait = catalog.find((item) => item.code === selectedTrait);
  const traitOptions = <>{catalog.map((item) => <option key={item.code} value={item.code}>{item.name}{item.unit ? ` (${item.unit})` : ""}</option>)}</>;
  function startNewVersion(rule) {
    setRuleType(rule.rule_type);
    window.setTimeout(() => {
      const form = formRef.current;
      if (!form) return;
      form.elements.rule_code.value = rule.rule_code;
      form.elements.rule_name.value = rule.rule_name;
      form.elements.severity.value = rule.severity;
      const config = rule.config || {};
      if (form.elements.trait_code && config.trait_code) {
        form.elements.trait_code.value = config.trait_code;
        setSelectedTrait(config.trait_code);
      }
      if (form.elements.publish_field && config.field) form.elements.publish_field.value = config.field;
      form.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 0);
  }

  return <div className="page-stack"><section className="panel"><PanelTitle icon={ShieldCheck} title="规则版本与标准字典" note="已发布规则不能覆盖修改；新增同一规则编号将自动生成新版本，旧数据仍保留原规则版本。" />
    {!readOnly && <form className="rule-form" ref={formRef} onSubmit={createRule}>
      <input name="rule_code" required placeholder="规则编号，例如 R501" />
      <input name="rule_name" required placeholder="规则名称，例如 株高上限预警" />
      <select name="rule_type" value={ruleType} onChange={(event) => setRuleType(event.target.value)}><option value="quality">质量校验</option><option value="mapping">字段映射</option><option value="unit">单位换算</option><option value="semantic">业务映射</option><option value="publish">发布校验</option><option value="name">名称处理</option></select>
      <select name="severity" defaultValue="warning"><option value="block">阻断发布</option><option value="warning">预警</option><option value="info">提示</option></select>
      {ruleType === "quality" && <><select name="trait_code" value={selectedTrait} onChange={(event) => setSelectedTrait(event.target.value)}>{traitOptions}</select><input name="min_value" type="number" step="any" placeholder="最小值（可不填）" /><input name="max_value" type="number" step="any" placeholder={`最大值（${trait?.unit || "无单位"}，可不填）`} /></>}
      {ruleType === "mapping" && <><input name="source_field" required placeholder="原始字段名，例如 植高记录" /><select name="target_trait_code" defaultValue="plant_height">{traitOptions}</select></>}
      {ruleType === "unit" && <><select name="trait_code" value={selectedTrait} onChange={(event) => setSelectedTrait(event.target.value)}>{traitOptions}</select><input name="source_unit" required placeholder="原始单位，例如 mg" /><input name="target_unit" required value={trait?.unit || ""} readOnly /><select name="unit_operation" defaultValue="divide"><option value="direct">不换算</option><option value="multiply">乘以系数</option><option value="divide">除以系数</option></select><input name="unit_factor" type="number" step="any" defaultValue="1" placeholder="换算系数" /></>}
      {ruleType === "semantic" && <><select name="trait_code" value={selectedTrait} onChange={(event) => setSelectedTrait(event.target.value)}>{traitOptions}</select><input name="source_text" required placeholder="原始写法，例如 叶瘟3级" /><input name="standard_value" required placeholder="标准表达，例如 3级" /></>}
      {ruleType === "publish" && <select name="publish_field" defaultValue="variety_name"><option value="variety_name">品种名称不能为空</option><option value="source_review_id">必须保留数据来源</option><option value="original_value">必须保留原始值</option></select>}
      {ruleType === "name" && <input name="name_pattern" required placeholder="名称规则，例如 名称（别名）" />}
      <input name="change_reason" required placeholder="新增或变更原因" />
      <button className="primary-button" type="submit"><Plus size={16} />新增规则版本</button>
    </form>}
    <div className="table-scroll"><table><thead><tr><th>编号</th><th>规则名称</th><th>类型</th><th>版本</th><th>等级</th><th>状态</th><th>变更原因</th><th>操作</th></tr></thead><tbody>{rules.map((rule) => <tr key={rule.id}><td>{rule.rule_code}</td><td>{rule.rule_name}</td><td>{rule.rule_type}</td><td>{rule.version}</td><td><Status value={rule.severity} /></td><td><Status value={rule.status} /></td><td>{rule.change_reason}</td><td>{!readOnly && <>{rule.status === "published" && <button className="text-button danger" onClick={() => retireRule(rule.id)}>停用</button>}{rule.status === "retired" && <button className="text-button" onClick={() => startNewVersion(rule)}>新建版本</button>}</>}</td></tr>)}</tbody></table></div>
  </section></div>;
}

function ManualEntry({ createManualRecord, varieties, catalog }) {
  return <div className="page-stack manual-entry-page"><div className="manual-entry-layout"><section className="panel manual-entry-form-panel"><PanelTitle icon={FilePlus2} title="手工新增品种或表型" note="可以选择已有品种新增一条表型，也可以填写新名称创建品种并同时录入表型。来源说明是必填项。" /><form className="manual-form" onSubmit={createManualRecord}><label className="manual-form-wide">已有品种（可选）<select name="variety_id" defaultValue=""><option value="">新建品种，请填写下方名称</option>{varieties.map((item) => <option key={item.id} value={item.id}>{item.variety_name}{item.alias_names.length ? `（${item.alias_names.join("、")}）` : ""}</option>)}</select></label><label>新建标准品种名称（选择已有品种时可留空）<input name="variety_name" placeholder="例如：田两优9号" /></label><label>别名<input name="aliases" placeholder="例如：田两优佳99" /></label><label>品种类型<input name="variety_type" placeholder="例如：籼型两系杂交水稻" /></label><label>标准性状<select name="trait_code" required><option value="">请选择</option>{catalog.map((item) => <option key={item.code} value={item.code}>{item.name} {item.unit ? `(${item.unit})` : ""}</option>)}</select></label><label>原始表型值<input name="original_value" required placeholder="例如：株高103.3厘米" /></label><label className="manual-form-wide">来源说明<input name="source_reference" required placeholder="网页地址、PDF文件名、试验记录编号等" /></label><label className="manual-form-wide">补充说明<textarea name="source_note" placeholder="可填写原始段落、测量条件或人工录入说明" /></label><button className="primary-button" type="submit"><Plus size={16} />保存为待处理记录</button></form></section><aside className="panel manual-entry-aside"><ClipboardCheck size={23} /><div><span className="eyebrow">补录规则</span><h2>保留原始记录与来源</h2><dl><div><dt>品种档案</dt><dd>关联已有品种，或用标准名称建立待审核档案。</dd></div><div><dt>原始表型值</dt><dd>保留录入时的文本、单位和测量表达，避免丢失原始证据。</dd></div><div><dt>处理路径</dt><dd>保存后进入待处理记录，质量检查与发布流程保持不变。</dd></div></dl></div></aside></div><section className="panel muted-panel"><Sparkles size={20} /><div><strong>不会绕过数据治理</strong><p>手工录入同样保留来源、原始值、规则版本和操作人，并需通过质量检查后才能发布。</p></div></section></div>;
}

function Research({ filters, setFilters, searchResearch, rows, selected, toggleSelection, openDetail, downloadCsv, downloadXlsx, downloadPng, downloadPdf, chartRef, catalog }) {
  const [comparisonFields, setComparisonFields] = useState(DEFAULT_COMPARISON_FIELDS);
  const [scatterAxes, setScatterAxes] = useState({ x: "plant_height", y: "yield_per_mu" });
  const [distributionField, setDistributionField] = useState("thousand_grain_weight");
  const traitByCode = useMemo(() => Object.fromEntries(catalog.map((item) => [item.code, item])), [catalog]);
  const numericTraits = catalog.filter((item) => item.unit);
  const selectedRows = rows.filter((item) => selected.includes(item.id));
  const comparisonRows = selectedRows.length ? selectedRows : rows.slice(0, 5);
  const fieldLabel = (code) => `${traitByCode[code]?.name || code}${traitByCode[code]?.unit ? ` (${traitByCode[code].unit})` : ""}`;
  const scatter = comparisonRows.filter((item) => item.traits[scatterAxes.x] != null && item.traits[scatterAxes.y] != null).map((item) => ({ name: item.variety_name, x: item.traits[scatterAxes.x], y: item.traits[scatterAxes.y] }));
  const distribution = buildDistribution(comparisonRows.map((item) => item.traits[distributionField]).filter((item) => item != null), traitByCode[distributionField]?.unit, distributionField);
  const barData = comparisonRows.map((item) => ({ name: item.variety_name, ...Object.fromEntries(comparisonFields.map((code) => [code, item.traits[code]])) }));
  const toggleComparisonField = (code) => setComparisonFields((current) => current.includes(code) ? current.filter((item) => item !== code) : current.length >= 6 ? current : [...current, code]);
  return <div className="page-stack"><section className="panel"><PanelTitle icon={Search} title="已发布数据检索" note="只展示已发布标准数据；原始文件不会在科研查询页下载。" /><form className="filter-grid" onSubmit={(event) => { event.preventDefault(); searchResearch(); }}><input value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="品种名称或别名" /><input type="number" value={filters.height_max} onChange={(event) => setFilters({ ...filters, height_max: event.target.value })} placeholder="株高上限 cm" /><input type="number" value={filters.grain_weight_min} onChange={(event) => setFilters({ ...filters, grain_weight_min: event.target.value })} placeholder="千粒重下限 g" /><input type="number" value={filters.blast_max} onChange={(event) => setFilters({ ...filters, blast_max: event.target.value })} placeholder="叶瘟等级上限" /><button className="primary-button" type="submit"><Search size={16} />筛选</button></form><div className="download-bar"><span>命中 {rows.length} 个品种，可选择最多5个进行对比。</span><div><button className="secondary-button" onClick={downloadCsv}>CSV</button><button className="secondary-button" onClick={downloadXlsx}>Excel</button><button className="secondary-button" onClick={downloadPdf}>PDF报告</button></div></div>
    <div className="comparison-controls"><details className="field-picker"><summary>选择对比字段（{comparisonFields.length}/6）</summary><div className="field-picker-menu">{numericTraits.map((trait) => { const checked = comparisonFields.includes(trait.code); return <label key={trait.code}><input type="checkbox" checked={checked} disabled={!checked && comparisonFields.length >= 6} onChange={() => toggleComparisonField(trait.code)} />{trait.name}{trait.unit ? ` (${trait.unit})` : ""}</label>; })}</div></details><label>散点横轴<select value={scatterAxes.x} onChange={(event) => setScatterAxes({ ...scatterAxes, x: event.target.value })}>{numericTraits.map((trait) => <option key={trait.code} value={trait.code}>{fieldLabel(trait.code)}</option>)}</select></label><label>散点纵轴<select value={scatterAxes.y} onChange={(event) => setScatterAxes({ ...scatterAxes, y: event.target.value })}>{numericTraits.map((trait) => <option key={trait.code} value={trait.code}>{fieldLabel(trait.code)}</option>)}</select></label><label>分布字段<select value={distributionField} onChange={(event) => setDistributionField(event.target.value)}>{numericTraits.map((trait) => <option key={trait.code} value={trait.code}>{fieldLabel(trait.code)}</option>)}</select></label></div>
    <div className="table-scroll"><table><thead><tr><th>对比</th><th>品种</th><th>别名</th>{comparisonFields.map((code) => <th key={code}>{fieldLabel(code)}</th>)}<th>详情</th></tr></thead><tbody>{rows.length ? rows.map((item) => <tr key={item.id}><td><input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggleSelection(item.id)} /></td><td>{item.variety_name}</td><td>{item.alias_names.join("、") || "-"}</td>{comparisonFields.map((code) => <td key={code}>{item.traits[code] ?? "-"}</td>)}<td><button className="text-button" onClick={() => openDetail(item.id)}>查看</button></td></tr>) : <EmptyRow colSpan={comparisonFields.length + 4} text="没有符合条件的已发布品种。" />}</tbody></table></div>
  </section><section className="panel charts" ref={chartRef}><div className="panel-title-row"><PanelTitle icon={BarChart3} title="表型对比与基础图表" note="三个图均展示当前选中品种；未选中时展示前5个查询结果。" /><button className="secondary-button" onClick={downloadPng}><Download size={16} />下载PNG</button></div><div className="chart-grid"><ChartCard title="所选字段柱状对比"><ResponsiveContainer width="100%" height={310}><BarChart data={barData} margin={{ top: 12, right: 24, bottom: 12, left: 12 }}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" tick={{ fontSize: 11 }} /><YAxis width={58} /><Tooltip />{comparisonFields.map((code, index) => <Bar key={code} dataKey={code} name={fieldLabel(code)} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}</BarChart></ResponsiveContainer></ChartCard><ChartCard title={`${fieldLabel(scatterAxes.x)} 与 ${fieldLabel(scatterAxes.y)} 散点关系`}><ResponsiveContainer width="100%" height={310}><ScatterChart margin={{ top: 12, right: 30, bottom: 12, left: 36 }}><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" dataKey="x" name={traitByCode[scatterAxes.x]?.name || scatterAxes.x} unit={traitByCode[scatterAxes.x]?.unit || ""} /><YAxis type="number" dataKey="y" name={traitByCode[scatterAxes.y]?.name || scatterAxes.y} unit={traitByCode[scatterAxes.y]?.unit || ""} width={84} /><Tooltip cursor={{ strokeDasharray: "3 3" }} /><Scatter data={scatter} fill="#5d7fbc" /></ScatterChart></ResponsiveContainer></ChartCard><ChartCard title={`${fieldLabel(distributionField)} 分布`}><ResponsiveContainer width="100%" height={310}><BarChart data={distribution} margin={{ top: 12, right: 24, bottom: 12, left: 12 }}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="bucket" /><YAxis allowDecimals={false} width={58} /><Tooltip /><Bar dataKey="count" name="品种数">{distribution.map((item, index) => <Cell key={item.bucket} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}</Bar></BarChart></ResponsiveContainer></ChartCard></div></section></div>;
}

function Overview({ dashboard, rules }) {
  return <div className="page-stack"><section className="metric-grid"><Metric label="规则总数" value={rules.length} note="版本化维护，不覆盖历史" icon={SlidersHorizontal} /><Metric label="标准品种" value={dashboard.varieties ?? "-"} note="保留基础信息与别名" icon={BookOpen} /><Metric label="待处理" value={dashboard.pending ?? "-"} note="需数据处理员确认" icon={Gauge} tone="warn" /><Metric label="已发布表型" value={dashboard.published ?? "-"} note="科研助手可查询" icon={CheckCircle2} tone="good" /></section><section className="panel"><PanelTitle icon={LayoutDashboard} title="当前管理范围" note="字段管理员发布的版本会成为后续导入任务可选的高标准数据集模板。" /><div className="boundary-grid"><div><strong>字段管理员可维护</strong><ul><li>标准模板中的字段、单位、别名与取值范围</li><li>字段映射、单位换算、质量和发布规则</li><li>未识别字段申请及对应模板的新版本说明</li></ul></div><div><strong>角色边界</strong><ul><li>数据处理员负责导入、核验、审核和发布</li><li>字段管理员不直接修改业务原始数据</li><li>科研人员只访问已发布标准数据及自己的会话材料</li></ul></div></div></section></div>;
}

function DetailDrawer({ detail, onClose }) {
  return <div className="drawer-backdrop" onMouseDown={onClose}><aside className="detail-drawer" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="eyebrow">品种详情与来源追溯</span><h2>{detail.variety_name}</h2><p>{detail.alias_names.join("、") || "暂无别名"}</p></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header><section><h3>基础信息</h3><dl><dt>品种类型</dt><dd>{detail.variety_type || "-"}</dd><dt>选育单位</dt><dd>{detail.breeding_unit || "-"}</dd><dt>审定编号</dt><dd>{detail.approval_number || "-"}</dd><dt>适宜区域</dt><dd>{detail.suitable_region || "-"}</dd></dl></section><section><h3>表型记录</h3>{detail.observations.map((item) => <div className="detail-observation" key={item.id}><div><strong>{item.trait_name}</strong><span>{item.value_numeric ?? item.value_text} {item.unit}</span></div><small>原始值：{item.original_value} · {item.source_locator}</small></div>)}</section><section><h3>来源摘要</h3>{detail.sources.map((source) => <div className="source-card" key={source.id}><strong>{source.source_name}</strong><p>{source.source_url || source.page_or_locator}</p><small>{source.raw_text.slice(0, 220)}…</small></div>)}</section></aside></div>;
}

function EditModal({ observation, setObservation, onClose, onSave }) {
  const numeric = observation.observation_type === "numeric";
  return <div className="modal-backdrop"><section className="modal"><header><div><span className="eyebrow">逐条审核</span><h2>{observation.variety_name} · {observation.trait_name}</h2></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header><div className="modal-grid"><label>原始值<input value={observation.original_value || ""} onChange={(event) => setObservation({ ...observation, original_value: event.target.value })} /></label>{numeric ? <label>标准数值<input type="number" value={observation.value_numeric ?? ""} onChange={(event) => setObservation({ ...observation, value_numeric: event.target.value })} /></label> : <label>标准文本<input value={observation.value_text || ""} onChange={(event) => setObservation({ ...observation, value_text: event.target.value })} /></label>}<label>单位<input value={observation.unit || ""} onChange={(event) => setObservation({ ...observation, unit: event.target.value })} /></label><label className="full">修改原因<textarea required value={observation.review_comment || ""} onChange={(event) => setObservation({ ...observation, review_comment: event.target.value })} placeholder="例如：核对原始网页后修正解析遗漏数字" /></label></div><div className="modal-note"><Info size={16} />保存后会保留修改前后值、操作人、时间和原因；记录将回到待发布状态。</div><footer><button className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" onClick={onSave}>保存审核结果</button></footer></section></div>;
}

function PanelTitle({ icon: Icon, title, note }) { return <div className="panel-title"><div><Icon size={19} /><h2>{title}</h2></div><p>{note}</p></div>; }
function Metric({ label, value, note, icon: Icon, tone = "" }) { return <article className={`metric ${tone}`}><div><span>{label}</span><strong>{value}</strong><p>{note}</p></div><Icon size={24} /></article>; }
function Status({ value, label }) { return <span className={`status ${String(value).replaceAll("_", "-")}`}>{label || statusLabel(value)}</span>; }
function statusLabel(value) { return ({ passed: "通过", published: "已发布", pending: "待处理", blocked: "阻断", warning: "预警", info: "提示", block: "阻断", retired: "已停用" })[value] || value; }
function EmptyRow({ colSpan, text }) { return <tr><td colSpan={colSpan} className="empty-cell">{text}</td></tr>; }
function ChartCard({ title, children }) { return <article className="chart-card"><h3>{title}</h3>{children}</article>; }
function buildDistribution(values, unit = "", traitCode = "") {
  if (!values.length) return [];
  if (traitCode === "thousand_grain_weight") {
    const buckets = [{ bucket: `<20${unit}`, count: 0 }, { bucket: `20–25${unit}`, count: 0 }, { bucket: `25–30${unit}`, count: 0 }, { bucket: `≥30${unit}`, count: 0 }];
    values.forEach((value) => { if (value < 20) buckets[0].count += 1; else if (value < 25) buckets[1].count += 1; else if (value < 30) buckets[2].count += 1; else buckets[3].count += 1; });
    return buckets;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [{ bucket: `${min}${unit}`, count: values.length }];
  const step = (max - min) / 4;
  const buckets = Array.from({ length: 4 }, (_, index) => {
    const lower = min + index * step;
    const upper = index === 3 ? max : min + (index + 1) * step;
    return { bucket: `${lower.toFixed(1)}–${upper.toFixed(1)}${unit}`, lower, upper, count: 0 };
  });
  values.forEach((value) => {
    const index = Math.min(3, Math.floor((value - min) / step));
    buckets[index].count += 1;
  });
  return buckets;
}
function downloadBlob(blob, name) { const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url); }

export default App;
