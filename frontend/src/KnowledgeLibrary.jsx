import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  FileSearch,
  FileText,
  Filter,
  Folder,
  FolderOpen,
  FolderPlus,
  LoaderCircle,
  Pencil,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { request } from "./api";

const ACCEPTED_FILES = ".pdf,.docx,.xlsx,.xls,.csv,.pptx,.txt,.md,.markdown,.html,.htm,.json,.xml";

const EMPTY_METADATA = {
  sourceOrganization: "",
  author: "",
  publicationYear: "",
  sourceUrl: "",
  description: "",
  authorizationBasis: "",
  licenseScope: "公开资料",
  topicTags: "",
  versionChangeSummary: "",
};

function formatSize(bytes = 0) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date(value));
}

function formatDateTime(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function statusLabel(document) {
  if (document.parsing_status === "failed" || document.indexing_status === "failed") return "解析失败";
  if (document.indexing_status === "pending_model") return "等待建立索引";
  if (document.parsing_status === "processing") return "正在本地解析";
  if (document.indexing_status === "processing" || document.indexing_status === "pending") return "正在建立索引";
  if (document.status === "review") return "待核验";
  if (document.status === "published") return "已发布";
  if (document.status === "withdrawn") return "已撤回";
  if (document.status === "superseded") return "已被新版本替代";
  if (document.status === "ready") return "可作为私人知识证据";
  return "处理中";
}

function documentTone(document) {
  if (document.parsing_status === "failed" || document.indexing_status === "failed") return "failed";
  if (document.status === "review" || document.indexing_status === "pending_model") return "pending";
  if (document.status === "published" || document.status === "ready") return "ready";
  return "processing";
}

function filterStatus(document) {
  if (document.parsing_status === "failed" || document.indexing_status === "failed") return "failed";
  if (document.parsing_status === "processing" || document.indexing_status === "processing" || document.indexing_status === "pending") return "processing";
  if (document.status === "review" || document.indexing_status === "pending_model") return "review";
  if (document.status === "published") return "published";
  return "ready";
}

function folderLabel(folder, scope) {
  if (folder) return folder.folder_name;
  return scope === "public" ? "未分类" : "未分类";
}

export default function KnowledgeLibrary({ adminMode = false, onNotice = () => {} }) {
  const [scope, setScope] = useState(adminMode ? "public" : "private");
  const [folders, setFolders] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [summary, setSummary] = useState(null);
  const [selectedFolderId, setSelectedFolderId] = useState("");
  const [query, setQuery] = useState("");
  const [folderQuery, setFolderQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [metadata, setMetadata] = useState(EMPTY_METADATA);
  const [editing, setEditing] = useState(null);
  const [editingFolder, setEditingFolder] = useState(null);
  const [preview, setPreview] = useState(null);
  const [detailDocument, setDetailDocument] = useState(null);
  const [versionTarget, setVersionTarget] = useState(null);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [pendingUploadFiles, setPendingUploadFiles] = useState([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filters, setFilters] = useState({ status: "all", year: "all", source: "" });
  const fileInputRef = useRef(null);
  const filterRef = useRef(null);

  const isPublic = scope === "public";
  const showAdministration = adminMode && isPublic;

  const folderRows = useMemo(() => {
    const children = new Map();
    folders.forEach((folder) => {
      const key = folder.parent_id || "root";
      children.set(key, [...(children.get(key) || []), folder]);
    });
    children.forEach((items) => items.sort((left, right) => left.folder_name.localeCompare(right.folder_name, "zh-CN")));
    const rows = [];
    const addChildren = (parentId, depth) => {
      (children.get(parentId) || []).forEach((folder) => {
        rows.push({ folder, depth });
        addChildren(folder.id, depth + 1);
      });
    };
    addChildren("root", 0);
    return rows;
  }, [folders]);

  const filteredFolderRows = useMemo(() => {
    const keyword = folderQuery.trim().toLocaleLowerCase("zh-CN");
    if (!keyword) return folderRows;
    return folderRows.filter(({ folder }) => folder.folder_name.toLocaleLowerCase("zh-CN").includes(keyword));
  }, [folderQuery, folderRows]);

  const folderDocumentCounts = useMemo(() => documents.reduce((counts, document) => {
    if (document.folder_id) counts[document.folder_id] = (counts[document.folder_id] || 0) + 1;
    return counts;
  }, {}), [documents]);

  const availableYears = useMemo(() => [...new Set(
    documents.map((document) => String(document.publication_year || "").trim()).filter(Boolean),
  )].sort((left, right) => right.localeCompare(left, "zh-CN", { numeric: true })), [documents]);

  const hasActiveProcessing = documents.some((document) => (
    document.parsing_status === "processing"
    || document.indexing_status === "pending"
    || document.indexing_status === "processing"
  ));

  const visibleDocuments = useMemo(() => documents
    .filter((document) => !selectedFolderId || document.folder_id === selectedFolderId)
    .filter((document) => filters.status === "all" || filterStatus(document) === filters.status)
    .filter((document) => filters.year === "all" || String(document.publication_year || "") === filters.year)
    .filter((document) => !filters.source.trim() || document.source_organization.toLocaleLowerCase("zh-CN").includes(filters.source.trim().toLocaleLowerCase("zh-CN"))), [documents, filters, selectedFolderId]);

  const selectedFolder = folders.find((folder) => folder.id === selectedFolderId);

  async function load(nextScope = scope, keepFolder = false, { silent = false } = {}) {
    if (!silent) setLoading(true);
    try {
      const includeUnpublished = nextScope === "public" && adminMode;
      const [folderList, documentList, nextSummary] = await Promise.all([
        request(`/api/knowledge/folders?scope=${nextScope}`),
        request(`/api/knowledge/documents?scope=${nextScope}&q=${encodeURIComponent(query)}${includeUnpublished ? "&include_unpublished=true" : ""}`),
        request("/api/knowledge/summary"),
      ]);
      setFolders(folderList);
      setDocuments(documentList);
      setSummary(nextSummary);
      if (!keepFolder) setSelectedFolderId("");
    } catch (error) {
      onNotice(error.message || "无法读取知识库资料目录。");
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => { load(scope); }, [scope]);

  useEffect(() => {
    if (!hasActiveProcessing) return undefined;
    let disposed = false;
    let timerId;
    const refreshProcessingStatus = async () => {
      await load(scope, true, { silent: true });
      if (!disposed) timerId = window.setTimeout(refreshProcessingStatus, 2000);
    };
    timerId = window.setTimeout(refreshProcessingStatus, 1500);
    return () => {
      disposed = true;
      window.clearTimeout(timerId);
    };
  }, [hasActiveProcessing, scope, query]);

  useEffect(() => {
    if (!filterOpen) return undefined;
    const closeFilterOnOutsideClick = (event) => {
      if (!filterRef.current?.contains(event.target)) setFilterOpen(false);
    };
    window.addEventListener("mousedown", closeFilterOnOutsideClick);
    return () => window.removeEventListener("mousedown", closeFilterOnOutsideClick);
  }, [filterOpen]);

  function switchScope(nextScope) {
    if (nextScope === scope) return;
    setScope(nextScope);
    setQuery("");
    setFolderQuery("");
    setSelectedFolderId("");
    setFilters({ status: "all", year: "all", source: "" });
    setDetailDocument(null);
    setVersionTarget(null);
    setUploadDialogOpen(false);
  }

  async function createFolder(event) {
    event.preventDefault();
    if (!folderName.trim()) return;
    try {
      await request(`/api/knowledge/folders?scope=${scope}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_name: folderName.trim(), parent_id: selectedFolderId || null }),
      });
      setFolderName("");
      await load(scope, true);
      onNotice("资料分类已建立。");
    } catch (error) {
      onNotice(error.message || "资料分类未能建立。");
    }
  }

  async function saveFolder(event) {
    event.preventDefault();
    if (!editingFolder?.folder_name?.trim()) return;
    try {
      await request(`/api/knowledge/folders/${editingFolder.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          folder_name: editingFolder.folder_name.trim(),
          parent_id: editingFolder.parent_id || null,
          description: editingFolder.description || "",
        }),
      });
      setEditingFolder(null);
      await load(scope, true);
      onNotice("资料分类已更新。");
    } catch (error) {
      onNotice(error.message || "资料分类更新失败。");
    }
  }

  async function deleteFolder(folder) {
    if (!window.confirm(`删除资料分类“${folder.folder_name}”？分类必须为空，资料不会被自动删除。`)) return;
    try {
      await request(`/api/knowledge/folders/${folder.id}`, { method: "DELETE" });
      if (selectedFolderId === folder.id) setSelectedFolderId("");
      await load(scope, true);
      onNotice("资料分类已删除。");
    } catch (error) {
      onNotice(error.message || "资料分类无法删除，请先移动其中资料或子分类。");
    }
  }

  function resetUploadState() {
    setPendingUploadFiles([]);
    setVersionTarget(null);
    setMetadata(EMPTY_METADATA);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function openUploadDialog(nextVersionTarget = null) {
    setDetailDocument(null);
    setVersionTarget(nextVersionTarget);
    setPendingUploadFiles([]);
    setMetadata(nextVersionTarget ? {
      sourceOrganization: nextVersionTarget.source_organization || "",
      author: nextVersionTarget.author || "",
      publicationYear: nextVersionTarget.publication_year || "",
      sourceUrl: nextVersionTarget.source_url || "",
      description: nextVersionTarget.short_description || "",
      authorizationBasis: nextVersionTarget.authorization_basis || "",
      licenseScope: nextVersionTarget.license_scope || "公开资料",
      topicTags: (nextVersionTarget.topic_tags || []).join("，"),
      versionChangeSummary: "",
    } : EMPTY_METADATA);
    setUploadDialogOpen(true);
  }

  function closeUploadDialog() {
    if (uploading) return;
    setUploadDialogOpen(false);
    resetUploadState();
  }

  function selectUploadFiles(event) {
    setPendingUploadFiles([...event.target.files || []]);
    event.target.value = "";
  }

  async function uploadFiles(event) {
    event.preventDefault();
    const files = pendingUploadFiles;
    const uploadFolderId = versionTarget?.folder_id || selectedFolderId;
    if (!files.length) {
      onNotice("请先选择需要保存的资料文件。");
      return;
    }
    if (isPublic && !uploadFolderId) {
      onNotice("公共知识库上传前必须选择资料分类。");
      return;
    }
    if (isPublic && !metadata.sourceOrganization.trim()) {
      onNotice("公共资料发布前需要填写来源单位，便于科研引用溯源。");
      return;
    }
    if (isPublic && (!metadata.authorizationBasis.trim() || !metadata.licenseScope.trim())) {
      onNotice("公共资料必须填写授权依据和授权范围，未核验授权的资料不能发布。");
      return;
    }
    if (versionTarget && !metadata.versionChangeSummary.trim()) {
      onNotice("上传公共资料新版本时，请说明本次新增或修改的内容。");
      return;
    }
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    setUploading(true);
    try {
      const params = new URLSearchParams({
        scope,
        ...(uploadFolderId ? { folder_id: uploadFolderId } : {}),
        ...(metadata.sourceOrganization.trim() ? { source_organization: metadata.sourceOrganization.trim() } : {}),
        ...(metadata.author.trim() ? { author: metadata.author.trim() } : {}),
        ...(metadata.publicationYear.trim() ? { publication_year: metadata.publicationYear.trim() } : {}),
        ...(metadata.sourceUrl.trim() ? { source_url: metadata.sourceUrl.trim() } : {}),
        ...(metadata.description.trim() ? { short_description: metadata.description.trim() } : {}),
        ...(metadata.authorizationBasis.trim() ? { authorization_basis: metadata.authorizationBasis.trim() } : {}),
        ...(metadata.licenseScope.trim() ? { license_scope: metadata.licenseScope.trim() } : {}),
        ...(metadata.topicTags.trim() ? { topic_tags: metadata.topicTags.trim() } : {}),
        ...(versionTarget ? { supersedes_document_id: versionTarget.id } : {}),
        ...(versionTarget ? { version_change_summary: metadata.versionChangeSummary.trim() } : {}),
      });
      await request(`/api/knowledge/documents?${params.toString()}`, { method: "POST", body: form });
      setUploadDialogOpen(false);
      resetUploadState();
      await load(scope, true);
      onNotice("资料已保存在本地，正在自动解析并建立向量索引。");
    } catch (error) {
      onNotice(error.message || "文件上传失败。");
    } finally {
      setUploading(false);
    }
  }

  async function saveMetadata(event) {
    event.preventDefault();
    if (!editing) return;
    try {
      await request(`/api/knowledge/documents/${editing.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_title: editing.display_title,
          folder_id: editing.folder_id || null,
          source_organization: editing.source_organization || "",
          author: editing.author || "",
          publication_year: editing.publication_year || "",
          source_url: editing.source_url || "",
          short_description: editing.short_description || "",
          authorization_basis: editing.authorization_basis || "",
          license_scope: editing.license_scope || "",
          topic_tags: editing.topic_tags || [],
        }),
      });
      setEditing(null);
      await load(scope, true);
      onNotice("资料目录信息已更新。");
    } catch (error) {
      onNotice(error.message || "资料信息保存失败。");
    }
  }

  async function openPreview(document) {
    try {
      setPreview(await request(`/api/knowledge/documents/${document.id}/preview`));
    } catch (error) {
      onNotice(error.message || "无法读取本地解析预览。");
    }
  }

  async function publishDocument(document) {
    if (!window.confirm("请确认已核验本地解析预览、来源和分类信息。发布后科研人员可通过助手检索引用该资料。")) return;
    try {
      await request(`/api/knowledge/documents/${document.id}/publish`, { method: "POST" });
      await load(scope, true);
      onNotice("公共资料已发布，科研人员可以在助手中检索其证据片段。");
    } catch (error) {
      onNotice(error.message || "资料暂不能发布。");
    }
  }

  async function reindex(document) {
    try {
      await request(`/api/knowledge/documents/${document.id}/reindex`, { method: "POST" });
      await load(scope, true);
      onNotice("已提交本地重新解析和向量索引任务。");
    } catch (error) {
      onNotice(error.message || "无法重新处理该资料。");
    }
  }

  async function withdraw(document) {
    if (!window.confirm("撤回后科研人员无法再检索这份公共资料，审计和历史版本仍会保留。")) return;
    try {
      await request(`/api/knowledge/documents/${document.id}/withdraw`, { method: "POST" });
      await load(scope, true);
      onNotice("公共资料已撤回。");
    } catch (error) {
      onNotice(error.message || "资料撤回失败。");
    }
  }

  async function syncInstitutionLiterature() {
    try {
      const result = await request("/api/knowledge/sync-institution-literature", { method: "POST" });
      await load(scope, true);
      onNotice(`已从机构数据导入层新增 ${result.created_count} 份待核验资料，跳过 ${result.skipped_count} 份空内容或重复资料。`);
    } catch (error) {
      onNotice(error.message || "无法同步机构导入的文献资料。");
    }
  }

  async function deletePrivate(document) {
    if (!window.confirm("确定永久删除这份私人资料吗？原始文件、解析文本、切片和向量索引都会一并删除，无法恢复。")) return;
    try {
      await request(`/api/knowledge/documents/${document.id}`, { method: "DELETE" });
      if (detailDocument?.id === document.id) setDetailDocument(null);
      await load(scope, true);
      onNotice("私人资料已永久删除。");
    } catch (error) {
      onNotice(error.message || "资料删除失败。");
    }
  }

  function renderDocumentActions(document) {
    if (showAdministration) {
      return <div className="knowledge-doc-actions" onClick={(event) => event.stopPropagation()}>
        <button className="icon-button" type="button" title="编辑资料目录" onClick={() => setEditing(document)}><Pencil size={15} /></button>
        <button className="icon-button" type="button" title="查看本地解析预览" onClick={() => openPreview(document)}><FileSearch size={15} /></button>
        {(document.parsing_status === "failed" || document.indexing_status === "pending_model") && <button className="icon-button" type="button" title="重新处理" onClick={() => reindex(document)}><RotateCcw size={15} /></button>}
        {document.status === "review" && <button className="text-button success" type="button" onClick={() => publishDocument(document)}>发布</button>}
        {document.status === "published" && <><button className="text-button" type="button" onClick={() => openUploadDialog(document)}>新版本</button><button className="text-button danger" type="button" onClick={() => withdraw(document)}>撤回</button></>}
      </div>;
    }
    if (!isPublic) {
      return <div className="knowledge-doc-actions" onClick={(event) => event.stopPropagation()}>
        <button className="icon-button" type="button" title="编辑资料目录" onClick={() => setEditing(document)}><Pencil size={15} /></button>
        <button className="icon-button danger-icon" type="button" title="永久删除私人资料" onClick={() => deletePrivate(document)}><Trash2 size={15} /></button>
      </div>;
    }
    return <span className="knowledge-table-muted">-</span>;
  }

  function chooseFolder(folderId) {
    setSelectedFolderId(folderId);
    setDetailDocument(null);
  }

  return <section className={adminMode ? "knowledge-library knowledge-workspace knowledge-workspace-admin" : "knowledge-library knowledge-workspace"}>
    <div className="knowledge-workspace-shell">
      <aside className="knowledge-workspace-folders">
        <div className="knowledge-folder-brand">
          <div className="knowledge-breadcrumb">隆耘 Agent 育种智能体 <ChevronRight size={13} /> 知识库</div>
          <h2>{adminMode ? "公共知识库管理" : "知识库"}</h2>
          {!adminMode && <nav className="knowledge-workspace-tabs" aria-label="知识库范围">
            <button type="button" className={scope === "private" ? "active" : ""} onClick={() => switchScope("private")}>我的知识库</button>
            <button type="button" className={scope === "public" ? "active" : ""} onClick={() => switchScope("public")}>公共知识库</button>
          </nav>}
          {adminMode && <div className="knowledge-admin-note">字段管理员维护公共资料：核验解析和资料元数据后，再发布给科研人员检索。</div>}
        </div>
        <div className="knowledge-folder-search"><Search size={15} /><input value={folderQuery} onChange={(event) => setFolderQuery(event.target.value)} placeholder="搜索文件夹" /><button className="icon-button" type="button" title="新建分类" onClick={() => document.getElementById("knowledge-folder-name")?.focus()}><FolderPlus size={15} /></button></div>
        <div className="knowledge-folder-tree-title">{isPublic ? "资料分类" : "我的文件夹"}</div>
        <button className={`knowledge-folder-item ${!selectedFolderId ? "active" : ""}`} type="button" onClick={() => chooseFolder("")}><FolderOpen size={15} /><span>{isPublic ? "全部公共资料" : "全部资料"}</span><small>{documents.length}</small></button>
        <div className="knowledge-folder-tree">
          {filteredFolderRows.map(({ folder, depth }) => <div className={`knowledge-folder-row ${selectedFolderId === folder.id ? "active" : ""}`} key={folder.id}>
            <button className="knowledge-folder-item" type="button" onClick={() => chooseFolder(folder.id)}><span className="knowledge-folder-indent" style={{ width: `${depth * 14}px` }} /><ChevronRight size={14} /><Folder size={15} /><span>{folder.folder_name}</span><small>{folderDocumentCounts[folder.id] || 0}</small></button>
            <span className="knowledge-folder-actions"><button type="button" className="icon-button" title="编辑分类" onClick={() => setEditingFolder(folder)}><Pencil size={13} /></button><button type="button" className="icon-button danger-icon" title="删除空分类" onClick={() => deleteFolder(folder)}><Trash2 size={13} /></button></span>
          </div>)}
        </div>
        <form className="knowledge-folder-form" onSubmit={createFolder}><input id="knowledge-folder-name" value={folderName} onChange={(event) => setFolderName(event.target.value)} placeholder={isPublic ? "新增公共子分类" : "新增私人文件夹"} /><button className="secondary-button" type="submit">建立</button></form>
      </aside>

      <div className="knowledge-workspace-content">
        <header className="knowledge-content-header">
          <span>资料仅用于本地检索和助手引用，不提供原文下载</span>
          <div>
            <span className="knowledge-content-count">{isPublic ? `公共资料 ${summary?.public_published_count ?? 0} 份` : `我的资料 ${summary?.private_document_count ?? 0} 份 · ${formatSize(summary?.private_size_bytes || 0)}`}</span>
            <button className="icon-button" type="button" title="刷新资料目录" onClick={() => load(scope, true)}><RefreshCw size={17} /></button>
            {showAdministration && <button className="secondary-button" type="button" onClick={syncInstitutionLiterature}><RefreshCw size={15} />同步机构文献</button>}
            {(!isPublic || showAdministration) && <button className="primary-button" type="button" onClick={() => openUploadDialog()}><Upload size={16} />上传资料</button>}
          </div>
        </header>
        <div className="knowledge-content-toolbar">
          <form className="knowledge-search-box" onSubmit={(event) => { event.preventDefault(); load(scope, true); }}><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、文件名、作者或来源" /><button className="secondary-button" type="submit">搜索</button></form>
          <div className="knowledge-filter-wrap" ref={filterRef}>
            <button className={`secondary-button ${filterOpen ? "active" : ""}`} type="button" onClick={() => setFilterOpen(!filterOpen)}><Filter size={15} />筛选</button>
            {filterOpen && <section className="knowledge-filter-panel">
              <div><strong>筛选条件</strong><button className="text-button" type="button" onClick={() => setFilters({ status: "all", year: "all", source: "" })}>清除</button></div>
              <label>解析状态<span className="knowledge-filter-chips">{[
                ["all", "全部"], ["published", "已发布"], ["processing", "正在解析"], ["review", "待核验"], ["failed", "解析失败"],
              ].map(([value, label]) => <button type="button" className={filters.status === value ? "active" : ""} key={value} onClick={() => setFilters({ ...filters, status: value })}>{label}</button>)}</span></label>
              <label>年份<span className="knowledge-filter-chips">{[["all", "全部"], ...availableYears.slice(0, 5).map((year) => [year, year])].map(([value, label]) => <button type="button" className={filters.year === value ? "active" : ""} key={value} onClick={() => setFilters({ ...filters, year: value })}>{label}</button>)}</span></label>
              <label>来源单位<input value={filters.source} onChange={(event) => setFilters({ ...filters, source: event.target.value })} placeholder="输入单位关键字" /></label>
              <button className="primary-button knowledge-filter-apply" type="button" onClick={() => setFilterOpen(false)}>应用筛选</button>
            </section>}
          </div>
          <span className="knowledge-sort-label">按更新时间排序</span>
        </div>

        <div className="knowledge-directory-heading">
          <div><span>{isPublic ? "已发布与待核验资料" : "个人资料目录"}</span><h3>{folderLabel(selectedFolder, scope)}</h3></div>
          {hasActiveProcessing && <span className="knowledge-live-status"><LoaderCircle size={14} className="spin" />正在自动更新解析状态</span>}
        </div>

        {loading ? <div className="knowledge-loading"><LoaderCircle size={18} className="spin" />正在读取资料目录</div> : <div className="knowledge-table-scroll">
          <table className="knowledge-document-table">
            <thead><tr><th>资料标题</th><th>分类</th><th>来源单位</th><th>年份</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
            <tbody>{visibleDocuments.length ? visibleDocuments.map((document) => <tr key={document.id} className="knowledge-document-row" tabIndex={0} onClick={() => setDetailDocument(document)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setDetailDocument(document); } }}>
              <td><div className="knowledge-document-cell"><span className="knowledge-file-icon"><FileText size={17} /></span><div><strong>{document.display_title}</strong><small>{document.original_file_name} · {formatSize(document.size_bytes)}</small></div></div></td>
              <td>{document.folder_name || "未分类"}</td>
              <td>{document.source_organization || "-"}</td>
              <td>{document.publication_year || "-"}</td>
              <td><span className={`knowledge-status ${documentTone(document)}`}>{statusLabel(document)}</span></td>
              <td>{formatDate(document.updated_at)}</td>
              <td>{renderDocumentActions(document)}</td>
            </tr>) : <tr><td className="knowledge-empty-cell" colSpan="7"><ShieldCheck size={22} /><strong>{isPublic ? "暂无可查看的公共资料" : "我的知识库还没有资料"}</strong><span>{isPublic ? "公共资料发布后会出现在这里，并可通过助手进行证据检索。" : "上传论文、规范或报告后，系统会在本地解析并建立向量索引。"}</span></td></tr>}</tbody>
          </table>
        </div>}
      </div>
    </div>

    {uploadDialogOpen && <div className="knowledge-modal-backdrop" onMouseDown={closeUploadDialog}><form className="knowledge-modal knowledge-upload-modal" onSubmit={uploadFiles} onMouseDown={(event) => event.stopPropagation()}>
      <header><div><p>{versionTarget ? "创建新的公共资料版本，历史版本会保留可追溯记录" : "资料先保存在本地，后台将自动解析并建立向量索引"}</p><h3>{versionTarget ? "上传资料新版本" : "上传资料"}</h3></div><button className="icon-button" type="button" title="关闭" onClick={closeUploadDialog}><X size={17} /></button></header>
      {versionTarget && <div className="knowledge-version-context">正在为“{versionTarget.display_title}”上传新版本。</div>}
      <label className="knowledge-upload-dropzone"><input ref={fileInputRef} hidden type="file" multiple={!versionTarget} accept={ACCEPTED_FILES} onChange={selectUploadFiles} /><Upload size={22} /><strong>{pendingUploadFiles.length ? `已选择 ${pendingUploadFiles.length} 个文件` : "拖放文件至此处，或点击选择"}</strong><span>支持 PDF、Excel、Word、PPT 和文本资料，单文件不超过 100 MB</span></label>
      {pendingUploadFiles.length > 0 && <div className="knowledge-selected-files">{pendingUploadFiles.map((file) => <span key={`${file.name}-${file.lastModified}`}><FileText size={14} />{file.name}<small>{formatSize(file.size)}</small></span>)}</div>}
      <label>存入分类<select value={versionTarget?.folder_id || selectedFolderId} disabled={Boolean(versionTarget)} onChange={(event) => setSelectedFolderId(event.target.value)} required={isPublic}><option value="">{isPublic ? "请选择公共资料分类" : "未分类"}</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.folder_name}</option>)}</select></label>
      <details className="knowledge-optional-metadata" open={isPublic}><summary>补充资料信息{isPublic ? "（来源与授权必填）" : "（可选）"}</summary><div><label>来源单位{isPublic && <b> *</b>}<input value={metadata.sourceOrganization} onChange={(event) => setMetadata({ ...metadata, sourceOrganization: event.target.value })} /></label><label>作者<input value={metadata.author} onChange={(event) => setMetadata({ ...metadata, author: event.target.value })} /></label><label>发表年份<input value={metadata.publicationYear} onChange={(event) => setMetadata({ ...metadata, publicationYear: event.target.value })} /></label><label>来源链接<input value={metadata.sourceUrl} onChange={(event) => setMetadata({ ...metadata, sourceUrl: event.target.value })} /></label>{isPublic && <><label>授权范围<b> *</b><select value={metadata.licenseScope} onChange={(event) => setMetadata({ ...metadata, licenseScope: event.target.value })}><option value="公开资料">公开资料</option><option value="合法授权资料">合法授权资料</option><option value="限本课题授权资料">限本课题授权资料</option></select></label><label className="knowledge-metadata-wide">授权依据<b> *</b><textarea value={metadata.authorizationBasis} onChange={(event) => setMetadata({ ...metadata, authorizationBasis: event.target.value })} placeholder="例如：政府官网公开发布；或授权单位、授权日期和使用范围" /></label><label className="knowledge-metadata-wide">主题标签<input value={metadata.topicTags} onChange={(event) => setMetadata({ ...metadata, topicTags: event.target.value })} placeholder="品种，基因，性状，育种目标" /></label></>}<label className="knowledge-metadata-wide">解析摘要或备注<textarea value={metadata.description} onChange={(event) => setMetadata({ ...metadata, description: event.target.value })} /></label>{versionTarget && <label className="knowledge-metadata-wide">本次版本新增或修改内容<b> *</b><textarea value={metadata.versionChangeSummary} required onChange={(event) => setMetadata({ ...metadata, versionChangeSummary: event.target.value })} /></label>}</div></details>
      <footer><span>上传后系统将自动启动本地解析，通常需要 1 至 3 分钟。</span><div><button type="button" className="secondary-button" onClick={closeUploadDialog}>取消</button><button type="submit" className="primary-button" disabled={uploading}>{uploading ? "正在保存" : "确认上传"}</button></div></footer>
    </form></div>}

    {detailDocument && <div className="knowledge-detail-backdrop" onMouseDown={() => setDetailDocument(null)}><aside className="knowledge-detail-drawer" onMouseDown={(event) => event.stopPropagation()}>
      <header><strong>资料详情</strong><button className="icon-button" type="button" title="关闭" onClick={() => setDetailDocument(null)}><X size={17} /></button></header>
      <div className="knowledge-detail-file"><span className="knowledge-file-icon"><FileText size={22} /></span><div><strong>{detailDocument.display_title}</strong><small>{detailDocument.original_file_name}</small></div></div>
      <dl className="knowledge-detail-metadata"><div><dt>所属分类</dt><dd>{detailDocument.folder_name || "未分类"}</dd></div><div><dt>来源单位</dt><dd>{detailDocument.source_organization || "未填写"}</dd></div><div><dt>作者</dt><dd>{detailDocument.author || "未填写"}</dd></div><div><dt>发表年份</dt><dd>{detailDocument.publication_year || "未填写"}</dd></div><div><dt>最近更新</dt><dd>{formatDateTime(detailDocument.updated_at)}</dd></div><div><dt>解析状态</dt><dd><span className={`knowledge-status ${documentTone(detailDocument)}`}>{statusLabel(detailDocument)}</span></dd></div></dl>
      {detailDocument.short_description && <section className="knowledge-detail-summary"><strong>解析摘要</strong><p>{detailDocument.short_description}</p></section>}
      {detailDocument.scope === "public" && <section className="knowledge-detail-summary"><strong>授权与引用边界</strong><p>{detailDocument.license_scope || "未填写授权范围"} · {detailDocument.authorization_basis || "未填写授权依据"}</p>{detailDocument.topic_tags?.length > 0 && <p>主题标签：{detailDocument.topic_tags.join("、")}</p>}</section>}
      {detailDocument.version_change_summary && <section className="knowledge-detail-summary"><strong>版本说明</strong><p>{detailDocument.version_change_summary}</p></section>}
      <section className="knowledge-detail-reference"><CheckCircle2 size={16} /><span>助手仅在回答中展示命中的证据片段和资料来源，不开放原文阅读或下载。</span></section>
      {showAdministration && <div className="knowledge-detail-admin"><button type="button" className="secondary-button" onClick={() => openPreview(detailDocument)}><FileSearch size={15} />核验本地解析</button></div>}
      <footer>不提供原文下载</footer>
    </aside></div>}

    {editing && <div className="knowledge-modal-backdrop" onMouseDown={() => setEditing(null)}><form className="knowledge-modal" onSubmit={saveMetadata} onMouseDown={(event) => event.stopPropagation()}><header><div><p>只维护资料目录信息，不改变原始文件。</p><h3>编辑资料</h3></div><button className="icon-button" type="button" onClick={() => setEditing(null)}><X size={17} /></button></header><label>显示标题<input value={editing.display_title} required onChange={(event) => setEditing({ ...editing, display_title: event.target.value })} /></label><label>资料分类<select value={editing.folder_id || ""} onChange={(event) => setEditing({ ...editing, folder_id: event.target.value || null })} required={editing.scope === "public"}><option value="">{editing.scope === "public" ? "请选择资料分类" : "未分类"}</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.folder_name}</option>)}</select></label><label>来源单位<input value={editing.source_organization || ""} onChange={(event) => setEditing({ ...editing, source_organization: event.target.value })} /></label><label>作者<input value={editing.author || ""} onChange={(event) => setEditing({ ...editing, author: event.target.value })} /></label><label>年份<input value={editing.publication_year || ""} onChange={(event) => setEditing({ ...editing, publication_year: event.target.value })} /></label><label>来源链接<input value={editing.source_url || ""} onChange={(event) => setEditing({ ...editing, source_url: event.target.value })} /></label>{editing.scope === "public" && <><label>授权范围<input value={editing.license_scope || ""} required onChange={(event) => setEditing({ ...editing, license_scope: event.target.value })} /></label><label>授权依据<textarea value={editing.authorization_basis || ""} required onChange={(event) => setEditing({ ...editing, authorization_basis: event.target.value })} /></label><label>主题标签<input value={(editing.topic_tags || []).join("，")} onChange={(event) => setEditing({ ...editing, topic_tags: event.target.value.split(/[，,；;]/).map((item) => item.trim()).filter(Boolean) })} /></label></>}<label>摘要说明<textarea value={editing.short_description || ""} onChange={(event) => setEditing({ ...editing, short_description: event.target.value })} /></label><footer><button type="button" className="secondary-button" onClick={() => setEditing(null)}>取消</button><button type="submit" className="primary-button">保存资料目录</button></footer></form></div>}

    {editingFolder && <div className="knowledge-modal-backdrop" onMouseDown={() => setEditingFolder(null)}><form className="knowledge-modal" onSubmit={saveFolder} onMouseDown={(event) => event.stopPropagation()}><header><div><p>不改变资料原文，只维护目录结构。</p><h3>编辑资料分类</h3></div><button className="icon-button" type="button" onClick={() => setEditingFolder(null)}><X size={17} /></button></header><label>分类名称<input value={editingFolder.folder_name} required onChange={(event) => setEditingFolder({ ...editingFolder, folder_name: event.target.value })} /></label><label>上级分类<select value={editingFolder.parent_id || ""} onChange={(event) => setEditingFolder({ ...editingFolder, parent_id: event.target.value || null })}><option value="">顶级分类</option>{folders.filter((folder) => folder.id !== editingFolder.id).map((folder) => <option key={folder.id} value={folder.id}>{folder.folder_name}</option>)}</select></label><label>说明<textarea value={editingFolder.description || ""} onChange={(event) => setEditingFolder({ ...editingFolder, description: event.target.value })} /></label><footer><button type="button" className="secondary-button" onClick={() => setEditingFolder(null)}>取消</button><button type="submit" className="primary-button">保存分类</button></footer></form></div>}

    {preview && <div className="knowledge-modal-backdrop" onMouseDown={() => setPreview(null)}><section className="knowledge-preview-modal" onMouseDown={(event) => event.stopPropagation()}><header><div><p>字段管理员本地解析核验</p><h3>{preview.display_title}</h3></div><button className="icon-button" type="button" onClick={() => setPreview(null)}><X size={17} /></button></header>{preview.parser_warnings?.map((warning) => <div className="knowledge-warning" key={warning}>{warning}</div>)}<pre>{preview.preview || "未解析到足够的可用文字。"}</pre>{preview.preview_truncated && <small>预览仅显示前 60,000 个字符。</small>}</section></div>}
  </section>;
}
