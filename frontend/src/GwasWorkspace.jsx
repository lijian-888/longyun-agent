import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Download,
  FileArchive,
  FileSpreadsheet,
  FlaskConical,
  Link2,
  LoaderCircle,
  Play,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { authorizedFetch, request } from "./api";

const statusLabel = {
  collecting: "待补齐输入",
  confirmed: "计划已确认",
  running: "本地正在运行",
  completed: "分析已完成",
  failed: "分析未完成",
};

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

function FileState({ title, note, ready, children }) {
  return <article className={`gwas-input-card ${ready ? "ready" : ""}`}>
    <div className="gwas-input-card-head">
      <span className="gwas-input-icon">{ready ? <CheckCircle2 size={18} /> : <CircleDot size={18} />}</span>
      <div><strong>{title}</strong><small>{note}</small></div>
    </div>
    {children}
  </article>;
}

function InputSummary({ manifest, preflight }) {
  const genotype = manifest?.genotype;
  const phenotype = manifest?.phenotype;
  const covariates = manifest?.covariates;
  return <section className="gwas-summary-grid">
    <article>
      <span>基因型版本</span>
      <strong>{genotype ? `${genotype.sample_count} 样本` : "待选择"}</strong>
      <small>{genotype?.source_archive_name || "请选择已发布的质控版本"}</small>
    </article>
    <article>
      <span>表型交集</span>
      <strong>{phenotype ? `${phenotype.matched_sample_count} 份` : "待上传"}</strong>
      <small>{phenotype ? `${phenotype.trait_column} · 环境：${phenotype.analysis_environment}` : "需使用系统专用表型模板"}</small>
    </article>
    <article>
      <span>协变量</span>
      <strong>{covariates ? "已提供" : "使用 PCA"}</strong>
      <small>{covariates ? `${Math.max(0, covariates.headers.length - 2)} 个字段` : "默认从基因型计算群体结构 PC"}</small>
    </article>
    <article>
      <span>预检状态</span>
      <strong>{preflight?.status === "ready" ? "可确认" : "待补齐"}</strong>
      <small>{preflight?.matched_sample_count ? `有效交集 ${preflight.matched_sample_count}` : "尚未形成有效样本交集"}</small>
    </article>
  </section>;
}

function ResultFiles({ plan, onNotice }) {
  const files = plan?.result_manifest?.files || [];
  if (!files.length) return null;
  return <section className="gwas-result-files">
    <div>
      <p>已完成的本地结果</p>
      <h4>下载与复核</h4>
      <span>结果由已锁定的质控基因型版本、表型、协变量和固定参数产生。</span>
    </div>
    <div className="gwas-result-file-list">
      {files.map((file) => <button type="button" key={file.key} onClick={() => downloadProtected(`/api/gwas/plans/${plan.id}/results/${encodeURIComponent(file.key)}`, file.file_name, onNotice)}>
        <Download size={15} /><span><strong>{file.title}</strong><small>{file.file_name}</small></span>
      </button>)}
    </div>
  </section>;
}

export default function GwasWorkspace({ onNotice }) {
  const [plans, setPlans] = useState([]);
  const [assets, setAssets] = useState([]);
  const [activePlanId, setActivePlanId] = useState("");
  const [selectedAsset, setSelectedAsset] = useState("");
  const [traitName, setTraitName] = useState("株高");
  const [assembly, setAssembly] = useState("IRGSP-1.0");
  const [traitColumn, setTraitColumn] = useState("trait_value");
  const [busy, setBusy] = useState("");
  const phenotypeInput = useRef(null);
  const covariateInput = useRef(null);
  const legacyGenotypeInput = useRef(null);

  const activePlan = plans.find((item) => item.id === activePlanId) || plans[0] || null;
  const manifest = activePlan?.input_manifest || {};
  const preflight = activePlan?.preflight || {};
  const canConfirm = activePlan?.status === "collecting" && preflight.status === "ready";
  const attachedAssetKey = manifest.genotype?.source_asset_id && manifest.genotype?.source_version_id
    ? `${manifest.genotype.source_asset_id}:${manifest.genotype.source_version_id}`
    : "";

  async function load(preferredId = "") {
    try {
      const [planRows, assetRows] = await Promise.all([
        request("/api/gwas/plans"),
        request("/api/genotype-assets/analysis-ready"),
      ]);
      setPlans(planRows);
      setAssets(assetRows);
      setActivePlanId((current) => preferredId || current || planRows[0]?.id || "");
      setSelectedAsset((current) => {
        if (current) return current;
        const firstAsset = assetRows[0];
        return firstAsset ? `${firstAsset.asset_id}:${firstAsset.id}` : "";
      });
    } catch (error) {
      onNotice(error.message);
    }
  }

  useEffect(() => { void load(); }, []);

  async function createPlan(event) {
    event.preventDefault();
    setBusy("create");
    try {
      const result = await request("/api/gwas/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trait_name: traitName, reference_assembly: assembly }),
      });
      await load(result.id);
      onNotice("已创建连续性状 GWAS 计划。请依次选择质控版本、下载表型模板并上传填写后的表型文件。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function attachAsset() {
    if (!activePlan || !selectedAsset) {
      onNotice("请先选择一个已发布、可用于分析的基因型版本。");
      return;
    }
    const [assetId, versionId] = selectedAsset.split(":");
    setBusy("attach");
    try {
      const result = await request(`/api/gwas/plans/${activePlan.id}/genotype-asset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_id: assetId, version_id: versionId }),
      });
      setPlans((rows) => rows.map((item) => item.id === result.id ? result : item));
      onNotice("已绑定不可变的质控基因型版本。平台只在服务器内部复制 PLINK 文件，不会向浏览器暴露原始基因型。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function downloadPhenotypeTemplate() {
    const target = attachedAssetKey || selectedAsset;
    if (!target) {
      onNotice("请先选择已发布的质控版本。");
      return;
    }
    const [assetId, versionId] = target.split(":");
    await downloadProtected(
      `/api/genotype-assets/${assetId}/versions/${versionId}/phenotype-template`,
      "水稻GWAS专用表型模板.xlsx",
      onNotice,
    );
  }

  async function upload(kind, event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !activePlan) return;
    setBusy(kind);
    try {
      const form = new FormData();
      form.append("file", file);
      const suffix = kind === "phenotype" ? `?trait_column=${encodeURIComponent(traitColumn.trim())}` : "";
      const result = await request(`/api/gwas/plans/${activePlan.id}/${kind}${suffix}`, { method: "POST", body: form });
      setPlans((rows) => rows.map((item) => item.id === result.id ? result : item));
      onNotice(kind === "phenotype"
        ? "表型文件已完成样本交集和单环境校验。请核对预检状态后确认计划。"
        : kind === "covariates"
          ? "协变量已保存；它会与默认 PCA 一起进入固定模型。"
          : "旧计划兼容基因型包已校验。新计划建议回到基因型导入与质控工作台发布版本后再使用。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function confirmPlan() {
    if (!activePlan) return;
    setBusy("confirm");
    try {
      const result = await request(`/api/gwas/plans/${activePlan.id}/confirm`, { method: "POST" });
      setPlans((rows) => rows.map((item) => item.id === result.id ? result : item));
      onNotice("计划已锁定。下一步会使用固定输入快照和参数执行本地受控分析。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function queuePlan() {
    if (!activePlan) return;
    setBusy("run");
    try {
      const result = await request(`/api/gwas/plans/${activePlan.id}/run`, { method: "POST" });
      setPlans((rows) => rows.map((item) => item.id === result.id ? result : item));
      onNotice("已提交本地受控 GWAS 队列，结果将保存在计划与结果库中。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  async function archivePlan() {
    if (!activePlan) return;
    setBusy("archive");
    try {
      const result = await request(`/api/gwas/plans/${activePlan.id}/archive`, { method: "POST" });
      setPlans((rows) => rows.map((item) => item.id === result.id ? result : item));
      onNotice("已将可追溯结果摘要保存到结果库。");
    } catch (error) {
      onNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  return <div className="gwas-workspace">
    <section className="gwas-hero">
      <div>
        <p>平台内置可追溯分析技能</p>
        <h2>水稻连续性状 GWAS</h2>
        <span>从已发布的基因型质控版本出发，结合单一明确分析环境下的连续性状表型，完成受控关联分析。原始数据、样本映射、参数和结果均可追溯。</span>
      </div>
      <div className="gwas-hero-note"><ShieldCheck size={18} /><span>AI 只协助解释与引导；计算由本地固定流程执行，不会生成或运行任意 Shell 命令。</span></div>
    </section>

    <section className="gwas-flow" aria-label="GWAS 引导流程">
      {[["1", "新建计划"], ["2", "选择质控版本"], ["3", "填报表型"], ["4", "确认运行"]].map(([number, label], index) => <div key={number} className={index === 0 || activePlan ? "active" : ""}><b>{number}</b><span>{label}</span>{index < 3 && <ChevronRight size={17} />}</div>)}
    </section>

    <section className="gwas-layout">
      <aside className="gwas-plan-list">
        <div className="gwas-plan-list-title"><strong>我的分析计划</strong><span>{plans.length} 个</span></div>
        {!plans.length && <div className="gwas-plan-empty"><FlaskConical size={22} /><span>先创建一个连续性状 GWAS 计划。</span></div>}
        {plans.map((plan) => <button type="button" key={plan.id} onClick={() => setActivePlanId(plan.id)} className={plan.id === activePlan?.id ? "active" : ""}><FlaskConical size={16} /><span><strong>{plan.trait_name}</strong><small>{plan.reference_assembly}</small></span><em>{statusLabel[plan.status] || plan.status}</em></button>)}
        <form className="gwas-create-form" onSubmit={createPlan}>
          <strong>新建计划</strong>
          <label>连续性状<input value={traitName} onChange={(event) => setTraitName(event.target.value)} required placeholder="例如：株高" /></label>
          <label>参考基因组/坐标版本<input value={assembly} onChange={(event) => setAssembly(event.target.value)} required /></label>
          <button className="secondary-button" disabled={busy === "create"}>{busy === "create" ? <LoaderCircle size={15} className="spin" /> : <FlaskConical size={15} />}生成确认计划</button>
        </form>
      </aside>

      <section className="gwas-plan-detail">
        {!activePlan ? <div className="gwas-detail-empty"><FileArchive size={30} /><h3>从一个明确的科研问题开始</h3><p>例如：在已发布的水稻材料群体中，寻找与株高相关的候选 SNP 位点。</p></div> : <>
          <header className="gwas-detail-head"><div><p>固定工作流 · {activePlan.workflow_code}</p><h3>{activePlan.trait_name} GWAS</h3><span>{activePlan.purpose}</span></div><span className={`gwas-status ${activePlan.status}`}>{statusLabel[activePlan.status] || activePlan.status}</span></header>
          <InputSummary manifest={manifest} preflight={preflight} />

          {activePlan.status === "collecting" && <>
            <section className="gwas-guidance"><Link2 size={19} /><div><strong>先选择已发布的质控版本</strong><span>每个版本已固化原始文件哈希、QC 参数、样本到材料映射和参考版本。GWAS 只读取该版本的内部 PLINK 副本，避免再次上传和混用文件。</span></div></section>
            <div className="gwas-input-grid gwas-input-grid-genotype">
              <FileState title="1. 已发布的基因型质控版本" note={manifest.genotype ? `${manifest.genotype.source_archive_name} · 已在本计划内部绑定` : "必填 · 请先在“基因型导入与质控”完成映射和发布"} ready={Boolean(manifest.genotype)}>
                <select value={attachedAssetKey || selectedAsset} disabled={Boolean(manifest.genotype) || busy === "attach"} onChange={(event) => setSelectedAsset(event.target.value)}>
                  {!assets.length && <option value="">暂无可用版本，请先完成基因型质控与发布</option>}
                  {assets.map((asset) => <option key={asset.id} value={`${asset.asset_id}:${asset.id}`}>{asset.title} · v{asset.version_number} · {asset.qc_summary?.qc_sample_count || 0} 样本 · {asset.reference_assembly}</option>)}
                </select>
                {!manifest.genotype && <button type="button" className="primary-button" onClick={attachAsset} disabled={!selectedAsset || busy === "attach"}>{busy === "attach" ? <LoaderCircle size={15} className="spin" /> : <Link2 size={15} />}绑定到本计划</button>}
                {manifest.genotype && <small className="gwas-source-lock"><ShieldCheck size={13} /> 已锁定当前版本；如需更换，请新建分析计划，避免输入快照被改写。</small>}
              </FileState>
              <FileState title="2. 单环境连续性状表型" note={manifest.phenotype ? `${manifest.phenotype.source_file_name} · ${manifest.phenotype.matched_sample_count} 个有效匹配样本` : "必填 · 先下载系统预填 FID/IID 的专用模板"} ready={Boolean(manifest.phenotype)}>
                <div className="gwas-template-actions">
                  <button type="button" className="secondary-button" onClick={downloadPhenotypeTemplate} disabled={!attachedAssetKey && !selectedAsset}><Download size={15} />下载专用表型模板</button>
                  <label className="gwas-inline-field">性状列名<input value={traitColumn} onChange={(event) => setTraitColumn(event.target.value)} disabled={busy === "phenotype"} /></label>
                </div>
                <small>模板中已预填 FID、IID、材料编码和名称；只需填写一个明确分析环境与 {traitColumn || "trait_value"}。首版不接受多环境或重复行，请先计算 BLUP 或交由区域试验资料包治理。</small>
                <button type="button" className="primary-button" onClick={() => phenotypeInput.current?.click()} disabled={!manifest.genotype || busy === "phenotype"}>{busy === "phenotype" ? <LoaderCircle size={15} className="spin" /> : <FileSpreadsheet size={15} />}{manifest.phenotype ? "替换表型文件" : "上传已填写模板"}</button>
                <input ref={phenotypeInput} hidden type="file" accept=".csv,.tsv,.txt,.xlsx" onChange={(event) => upload("phenotype", event)} />
              </FileState>
              <FileState title="3. 协变量（可选）" note={manifest.covariates ? `${manifest.covariates.source_file_name} · 已纳入控制` : "不提供时系统仍会使用 PCA 校正群体结构"} ready={Boolean(manifest.covariates)}>
                <button type="button" className="secondary-button" onClick={() => covariateInput.current?.click()} disabled={!manifest.genotype || busy === "covariates"}>{busy === "covariates" ? <LoaderCircle size={15} className="spin" /> : <Upload size={15} />}{manifest.covariates ? "替换协变量" : "上传协变量"}</button>
                <input ref={covariateInput} hidden type="file" accept=".csv,.tsv,.txt,.xlsx" onChange={(event) => upload("covariates", event)} />
              </FileState>
            </div>

            <details className="gwas-legacy-path">
              <summary>历史兼容：直接上传已有 PLINK 三件套 ZIP</summary>
              <p>仅用于已创建的旧计划。新项目请先通过“基因型导入与质控”完成格式统一、质控、样本映射和版本发布。</p>
              <button type="button" className="secondary-button" onClick={() => legacyGenotypeInput.current?.click()} disabled={Boolean(manifest.genotype) || busy === "genotype"}><Upload size={15} />上传历史 PLINK ZIP</button>
              <input ref={legacyGenotypeInput} hidden type="file" accept=".zip" onChange={(event) => upload("genotype", event)} />
            </details>
          </>}

          {preflight.warnings?.length > 0 && <section className="gwas-warnings"><AlertTriangle size={17} /><div><strong>需要科研人员审阅</strong>{preflight.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div></section>}

          <section className="gwas-confirmation-card">
            <div><p>执行计划</p><h4>Rice GWAS LMM v1</h4><span>质控：MAF ≥ {activePlan.parameters.maf}；SNP/样本缺失率 ≤ {activePlan.parameters.snp_missing_rate}；LD 筛选后计算 {activePlan.parameters.principal_components} 个 PC；使用混合线性模型；显著位点上下游 {activePlan.parameters.candidate_window_kb} kb 查询候选基因。</span></div>
            <div className="gwas-confirmation-actions">
              {activePlan.status === "collecting" && <button type="button" className="primary-button" onClick={confirmPlan} disabled={!canConfirm || busy === "confirm"}>{busy === "confirm" ? <LoaderCircle size={16} className="spin" /> : <CheckCircle2 size={16} />}{canConfirm ? "确认并锁定计划" : "补齐输入后可确认"}</button>}
              {activePlan.status === "confirmed" && <button type="button" className="primary-button" onClick={queuePlan} disabled={busy === "run"}>{busy === "run" ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}提交受控运行</button>}
              {activePlan.status === "running" && <span className="gwas-queued"><LoaderCircle size={16} className="spin" />本地正在执行固定 GWAS 流程</span>}
              {activePlan.status === "completed" && <><span className="gwas-completed"><CheckCircle2 size={16} />核心输出已生成</span><button type="button" className="secondary-button" onClick={archivePlan} disabled={busy === "archive"}>{busy === "archive" ? <LoaderCircle size={15} className="spin" /> : <FileArchive size={15} />}保存到结果库</button></>}
              {activePlan.status === "failed" && <span className="gwas-failed"><AlertTriangle size={16} />{activePlan.confirmation?.execution_error || "请查看输入和运行环境。"}</span>}
              <small>确认后会记录输入文件校验值、来源基因型版本、参考版本、固定参数、确认人与时间。</small>
            </div>
          </section>
          <ResultFiles plan={activePlan} onNotice={onNotice} />
        </>}
      </section>
    </section>
  </div>;
}
