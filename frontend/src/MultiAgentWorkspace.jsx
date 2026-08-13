import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, CheckCircle2, Clock3, LoaderCircle, Play, RefreshCw, ShieldCheck, Sparkles, XCircle } from "lucide-react";

import { request } from "./api";


const TERMINAL = new Set(["completed", "failed", "cancelled"]);


function statusLabel(status) {
  return {
    queued: "排队中",
    running: "分析中",
    completed: "已完成",
    failed: "执行失败",
    cancelled: "已取消",
  }[status] || status;
}


export default function MultiAgentWorkspace({ sessionId, attachmentIds = [], onNotice, projectId }) {
  const [catalog, setCatalog] = useState([]);
  const [modelPolicy, setModelPolicy] = useState({ deployment_mode: "external_api", external_data_acknowledgement_required: true, private_evidence_allowed: false });
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState([]);
  const [content, setContent] = useState("");
  const [includeAttachments, setIncludeAttachments] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    if (!projectId) {
      setRuns([]);
      return;
    }
    const [agentPayload, runPayload] = await Promise.all([
      request("/api/agents"),
      request(`/api/agent-workflows?limit=30&project_id=${encodeURIComponent(projectId)}`),
    ]);
    setCatalog(agentPayload.agents || []);
    const nextPolicy = agentPayload.model_policy || { deployment_mode: "external_api", external_data_acknowledgement_required: true, private_evidence_allowed: false };
    setModelPolicy(nextPolicy);
    if (!nextPolicy.private_evidence_allowed) setIncludeAttachments(false);
    setRuns(runPayload || []);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await load();
      } catch (error) {
        if (!cancelled) onNotice(error.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId]);

  const activeIds = useMemo(
    () => runs.filter((item) => !TERMINAL.has(item.status)).map((item) => item.id),
    [runs],
  );

  useEffect(() => {
    if (!activeIds.length) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const updates = await Promise.all(activeIds.map((id) => request(`/api/agent-workflows/${id}?project_id=${encodeURIComponent(projectId)}`)));
        setRuns((current) => current.map((item) => updates.find((update) => update.id === item.id) || item));
      } catch (error) {
        onNotice(error.message);
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [activeIds.join("|")]);

  function toggleAgent(code) {
    setSelected((current) => current.includes(code)
      ? current.filter((item) => item !== code)
      : [...current, code]);
  }

  async function submit(event) {
    event.preventDefault();
    if (!content.trim() || submitting || !projectId) return;
    setSubmitting(true);
    try {
      const created = await request("/api/agent-workflows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: content.trim(),
          agent_codes: selected,
          project_id: projectId,
          research_session_id: sessionId || null,
          attachment_ids: includeAttachments ? attachmentIds : [],
          external_data_acknowledged: false,
          idempotency_key: `web-${window.crypto.randomUUID()}`,
        }),
      });
      setRuns((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setContent("");
      onNotice(selected.length
        ? "多智能体任务已提交，系统将按依赖关系执行并保存每个子智能体产物。"
        : "任务已提交，总控智能体将根据问题自动选择最小必要的子智能体。"
      );
    } catch (error) {
      onNotice(error.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelRun(runId) {
    try {
      const cancelled = await request(`/api/agent-workflows/${runId}/cancel?project_id=${encodeURIComponent(projectId)}`, {
        method: "POST",
      });
      setRuns((current) => current.map((item) => item.id === runId ? cancelled : item));
      onNotice("已提交取消请求；正在执行的节点会在安全边界停止。", "success");
    } catch (error) {
      onNotice(error.message);
    }
  }

  if (loading) {
    return <section className="agent-matrix-loading"><LoaderCircle className="spin" size={22} />正在读取机构智能体能力</section>;
  }

  return <section className="agent-matrix-workspace">
    <header className="agent-matrix-hero">
      <div><p>隆耘 Agent · 可审计多智能体编排</p><h1>农业科研智能体矩阵</h1><span>每个任务绑定当前机构与账号；子智能体产物分别留痕，最终答复由总控智能体汇总。</span></div>
      <div className="agent-matrix-security"><ShieldCheck size={18} /><span>机构隔离<br /><small>个人任务默认私有</small></span></div>
    </header>

    <div className="agent-catalog-grid">
      {catalog.map((agent) => <button
        key={agent.code}
        type="button"
        className={`agent-catalog-card ${selected.includes(agent.code) ? "selected" : ""}`}
        onClick={() => toggleAgent(agent.code)}
      >
        <span className="agent-catalog-icon"><Bot size={20} /></span>
        <span><strong>{agent.name}</strong><small>v{agent.version}</small></span>
        <p>{agent.description}</p>
        <div>{(agent.capabilities || []).map((capability) => <em key={capability}>{capability}</em>)}</div>
        {selected.includes(agent.code) && <CheckCircle2 className="agent-selected-check" size={19} />}
      </button>)}
    </div>

    <form className="agent-task-composer" onSubmit={submit}>
      <div className="agent-task-mode"><Sparkles size={17} /><span>{selected.length ? `已指定 ${selected.length} 个子智能体` : "自动编排：按问题选择最小必要智能体"}</span></div>
      <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="描述科研任务、决策目标、已有数据和希望得到的产物……" />
      <footer>
        <label><input type="checkbox" checked={includeAttachments} onChange={(event) => setIncludeAttachments(event.target.checked)} disabled={!attachmentIds.length || !modelPolicy.private_evidence_allowed} />使用当前会话待发送的已解析附件（{attachmentIds.length}）</label>
        <button type="submit" disabled={!projectId || !content.trim() || submitting}><Play size={16} />{submitting ? "正在提交" : "启动分析"}</button>
      </footer>
    </form>

    <section className="agent-run-list">
      <header><div><h2>任务与产物</h2><span>任务在后台执行，离开页面不会中断。</span></div><button type="button" onClick={() => load().catch((error) => onNotice(error.message))}><RefreshCw size={15} />刷新</button></header>
      {!runs.length && <div className="agent-run-empty"><Bot size={26} /><strong>尚未提交多智能体任务</strong><span>可让系统自动路由，也可以明确指定需要协作的子智能体。</span></div>}
      {runs.map((run) => <article className={`agent-run-card ${run.status}`} key={run.id}>
        <header><div><span className="agent-run-status">{run.status === "running" ? <LoaderCircle className="spin" size={15} /> : <Clock3 size={15} />}{statusLabel(run.status)}</span><small>任务 {run.id.slice(0, 8)} · 尝试 {run.attempt_no || 0}/{run.max_attempts || 3}</small></div><div className="agent-run-actions"><time>{run.created_at ? new Date(run.created_at).toLocaleString() : ""}</time>{!TERMINAL.has(run.status) && <button type="button" onClick={() => cancelRun(run.id)}><XCircle size={14} />取消</button>}</div></header>
        <h3>{run.user_request}</h3>
        <div className="agent-run-route">{(run.plan?.length ? run.plan : run.requested_agents || []).map((code) => {
          const agent = catalog.find((item) => item.code === code);
          return <span key={code}>{agent?.name || code}</span>;
        })}</div>
        {run.error_detail && <div className="agent-run-error">{run.error_detail}</div>}
        {run.final_content && <div className="agent-final-answer"><ReactMarkdown remarkPlugins={[remarkGfm]}>{run.final_content}</ReactMarkdown></div>}
        {!!run.steps?.length && <div className="agent-step-statuses">{run.steps.map((step) => <span className={step.status} key={`${step.agent_code}-${step.attempt_no}`}>{catalog.find((item) => item.code === step.agent_code)?.name || step.agent_code} · {statusLabel(step.status)}</span>)}</div>}
        {!!run.artifacts?.length && <details><summary>查看 {run.artifacts.length} 个子智能体独立产物</summary>{run.artifacts.map((artifact) => <section key={artifact.id}><strong>{artifact.agent_name} <small>v{artifact.agent_version}</small></strong><ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content}</ReactMarkdown></section>)}</details>}
      </article>)}
    </section>
  </section>;
}
