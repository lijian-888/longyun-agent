import { useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  Clock3,
  Dna,
  FlaskConical,
  Image,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import { request } from "./api";

const iconBySkill = {
  regional_trial_statistics: BarChart3,
  continuous_trait_gwas: FlaskConical,
  field_image_phenotyping: Image,
  genotype_import_qc: Dna,
};

function formatTime(value) {
  if (!value) return "尚未打开";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "已记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function SkillCard({ skill, busy, onOpen, onNotice }) {
  const Icon = iconBySkill[skill.code] || Activity;
  const available = skill.status === "available";

  return <article className={`skill-card ${available ? "available" : "planned"}`}>
    <header className="skill-card-head">
      <span className="skill-card-icon"><Icon size={20} /></span>
      <div>
        <span className="skill-card-category">{skill.category}</span>
        <h4>{skill.name}</h4>
        <span className={`skill-status ${skill.status}`}>{skill.status_label}</span>
      </div>
    </header>
    <p className="skill-card-summary">{skill.summary}</p>
    <div className="skill-card-detail">
      <section>
        <strong>受控输入</strong>
        <ul>{skill.inputs.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>
      <section>
        <strong>可交付结果</strong>
        <ul>{skill.outputs.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>
    </div>
    <section className="skill-traceability">
      <strong>追溯与审计</strong>
      <ul>{skill.traceability.map((item) => <li key={item}>{item}</li>)}</ul>
    </section>
    <footer className="skill-card-footer">
      <small>最近打开：{formatTime(skill.last_opened_at)}</small>
      {available ? <button type="button" className="primary-button" disabled={busy} onClick={() => onOpen(skill)}>
        {busy ? <LoaderCircle size={15} className="spin" /> : <ArrowUpRight size={15} />}打开技能
      </button> : <button type="button" className="secondary-button" onClick={() => onNotice("田间图像表型提取尚未接入本地 PlantCV 服务；当前仅保留受控数据、算法版本和人工核验的设计边界。")}>查看接入边界</button>}
    </footer>
  </article>;
}

export default function SkillLibrary({ onNotice, onOpenWorkspace }) {
  const [catalog, setCatalog] = useState({ skills: [], recent_runs: [] });
  const [loading, setLoading] = useState(true);
  const [busyCode, setBusyCode] = useState("");

  async function load() {
    setLoading(true);
    try {
      setCatalog(await request("/api/research/skills"));
    } catch (error) {
      onNotice(error.message || "无法读取科研技能库。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function openSkill(skill) {
    setBusyCode(skill.code);
    try {
      const result = await request(`/api/research/skills/${skill.code}/launch`, { method: "POST" });
      setCatalog((current) => ({
        ...current,
        skills: current.skills.map((item) => item.code === skill.code ? result.skill : item),
        recent_runs: [
          { ...result.audit, skill_code: skill.code, skill_name: skill.name, workspace: skill.workspace },
          ...current.recent_runs,
        ].slice(0, 30),
      }));
      onOpenWorkspace(result.skill);
    } catch (error) {
      onNotice(error.message || "无法打开该科研技能。");
    } finally {
      setBusyCode("");
    }
  }

  return <section className="skill-library" aria-label="科研技能库">
    <header className="skill-library-header">
      <div>
        <p>受控科研能力目录</p>
        <h2>技能库</h2>
        <span>将平台可运行的科研能力封装为明确输入、固定输出和可追溯证据链，不以大模型文字替代统计或生信计算。</span>
      </div>
      <button className="skill-library-refresh" title="刷新技能记录" type="button" onClick={() => void load()} disabled={loading}>
        {loading ? <LoaderCircle size={16} className="spin" /> : <Activity size={16} />}
      </button>
    </header>

    <section className="skill-library-boundary"><ShieldCheck size={17} /><span>“已接入”技能仅在完成输入校验或使用已发布数据后运行；每次打开都会按当前账号写入审计记录，实际分析另保留其资料包、计划或运行编号。</span></section>

    {loading ? <div className="skill-library-loading"><LoaderCircle size={22} className="spin" />正在读取技能目录</div> : <>
      <div className="skill-library-section-title"><h3>平台技能</h3><span>共 {catalog.skills.length} 项，其中 {catalog.skills.filter((skill) => skill.status === "available").length} 项可直接使用</span></div>
      <div className="skill-card-grid">
        {catalog.skills.map((skill) => <SkillCard key={skill.code} skill={skill} busy={busyCode === skill.code} onOpen={openSkill} onNotice={onNotice} />)}
      </div>
      <section className="skill-audit-panel">
        <div className="skill-audit-header"><CheckCircle2 size={17} /><h3>近期技能操作</h3><span>仅显示当前科研账号的审计记录</span></div>
        {catalog.recent_runs?.length ? <div className="skill-audit-list">{catalog.recent_runs.slice(0, 6).map((item) => <article key={item.id}>
          <strong>{item.skill_name}</strong>
          <span>{formatTime(item.created_at)} · {item.workspace === "assistant" ? "隆耘 Agent 育种智能体" : item.workspace === "genotype" ? "基因型导入与质控" : "水稻 GWAS"}</span>
        </article>)}</div> : <p className="skill-audit-empty">尚未打开技能。打开已接入技能后，这里会记录当前账号的操作时间与目标工作台。</p>}
      </section>
    </>}
  </section>;
}
