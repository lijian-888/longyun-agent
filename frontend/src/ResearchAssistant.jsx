import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDown,
  BarChart3,
  Bot,
  Building2,
  ChevronDown,
  FileText,
  FileDown,
  LoaderCircle,
  LogOut,
  MessageSquarePlus,
  Paperclip,
  Pencil,
  Plus,
  Search,
  SendHorizontal,
  ShieldCheck,
  Sparkles,
  Sprout,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import { keycloak } from "./auth";
import { authorizedFetch, request } from "./api";
import KnowledgeLibrary from "./KnowledgeLibrary";
import ResultsLibrary from "./ResultsLibrary";
import GwasWorkspace from "./GwasWorkspace";
import SkillLibrary from "./SkillLibrary";
import { SinglePlantResearchWorkspace } from "./SinglePlantWorkspace";
import { BaseShowcaseWorkspace, VarietyEvaluationWorkspace } from "./BreedingDecisionWorkspaces";

const AGENT_NAME = "隆耘 Agent 育种智能体";

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function localMessage(role, content) {
  return {
    id: `local-${crypto.randomUUID()}`,
    role,
    content,
    evidence: [],
    operation_state: [],
    created_at: new Date().toISOString(),
  };
}

function isVisionAttachment(item) {
  const fileName = item?.file_name || item?.name || "";
  return item?.parsing_status === "image_ready" || /\.(png|jpe?g|webp)$/i.test(fileName);
}

function parseSseBlock(block) {
  const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim() || "message";
  const data = block.match(/^data:\s*(.+)$/m)?.[1];
  if (!data) return null;
  try {
    return { event, data: JSON.parse(data) };
  } catch {
    return null;
  }
}

function stripGeneratedReportInstructions(content) {
  // Report download is a controlled platform action. Some model providers
  // append a second, malformed "PDF report instructions" table after the
  // research conclusion; keeping that table causes its traceability fields
  // to fall into the first column. The real download affordance is rendered
  // below from `report_available`, so this presentation-only tail is removed.
  return content.replace(
    /(?:\n|^)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+[、.．]\s*)?关于\s*PDF\s*报告(?:文档|下载|生成|说明)?[\s\S]*$/i,
    "",
  ).trim();
}

function markdownForDisplay(content, streaming, suppressReportInstructions = false) {
  const originalContent = String(content || "");
  if (/<\/?think\b|<\/?tool\b|<\/?query\b|<\/?domains\b|<\/?function(?:_call)?\b|<tool_call\b|<analysis\b/i.test(originalContent)) {
    return "本轮回答因模型工具协议异常已被系统拦截，未作为有效科研结论保留。请重新提问。";
  }
  // Earlier MinerU imports contained Markdown-escaped `\\~` range markers.
  // Render them as a readable Chinese range marker in historical answers too.
  const normalizedContent = (suppressReportInstructions
    ? stripGeneratedReportInstructions(originalContent)
    : originalContent
  ).replace(/\\~/g, "～");
  if (!streaming) return normalizedContent;
  // During streaming, hide an unfinished bold marker until the closing marker
  // arrives. The persisted answer is always rendered from the original text.
  const boldMarkerCount = (normalizedContent.match(/\*\*/g) || []).length;
  return boldMarkerCount % 2 ? normalizedContent.replace(/\*\*([^*]*)$/, "$1") : normalizedContent;
}

function AssistantMarkdown({ content, streaming, suppressReportInstructions = false }) {
  const displayContent = markdownForDisplay(content, streaming, suppressReportInstructions);
  return <div className="assistant-markdown"><ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h3>{children}</h3>,
        h2: ({ children }) => <h3>{children}</h3>,
        h3: ({ children }) => <h4>{children}</h4>,
        // Preserve section spacing without showing model-generated `---` rules.
        hr: () => <div className="assistant-section-gap" aria-hidden="true" />,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
      }}
    >{displayContent}</ReactMarkdown></div>;
}

function ReportDownloadCard({ message, onDownload }) {
  const operation = (message.operation_state || []).find((item) => item?.state === "report_ready");
  const breedingReport = operation?.report_kind === "breeding_dossier";
  const title = breedingReport ? "品种选育报告（审定辅助草稿）" : "本轮科研分析报告";
  const description = breedingReport
    ? "报告仅汇总已发布试验表现和已标注的演示育种档案；正式申报前仍需以农科院原始材料复核。"
    : "报告仅使用本轮已确认的结构化数据、图表和证据来源生成，不采用模型自行推测的数值。";
  return <section className="assistant-report-card" aria-label="本轮报告下载">
    <div>
      <span>研究产物已准备</span>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
    <button className="report-download-button" type="button" onClick={onDownload}>
      <FileDown size={15} />下载 PDF 报告
    </button>
  </section>;
}


const QUERY_OPERATORS = [
  { value: "contains", label: "包含", kinds: ["text", "json"] },
  { value: "eq", label: "等于", kinds: ["text", "number", "integer", "boolean", "date", "datetime"] },
  { value: "ne", label: "不等于", kinds: ["text", "number", "integer", "boolean", "date", "datetime"] },
  { value: "gte", label: "不小于", kinds: ["number", "integer", "date", "datetime"] },
  { value: "gt", label: "大于", kinds: ["number", "integer", "date", "datetime"] },
  { value: "lte", label: "不超过", kinds: ["number", "integer", "date", "datetime"] },
  { value: "lt", label: "小于", kinds: ["number", "integer", "date", "datetime"] },
];

const TRIAL_ANALYSIS_PROMPTS = [
  "2025 年南昌点、标准施氮处理下，哪些材料产量更高且株高更低？",
  "候选材料 A 与对照品种在 3 年 4 点的平均产量、相对增产、波动和有效环境数如何？",
  "土壤 pH、有效磷、降雨量与结实率、千粒重、产量有什么关联？",
  "标准施氮和较高施氮下，哪些材料增产明显，哪些材料倒伏风险上升？",
  "高产材料是否同时具备较好米质？高产和抗倒伏之间是否存在取舍？",
  "某材料 2025 年产量下降，是因为土壤、天气、施氮或病害压力变化，还是数据本身异常？",
];

function fieldLabel(field) {
  return field?.unit ? `${field.name} (${field.unit})` : field?.name || "";
}

function StructuredQueryPanel({ onNotice, projectId }) {
  const [catalog, setCatalog] = useState({ datasets: [], project: null });
  const [datasetCode, setDatasetCode] = useState("");
  const [selectedFieldCodes, setSelectedFieldCodes] = useState([]);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState([]);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const datasets = catalog.datasets || [];
  const dataset = datasets.find((item) => item.code === datasetCode);
  const fields = dataset?.fields || [];
  const selectedFields = fields.filter((item) => selectedFieldCodes.includes(item.code));
  const allSelected = fields.length > 0 && selectedFieldCodes.length === fields.length;

  useEffect(() => {
    if (!projectId) {
      setCatalog({ datasets: [], project: null });
      setDatasetCode("");
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const response = await request(`/api/research/project-data/catalog?project_id=${encodeURIComponent(projectId)}`);
        if (cancelled) return;
        setCatalog(response);
        setDatasetCode(response.datasets?.[0]?.code || "");
      } catch (requestError) {
        if (!cancelled) setError(requestError.message || "无法读取当前课题的数据目录。");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId]);

  useEffect(() => {
    setSelectedFieldCodes(fields.filter((item) => item.default).map((item) => item.code));
    setSearch("");
    setFilters([]);
    setResult(null);
  }, [datasetCode]);

  function switchDataset(nextCode) {
    if (nextCode === datasetCode) return;
    setDatasetCode(nextCode);
    setError("");
  }

  function toggleField(code) {
    setSelectedFieldCodes((current) => current.includes(code)
      ? current.filter((item) => item !== code)
      : [...current, code]);
  }

  function toggleAllFields() {
    setSelectedFieldCodes(allSelected ? [] : fields.map((item) => item.code));
  }

  function addFilter() {
    const firstField = fields.find((item) => item.filterable);
    if (!firstField || filters.length >= 8) return;
    const operator = ["number", "integer", "date", "datetime"].includes(firstField.kind) ? "gte" : "contains";
    setFilters((current) => [...current, { field: firstField.code, operator, value: "" }]);
  }

  function updateFilter(index, patch) {
    setFilters((current) => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      if (!patch.field) return { ...item, ...patch };
      const nextField = fields.find((field) => field.code === patch.field);
      const operator = ["number", "integer", "date", "datetime"].includes(nextField?.kind) ? "gte" : "contains";
      return { ...item, ...patch, operator, value: "" };
    }));
  }

  function removeFilter(index) {
    setFilters((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  function exportCsv() {
    if (!result?.records?.length) return;
    const resultFields = result.fields || [];
    const columns = resultFields.map(fieldLabel);
    const escapeCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const rows = result.records.map((record) => resultFields.map((field) => {
      const value = record[field.code];
      return value && typeof value === "object" ? JSON.stringify(value) : value;
    }));
    const csv = `\uFEFF${[columns, ...rows].map((row) => row.map(escapeCell).join(",")).join("\r\n")}`;
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${dataset?.title || "课题数据"}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function runQuery(event) {
    event.preventDefault();
    setError("");
    if (!projectId || !datasetCode) {
      setError("请先选择一个可访问的课题和数据类型。");
      return;
    }
    if (!selectedFieldCodes.length) {
      setError("请至少选择一个需要展示的字段。");
      return;
    }
    if (filters.some((item) => item.value === "")) {
      setError("每个筛选条件都需要填写值。");
      return;
    }
    setRunning(true);
    try {
      const response = await request("/api/research/project-data/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          dataset: datasetCode,
          fields: selectedFieldCodes,
          search: search.trim(),
          filters,
          limit: 100,
          offset: 0,
        }),
      });
      setResult(response);
      onNotice(`已从“${catalog.project?.project_name || "当前课题"}”查询到 ${response.record_count} 条${response.dataset_title}记录。`);
    } catch (requestError) {
      setError(requestError.message || "查询未完成，请稍后重试。");
    } finally {
      setRunning(false);
    }
  }

  if (loading) return <section className="structured-query-loading"><LoaderCircle size={18} className="spin" />正在读取当前课题的数据目录</section>;

  return <section className="structured-query-panel">
    <div className="structured-query-heading">
      <div><p>当前课题 · {catalog.project?.project_name || "尚未选择"}</p><h2>课题数据查询</h2><span>查询当前账号有权访问的课题业务数据；系统使用受控字段，不需要填写 SQL。</span></div>
      <span className="structured-query-limit">单次最多返回 100 条记录</span>
    </div>

    <div className="dataset-switch" role="tablist" aria-label="选择课题数据类型">
      {datasets.map((item) => <button key={item.code} type="button" role="tab" aria-selected={datasetCode === item.code} className={datasetCode === item.code ? "active" : ""} onClick={() => switchDataset(item.code)}>{item.title}</button>)}
    </div>

    {dataset && <form className="structured-query-form" onSubmit={runQuery}>
      <div className="structured-query-description">{dataset.description}</div>
      <label className="structured-name-input"><span>关键词（可选）</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入材料、试验、地点、性状、处理或数据集名称" /><small>关键词只在当前数据类型标记为可搜索的字段中进行模糊匹配。</small></label>

      <section className="structured-field-section">
        <div className="structured-section-title"><div><strong>展示字段</strong><span>勾选结果中需要展示的课题业务字段</span></div><button className="text-button" type="button" onClick={toggleAllFields}>{allSelected ? "取消全选" : "全选全部字段"}</button></div>
        <div className="structured-field-grid">
          {fields.map((field) => <label className="structured-field-option" key={field.code}><input type="checkbox" checked={selectedFieldCodes.includes(field.code)} onChange={() => toggleField(field.code)} /><span>{fieldLabel(field)}</span></label>)}
        </div>
      </section>

      <section className="structured-filter-section">
        <div className="structured-section-title"><div><strong>筛选条件</strong><span>可选，最多 8 条；支持文本、数值和状态字段的受控筛选。</span></div><button className="secondary-button" type="button" onClick={addFilter} disabled={filters.length >= 8 || !fields.some((item) => item.filterable)}><Plus size={15} />新增条件</button></div>
        {!filters.length && <div className="structured-filter-empty">未添加条件时，将按关键词或当前课题的全部数据范围查询。</div>}
        {filters.map((filter, index) => { const filterField = fields.find((field) => field.code === filter.field); const operators = QUERY_OPERATORS.filter((operator) => !operator.kinds || operator.kinds.includes(filterField?.kind)); return <div className="structured-filter-row" key={`${filter.field}-${index}`}>
          <select value={filter.field} onChange={(event) => updateFilter(index, { field: event.target.value })}>{fields.filter((field) => field.filterable).map((field) => <option value={field.code} key={field.code}>{fieldLabel(field)}</option>)}</select>
          <select value={filter.operator} onChange={(event) => updateFilter(index, { operator: event.target.value })}>{operators.map((operator) => <option value={operator.value} key={operator.value}>{operator.label}</option>)}</select>
          {filterField?.kind === "boolean" ? <select value={filter.value} onChange={(event) => updateFilter(index, { value: event.target.value })}><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select> : <input type={["number", "integer"].includes(filterField?.kind) ? "number" : "text"} step={filterField?.kind === "integer" ? "1" : "any"} value={filter.value} onChange={(event) => updateFilter(index, { value: event.target.value })} placeholder="筛选值" />}
          <button className="icon-button" type="button" title="移除此筛选条件" onClick={() => removeFilter(index)}><X size={16} /></button>
        </div>; })}
      </section>

      {error && <div className="structured-query-error">{error}</div>}
      <div className="structured-query-actions"><button className="primary-button" type="submit" disabled={running}><Search size={17} />{running ? "正在查询" : "查询当前课题数据"}</button></div>
    </form>}

    {result && <section className="structured-query-result">
      <div className="structured-result-heading"><div><h3>{result.dataset_title}查询结果</h3><span>本次返回 {result.record_count} 条记录{result.has_more ? "，仍有更多数据，可继续细化条件" : ""}。</span></div><div className="structured-result-actions"><span>数据边界：当前课题</span><button className="secondary-button" type="button" onClick={exportCsv} disabled={!result.records?.length}><FileText size={15} />导出 CSV</button></div></div>
      <div className="table-scroll structured-result-table"><table><thead><tr>{(result.fields || []).map((field) => <th key={field.code}>{fieldLabel(field)}</th>)}</tr></thead><tbody>{result.records?.length ? result.records.map((record) => <tr key={record.id}>{(result.fields || []).map((field) => { const value = record[field.code]; return <td key={field.code}>{value == null || value === "" ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value)}</td>; })}</tr>) : <tr><td className="empty-cell" colSpan={Math.max(result.fields?.length || 0, 1)}>当前课题没有符合条件的数据。</td></tr>}</tbody></table></div>
    </section>}
  </section>;
}

export default function ResearchAssistant() {
  const [user, setUser] = useState(null);
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [messages, setMessages] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [composerAttachmentIds, setComposerAttachmentIds] = useState([]);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [progress, setProgress] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [imagePreviewUrls, setImagePreviewUrls] = useState({});
  const [pendingImages, setPendingImages] = useState([]);
  const [isDragActive, setIsDragActive] = useState(false);
  const [workspace, setWorkspace] = useState("assistant");
  const [accountOpen, setAccountOpen] = useState(false);
  const [showLatestButton, setShowLatestButton] = useState(false);
  const fileInputRef = useRef(null);
  const composerTextareaRef = useRef(null);
  const chatLogRef = useRef(null);
  const followLatestRef = useRef(true);
  const imagePreviewUrlsRef = useRef({});

  const activeSession = sessions.find((item) => item.id === activeSessionId);
  const attachmentById = new Map(attachments.map((item) => [item.id, item]));
  const composerAttachments = attachments.filter((item) => composerAttachmentIds.includes(item.id));

  const isGenerating = sending || messages.some((item) => item.streaming);

  function isNearChatBottom(container) {
    return container.scrollHeight - container.scrollTop - container.clientHeight < 72;
  }

  function scrollChatToLatest() {
    const container = chatLogRef.current;
    if (!container) return;
    followLatestRef.current = true;
    setShowLatestButton(false);
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }

  function handleChatScroll(event) {
    const isNearBottom = isNearChatBottom(event.currentTarget);
    followLatestRef.current = isNearBottom;
    setShowLatestButton(!isNearBottom);
  }

  useEffect(() => {
    const container = chatLogRef.current;
    if (!container) return;
    if (followLatestRef.current) {
      container.scrollTop = container.scrollHeight;
      setShowLatestButton(false);
    } else if (isGenerating) {
      setShowLatestButton(true);
    }
  }, [messages, progress, isGenerating]);

  useEffect(() => {
    let cancelled = false;
    const imageAttachments = attachments.filter(isVisionAttachment);
    const activeIds = new Set(imageAttachments.map((item) => item.id));
    const missing = imageAttachments.filter((item) => !imagePreviewUrlsRef.current[item.id]);

    async function loadImagePreviews() {
      const loaded = await Promise.all(missing.map(async (item) => {
        try {
          const response = await authorizedFetch(`/api/research/attachments/${item.id}/image?project_id=${encodeURIComponent(selectedProjectId)}`);
          if (!response.ok) throw new Error("图片读取失败");
          return [item.id, URL.createObjectURL(await response.blob())];
        } catch {
          return null;
        }
      }));
      if (cancelled) {
        loaded.forEach((entry) => entry && URL.revokeObjectURL(entry[1]));
        return;
      }
      setImagePreviewUrls((current) => {
        const next = {};
        Object.entries(current).forEach(([id, url]) => {
          if (activeIds.has(id)) next[id] = url;
          else URL.revokeObjectURL(url);
        });
        loaded.forEach((entry) => {
          if (entry) next[entry[0]] = entry[1];
        });
        imagePreviewUrlsRef.current = next;
        return next;
      });
    }

    void loadImagePreviews();
    return () => { cancelled = true; };
  }, [attachments, selectedProjectId]);

  useEffect(() => () => {
    Object.values(imagePreviewUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
  }, []);

  async function loadConversation(sessionId, projectId = selectedProjectId) {
    if (!sessionId || !projectId) return;
    const [messageList, attachmentList] = await Promise.all([
      request(`/api/research/sessions/${sessionId}/messages?project_id=${encodeURIComponent(projectId)}`),
      request(`/api/research/sessions/${sessionId}/attachments?project_id=${encodeURIComponent(projectId)}`),
    ]);
    followLatestRef.current = true;
    setShowLatestButton(false);
    setMessages(messageList);
    setAttachments(attachmentList);
    const sentAttachmentIds = new Set(messageList.flatMap((message) => (message.evidence || [])
      .filter((item) => item.type === "message_attachment" && item.attachment_id)
      .map((item) => item.attachment_id)));
    // Legacy files created before message-bound attachments stay visible once,
    // so the researcher can explicitly send them with the next question.
    setComposerAttachmentIds(attachmentList
      .filter((item) => !sentAttachmentIds.has(item.id))
      .map((item) => item.id));
    setActiveSessionId(sessionId);
  }

  async function loadSessions(preferredId = "", { reloadConversation = true } = {}, projectId = selectedProjectId) {
    if (!projectId) {
      setSessions([]);
      return [];
    }
    const sessionList = await request(`/api/research/sessions?project_id=${encodeURIComponent(projectId)}`);
    setSessions(sessionList);
    const targetId = preferredId || (sessionList.some((item) => item.id === activeSessionId) ? activeSessionId : sessionList[0]?.id);
    if (targetId && reloadConversation) await loadConversation(targetId, projectId);
    return sessionList;
  }

  async function createSession(title = "新会话", projectId = selectedProjectId) {
    if (!projectId) throw new Error("请先选择课题，再新建会话。");
    const session = await request("/api/research/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, project_id: projectId }),
    });
    await loadSessions(session.id, {}, projectId);
    return session;
  }

  useEffect(() => {
    (async () => {
      try {
        const [currentUser, projectList] = await Promise.all([
          request("/api/research/me"),
          request("/api/data-spine/projects"),
        ]);
        setUser(currentUser);
        setProjects(projectList);
        const remembered = window.localStorage.getItem("longyun-research-project");
        const projectId = projectList.some((item) => item.id === remembered) ? remembered : projectList[0]?.id || "";
        setSelectedProjectId(projectId);
        if (projectId) {
          const currentSessions = await loadSessions("", {}, projectId);
          if (!currentSessions.length) await createSession("新会话", projectId);
        }
      } catch (error) {
        setNotice(error.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function changeProject(projectId) {
    setSelectedProjectId(projectId);
    window.localStorage.setItem("longyun-research-project", projectId);
    setActiveSessionId("");
    setMessages([]);
    setAttachments([]);
    setComposerAttachmentIds([]);
    try {
      const projectSessions = await loadSessions("", {}, projectId);
      if (!projectSessions.length) await createSession("新会话", projectId);
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function renameSession(targetSession) {
    if (!targetSession) return;
    const title = window.prompt("会话名称", targetSession.title);
    if (!title?.trim()) return;
    try {
      await request(`/api/research/sessions/${targetSession.id}?project_id=${encodeURIComponent(selectedProjectId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim() }),
      });
      await loadSessions(activeSessionId || targetSession.id);
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function deleteSession(targetSession) {
    if (!targetSession || !window.confirm(`删除“${targetSession.title}”及其私有附件、解析文本和上下文？此操作不可恢复。`)) return;
    try {
      await request(`/api/research/sessions/${targetSession.id}?project_id=${encodeURIComponent(selectedProjectId)}`, { method: "DELETE" });
      const remaining = await request(`/api/research/sessions?project_id=${encodeURIComponent(selectedProjectId)}`);
      setSessions(remaining);
      if (targetSession.id === activeSessionId) {
        if (remaining[0]) await loadConversation(remaining[0].id);
        else await createSession("新会话", selectedProjectId);
      }
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function uploadFiles(files) {
    if (!files.length || !activeSessionId) return;
    const pending = files.filter(isVisionAttachment).map((file) => ({
      id: `pending-${crypto.randomUUID()}`,
      file_name: file.name,
      image_url: URL.createObjectURL(file),
    }));
    if (pending.length) setPendingImages((items) => [...items, ...pending]);
    setUploading(true);
    setNotice("");
    try {
      const uploadedIds = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const created = await request(`/api/research/sessions/${activeSessionId}/attachments?project_id=${encodeURIComponent(selectedProjectId)}`, { method: "POST", body: form });
        uploadedIds.push(created.id);
      }
      const attachmentList = await request(`/api/research/sessions/${activeSessionId}/attachments?project_id=${encodeURIComponent(selectedProjectId)}`);
      setAttachments(attachmentList);
      setComposerAttachmentIds((current) => [...new Set([...current, ...uploadedIds])]);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setUploading(false);
      setPendingImages((items) => items.filter((item) => !pending.some((pendingItem) => pendingItem.id === item.id)));
      pending.forEach((item) => URL.revokeObjectURL(item.image_url));
    }
  }

  async function uploadFileInput(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    await uploadFiles(files);
  }

  async function pasteImages(event) {
    const items = Array.from(event.clipboardData?.items || []);
    const clipboardFiles = Array.from(event.clipboardData?.files || [])
      .filter((file) => file.type.startsWith("image/"));
    const files = (clipboardFiles.length ? clipboardFiles : items
      .filter((item) => item.type.startsWith("image/"))
      .map((item, index) => {
        const blob = item.getAsFile();
        if (!blob) return null;
        const extension = blob.type === "image/png" ? "png" : blob.type === "image/webp" ? "webp" : "jpg";
        return new File([blob], `粘贴图片-${Date.now()}-${index + 1}.${extension}`, { type: blob.type });
      })
      .filter(Boolean));
    if (!files.length) return;
    event.preventDefault();
    setNotice(`已从剪贴板读取 ${files.length} 张图片，正在本地保存；提问时将原图直接交给当前配置的大模型进行视觉分析。`);
    await uploadFiles(files);
  }

  async function removeAttachment(attachmentId) {
    try {
      await request(`/api/research/attachments/${attachmentId}?project_id=${encodeURIComponent(selectedProjectId)}`, { method: "DELETE" });
      setComposerAttachmentIds((items) => items.filter((item) => item !== attachmentId));
      await loadConversation(activeSessionId);
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function showPreview(attachment) {
    if (isVisionAttachment(attachment)) {
      const imageUrl = imagePreviewUrls[attachment.id];
      if (!imageUrl) {
        setNotice("图片预览正在加载，请稍候再点击查看。");
        return;
      }
      setPreview({
        file_name: attachment.file_name,
        parsing_status: "image_ready",
        image_url: imageUrl,
        parser_warnings: attachment.parser_warnings || [],
      });
      return;
    }
    try {
      setPreview(await request(`/api/research/attachments/${attachment.id}/preview?project_id=${encodeURIComponent(selectedProjectId)}`));
    } catch (error) {
      setNotice(error.message);
    }
  }

  function dropFiles(event) {
    event.preventDefault();
    setIsDragActive(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length) void uploadFiles(files);
  }

  function applyTrialAnalysisPrompt(prompt) {
    setDraft(prompt);
    window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }

  function openResearchSkill(skill) {
    setWorkspace(skill.workspace);
    if (skill.suggested_question) {
      setDraft(skill.suggested_question);
      window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
    }
    setNotice(`${skill.name}已打开。${skill.suggested_question ? "已填入一条可直接体验的示例问题，可修改后发送。" : "请按受控流程准备输入并确认分析计划。"}`);
  }

  async function downloadResearchReport(messageId, automatic = false) {
    try {
      const response = await authorizedFetch(`/api/research/messages/${messageId}/report.pdf?project_id=${encodeURIComponent(selectedProjectId)}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "无法生成 PDF 报告。");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `农业科研分析报告-${new Date().toISOString().slice(0, 10)}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (!automatic) setNotice("PDF 报告已开始下载，并已保存到当前账号的结果库。");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "PDF 报告生成失败。";
      setNotice(automatic ? `回答已完成，但 PDF 未能自动下载：${detail}。可点击回答下方“下载 PDF 报告”重试。` : detail);
    }
  }

  async function sendQuestion(event) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || sending || !activeSessionId) return;
    setSending(true);
    setProgress("正在提交问题");
    setNotice("");
    setDraft("");
    followLatestRef.current = true;
    setShowLatestButton(false);
    const currentTurnAttachmentIds = composerAttachmentIds.filter((id) => attachmentById.has(id));
    const currentTurnAttachments = currentTurnAttachmentIds.map((id) => attachmentById.get(id));
    const userEntry = {
      ...localMessage("user", content),
      evidence: currentTurnAttachments.map((item) => ({
        type: "message_attachment",
        attachment_id: item.id,
        title: item.file_name,
        is_image: isVisionAttachment(item),
        size_bytes: item.size_bytes,
      })),
    };
    const assistantEntry = { ...localMessage("assistant", ""), streaming: true };
    setComposerAttachmentIds([]);
    setMessages((items) => [...items, userEntry, assistantEntry]);

    try {
      const response = await authorizedFetch(`/api/research/sessions/${activeSessionId}/chat/stream?project_id=${encodeURIComponent(selectedProjectId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          knowledge_scope: "both",
          attachment_ids: currentTurnAttachmentIds,
          external_data_acknowledged: false,
        }),
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "无法开始模型分析。");
      }
      // The backend has accepted and stored the first question at this point,
      // including its automatically summarized session title. Refresh only
      // sidebar metadata now, so the name changes even if model generation
      // later fails or takes a long time.
      await loadSessions(activeSessionId, { reloadConversation: false });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed = false;
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const rawEvent of events) {
          const parsed = parseSseBlock(rawEvent);
          if (!parsed) continue;
          if (parsed.event === "status") {
            setProgress(parsed.data.label || "正在处理");
          } else if (parsed.event === "token") {
            setMessages((items) => items.map((item) => item.id === assistantEntry.id
              ? { ...item, content: item.content + parsed.data.text }
              : item));
          } else if (parsed.event === "complete") {
            completed = true;
            setMessages((items) => items.map((item) => item.id === assistantEntry.id ? parsed.data.message : item));
            if (parsed.data.message?.report_available) {
              // The user already explicitly asked for a report in this turn.
              // Start its transient download without another confirmation; the
              // visible button remains available if a browser blocks it.
              void downloadResearchReport(parsed.data.message.id, true);
            }
          } else if (parsed.event === "error") {
            throw new Error(parsed.data.detail || "模型分析未完成。");
          }
        }
        if (done) break;
      }
      if (!completed) throw new Error("模型连接已结束，但没有返回完整答案。");
    } catch (error) {
      const detail = error instanceof Error && error.message
        ? error.message
        : "模型分析未完成，未获得可用的错误说明。";
      setMessages((items) => items.map((item) => item.id === assistantEntry.id
        ? { ...item, streaming: false, content: `分析未完成：${detail}`, error: true }
        : item));
      setComposerAttachmentIds((items) => [...new Set([...currentTurnAttachmentIds, ...items])]);
      setNotice(detail);
    } finally {
      setSending(false);
      setProgress("");
    }
  }

  function sendOnEnter(event) {
    if (sending) return;
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void sendQuestion();
  }

  if (loading) return <main className="assistant-loading"><LoaderCircle size={24} className="spin" />正在验证科研账号…</main>;

  return <div className="research-shell">
    <aside className="research-sidebar">
      <div className="research-brand"><div className="research-brand-mark brand-logo-mark" aria-hidden="true"><img src="/brand/longyun-agent-logo.png" alt="" /></div><div><strong>{AGENT_NAME}</strong><span>课题数据 + 育种智能分析</span></div></div>
      <nav className="research-workspaces" aria-label="科研工作台">
        <section className="research-workspace-group" aria-label="对话">
          <small>对话</small>
          <button type="button" className={workspace === "assistant" ? "active" : ""} onClick={() => setWorkspace("assistant")}><Bot size={16} />{AGENT_NAME}</button>
        </section>
        <section className="research-workspace-group" aria-label="管理">
          <small>管理</small>
          <button type="button" className={workspace === "structured" ? "active" : ""} onClick={() => setWorkspace("structured")}><Search size={16} />结构化查询</button>
          <button type="button" className={workspace === "variety-evaluation" ? "active" : ""} onClick={() => setWorkspace("variety-evaluation")}><BarChart3 size={16} />品种评价</button>
          <button type="button" className={workspace === "base-showcase" ? "active" : ""} onClick={() => setWorkspace("base-showcase")}><Building2 size={16} />基地展示大屏</button>
          <button type="button" className={workspace === "single-plants" ? "active" : ""} onClick={() => setWorkspace("single-plants")}><Sprout size={16} />单株管理</button>
          <button type="button" className={workspace === "skills" ? "active" : ""} onClick={() => setWorkspace("skills")}><Sparkles size={16} />技能库</button>
          <button type="button" className={workspace === "knowledge" ? "active" : ""} onClick={() => setWorkspace("knowledge")}><FileText size={16} />知识库</button>
          <button type="button" className={workspace === "results" ? "active" : ""} onClick={() => setWorkspace("results")}><FileDown size={16} />结果库</button>
        </section>
      </nav>
      <label className="research-project-select">
        <span>当前课题</span>
        <select value={selectedProjectId} onChange={(event) => changeProject(event.target.value)}>
          {!projects.length && <option value="">暂无可用课题</option>}
          {projects.map((project) => <option key={project.id} value={project.id}>{project.project_name}</option>)}
        </select>
      </label>
      <button className="new-session-button" onClick={() => createSession()} disabled={!selectedProjectId}><MessageSquarePlus size={17} />新建会话</button>
      <div className="conversation-list" aria-label="历史会话">
        <small>历史会话</small>
        {sessions.map((item) => <div key={item.id} className={`conversation-item-row ${item.id === activeSessionId ? "active" : ""}`}><button className="conversation-item" onClick={() => loadConversation(item.id)}>{item.title}</button><div className="conversation-item-actions"><button title="重命名会话" onClick={() => renameSession(item)}><Pencil size={14} /></button><button className="delete" title="删除会话" onClick={() => deleteSession(item)}><Trash2 size={14} /></button></div></div>)}
      </div>
      <div className="research-sidebar-bottom">
        {accountOpen && <div className="research-account-menu"><div><strong>{user?.display_name || user?.username}</strong><span>科研人员 · 已验证登录</span></div><button type="button" onClick={() => keycloak.logout({ redirectUri: window.location.origin })}><LogOut size={15} />退出登录</button></div>}
        <button className={`research-account ${accountOpen ? "expanded" : ""}`} type="button" aria-expanded={accountOpen} onClick={() => setAccountOpen((current) => !current)}><UserRound size={17} /><div><strong>{user?.display_name || user?.username}</strong><span>科研人员</span></div><ChevronDown size={15} /></button>
        <div className="research-sidebar-foot"><ShieldCheck size={16} /><span>会话、附件、任务与结果按当前课题和登录账号双重隔离</span></div>
      </div>
    </aside>

    <main className={`research-main ${workspace === "knowledge" ? "knowledge-main" : workspace === "structured" ? "structured-main" : workspace === "gwas" ? "gwas-main" : workspace === "single-plants" ? "single-plants-main" : workspace === "variety-evaluation" ? "decision-main" : workspace === "base-showcase" ? "base-showcase-main" : workspace === "skills" ? "skills-main" : workspace === "results" ? "results-main" : ""}`}>
      {workspace !== "knowledge" && workspace !== "results" && workspace !== "skills" && workspace !== "single-plants" && workspace !== "variety-evaluation" && workspace !== "base-showcase" && <header className="research-topbar"><div><p>{workspace === "gwas" ? "固定生信工作流 · 确认后执行" : workspace === "structured" ? "当前账号授权课题数据" : "课题数据与智能分析"}</p><h1>{workspace === "assistant" ? activeSession?.title || AGENT_NAME : workspace === "gwas" ? "水稻连续性状 GWAS" : "结构化查询"}</h1></div></header>}

      {notice && <div className="assistant-notice"><span>{notice}</span><button title="关闭提示" onClick={() => setNotice("")}><X size={16} /></button></div>}

      {workspace === "assistant" && <section className="assistant-boundary"><ShieldCheck size={18} /><span>平台数据只取已发布标准数据；私有附件仅在当前会话中作为参考材料。涉及病虫害、农药、施肥或种植建议时，结果需结合当地要求和专业人员意见确认。</span></section>}

      {workspace === "assistant" ? <>
      <div className="chat-pane">
      <section className="chat-log" ref={chatLogRef} onScroll={handleChatScroll}>
        {!messages.length && <div className="chat-empty"><Bot size={28} /><h2>开始隆耘 Agent 育种对话</h2><p>数据处理员发布区域试验资料包后，可在这里直接获得同试验比较、多年多点稳定性、环境和管理影响、性状权衡及表现变差的可追溯分析。</p><div className="trial-prompt-list" aria-label="区域试验分析示例问题"><span>区域试验分析示例</span>{TRIAL_ANALYSIS_PROMPTS.map((prompt) => <button type="button" key={prompt} onClick={() => applyTrialAnalysisPrompt(prompt)}>{prompt}</button>)}</div></div>}
        {messages.map((message) => {
          const content = message.content || (message.streaming ? "正在调用大模型…" : "");
          const messageAttachments = (message.evidence || []).filter((item) => item.type === "message_attachment");
          const sourceEvidence = (message.evidence || []).filter((item) => item.type !== "message_attachment");
          return <article className={`chat-message ${message.role} ${message.error ? "error" : ""}`} key={message.id}>
            <div className="message-avatar">{message.role === "assistant" ? <Bot size={18} /> : <UserRound size={17} />}</div>
            <div className="message-content">
              <div className="message-role">{message.role === "assistant" ? AGENT_NAME : user?.display_name || "科研人员"}</div>
              <div className="message-text">
                {message.role === "assistant" ? <AssistantMarkdown content={content} streaming={message.streaming} suppressReportInstructions={message.report_available} /> : content}
              </div>
              {messageAttachments.length > 0 && <div className="message-attachment-row" aria-label="随本轮问题发送的附件">{messageAttachments.map((item) => {
                const attachment = attachmentById.get(item.attachment_id);
                const imageUrl = imagePreviewUrls[item.attachment_id];
                return <button className={`message-attachment ${item.is_image ? "image" : ""}`} type="button" key={item.attachment_id} onClick={() => attachment && showPreview(attachment)} disabled={!attachment}>
                  {item.is_image && imageUrl ? <img src={imageUrl} alt={item.title} /> : <FileText size={16} />}
                  <span><strong>{item.title}</strong><small>{item.is_image ? "图片已随本轮消息发送" : `附件已随本轮消息发送${item.size_bytes ? ` · ${formatSize(item.size_bytes)}` : ""}`}</small></span>
                </button>;
              })}</div>}
              {message.streaming && <span className="stream-cursor" />}
              {message.role === "assistant" && message.report_available && <ReportDownloadCard message={message} onDownload={() => downloadResearchReport(message.id)} />}
              {sourceEvidence.length > 0 && <details className="evidence-card"><summary>证据与数据来源 <ChevronDown size={15} /></summary>{sourceEvidence.map((item, index) => <div className="evidence-item" key={`${item.type}-${index}`}><strong>{item.priority}. {item.title}</strong><span>{item.detail}</span>{item.query_template && <span>受控查询模板：{item.query_template}</span>}{item.query_parameters && <span>已验证参数：{JSON.stringify(item.query_parameters)}</span>}{item.query_planner && <span>参数解析方式：{item.query_planner}</span>}{item.url && <a href={item.url} target="_blank" rel="noreferrer">打开公开来源</a>}</div>)}</details>}
            </div>
          </article>;
        })}
        {progress && <div className="assistant-progress"><LoaderCircle size={15} className="spin" />{progress}</div>}
      </section>
      {showLatestButton && <button className="chat-latest-button" type="button" onClick={scrollChatToLatest}><ArrowDown size={16} />{isGenerating ? "回答仍在生成，回到最新" : "回到最新消息"}</button>}
      </div>

      <section className="assistant-composer">
        <div className="attachment-row">
          {pendingImages.map((item) => <div className="attachment-chip image pending" key={item.id}><img src={item.image_url} alt="正在上传的图片" /><span>{item.file_name}</span><small>正在保存</small></div>)}
          {composerAttachments.map((item) => <div className={`attachment-chip ${item.parsing_status} ${isVisionAttachment(item) ? "image" : ""}`} key={item.id}>
            {isVisionAttachment(item) && imagePreviewUrls[item.id] ? <button className="attachment-thumbnail" title="查看原图" onClick={() => showPreview(item)}><img src={imagePreviewUrls[item.id]} alt={item.file_name} /></button> : <FileText size={14} />}
            <button title={item.parsing_status === "image_ready" ? "查看原图" : "查看本地解析预览"} onClick={() => showPreview(item)}>{item.file_name}</button>
            <small>{item.parsing_status === "parsed" ? `已解析 · ${formatSize(item.size_bytes)}` : item.parsing_status === "image_ready" ? "原图视觉分析" : "解析失败"}</small>
            <button className="attachment-remove" title="从当前会话移除附件" onClick={() => removeAttachment(item.id)}><X size={14} /></button>
          </div>)}
        </div>
        <form className={isDragActive ? "is-dragging" : ""} onSubmit={sendQuestion} onDragEnter={(event) => { event.preventDefault(); setIsDragActive(true); }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setIsDragActive(false); }} onDrop={dropFiles}>
          <textarea ref={composerTextareaRef} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={sendOnEnter} onPaste={pasteImages} placeholder="输入育种科研问题；可直接粘贴、拖入或上传图片，明确要求时可生成图表或 PDF 报告" />
          <div className="composer-actions">
            <input ref={fileInputRef} hidden type="file" multiple accept=".pdf,.docx,.xlsx,.xls,.pptx,.txt,.md,.markdown,.html,.htm,.csv,.json,.xml,.png,.jpg,.jpeg,.webp" onChange={uploadFileInput} />
            <button className="icon-button" type="button" title="上传、粘贴或拖入当前会话附件（单个不超过 10 MB）" onClick={() => fileInputRef.current?.click()} disabled={uploading}><Paperclip size={18} /></button>
            <span>{uploading ? "正在保存附件" : sending ? "模型正在生成，可继续编辑下一条问题或添加图片；当前问题完成后再发送" : "图片原图直接送入多模态视觉分析；PDF、Office、表格和文本在本地解析；按 Enter 发送，Shift + Enter 换行"}</span>
            <button className="primary-button send-button" type="submit" disabled={!draft.trim() || sending || uploading}><SendHorizontal size={17} />发送</button>
          </div>
        </form>
      </section>
      </> : workspace === "structured" ? <StructuredQueryPanel onNotice={setNotice} projectId={selectedProjectId} /> : workspace === "gwas" ? <GwasWorkspace onNotice={setNotice} projectId={selectedProjectId} /> : workspace === "variety-evaluation" ? <VarietyEvaluationWorkspace onNotice={setNotice} projectId={selectedProjectId} /> : workspace === "base-showcase" ? <BaseShowcaseWorkspace onNotice={setNotice} projectId={selectedProjectId} /> : workspace === "single-plants" ? <SinglePlantResearchWorkspace onNotice={setNotice} projectId={selectedProjectId} /> : workspace === "skills" ? <SkillLibrary onNotice={setNotice} onOpenWorkspace={openResearchSkill} /> : workspace === "knowledge" ? <KnowledgeLibrary onNotice={setNotice} projectId={selectedProjectId} /> : <ResultsLibrary onNotice={setNotice} projectId={selectedProjectId} />}
    </main>

    {preview && <div className="attachment-preview-backdrop" onMouseDown={() => setPreview(null)}><section className={`attachment-preview ${preview.image_url ? "image-preview" : ""}`} onMouseDown={(event) => event.stopPropagation()}><header><div><p>{preview.image_url ? "当前会话私有原图" : "本地解析文字预览"}</p><h2>{preview.file_name}</h2></div><button className="icon-button" title="关闭预览" onClick={() => setPreview(null)}><X size={17} /></button></header>{preview.parser_warnings?.map((warning) => <div className="preview-warning" key={warning}>{warning}</div>)}{preview.image_url ? <div className="attachment-image-full"><img src={preview.image_url} alt={preview.file_name} /></div> : <pre>{preview.preview || "该附件未解析出可预览文本。"}</pre>}{preview.preview_truncated && <small>预览仅显示前 60,000 个字符；完整文本仍仅保存在当前会话的私有附件中。</small>}</section></div>}
  </div>;
}
