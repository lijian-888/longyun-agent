import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Award,
  BarChart3,
  Building2,
  Camera,
  CheckCircle2,
  ChevronRight,
  Clock3,
  CloudRain,
  Dna,
  Droplets,
  Expand,
  FlaskConical,
  GitCompareArrows,
  Leaf,
  LoaderCircle,
  MapPin,
  Medal,
  RefreshCw,
  Radio,
  RotateCcw,
  ScanLine,
  ShieldCheck,
  Sprout,
  Target,
  ThermometerSun,
  Trophy,
  Users,
  Wifi,
  Wind,
  X,
} from "lucide-react";
import {
  Legend,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { request } from "./api";

const CHART_COLORS = ["#1f7a5b", "#d89b2b", "#2f78a8", "#9a5eaa"];
const STATUS_LABELS = {
  candidate: "候选",
  retained: "保留",
  observed: "继续观察",
  eliminated: "淘汰",
  promoted: "晋级",
};

const BASE_MEDIA = [
  {
    id: "field-panorama",
    src: "/base-dashboard/field-panorama.jpg",
    title: "抚州试验点 · 东区定点相机",
    detail: "2026-08-05 10:41 · RGB全景 · FZ-CAM-01",
    alt: "带有试验小区、灌溉沟渠和气象站的水稻育种基地全景",
  },
  {
    id: "single-plant-flowering",
    src: "/base-dashboard/single-plant-flowering.jpg",
    title: "单株 FZ-2026-A07-018",
    detail: "抽穗扬花期 · 正面标准照 · AI质量 98%",
    alt: "育种试验田中处于抽穗扬花期的带标识水稻单株",
  },
  {
    id: "plant-lifecycle",
    src: "/base-dashboard/plant-lifecycle.jpg",
    title: "同株关键生育期影像",
    detail: "分蘖 → 孕穗 → 抽穗扬花 → 灌浆",
    alt: "同一水稻单株从分蘖到灌浆的四阶段连续影像",
  },
];

const BASE_ENVIRONMENT = [
  { label: "空气温度", value: "24.8", unit: "℃", icon: ThermometerSun, note: "适宜" },
  { label: "空气湿度", value: "82", unit: "%RH", icon: CloudRain, note: "+3%" },
  { label: "土壤含水", value: "31.6", unit: "%", icon: Droplets, note: "20 cm" },
  { label: "田间水层", value: "3.8", unit: "cm", icon: Activity, note: "稳定" },
  { label: "光照强度", value: "41.2", unit: "klux", icon: Radio, note: "上升" },
  { label: "平均风速", value: "1.9", unit: "m/s", icon: Wind, note: "东南风" },
];

const BASE_DEVICES = [
  { label: "气象站", value: "2 / 2", status: "正常" },
  { label: "土壤探针", value: "32 / 34", status: "2台待检" },
  { label: "定点相机", value: "12 / 12", status: "正常" },
  { label: "水泵与阀门", value: "23 / 24", status: "1台离线" },
];

const BASE_PHENOLOGY = [
  { label: "分蘖期", value: 8 },
  { label: "拔节期", value: 16 },
  { label: "孕穗期", value: 29 },
  { label: "抽穗扬花", value: 34 },
  { label: "灌浆期", value: 13 },
];

const BASE_ALERTS = [
  { level: "warning", place: "抚州 · A07", text: "土壤含水率连续30分钟偏低", time: "10:36" },
  { level: "notice", place: "抚州 · A12", text: "抽穗进度晚于同处理均值2.1天", time: "09:58" },
  { level: "warning", place: "赣州 · 阀门24", text: "灌溉控制器离线，已转人工巡检", time: "09:21" },
];

function BaseMonitoringOverview({ onOpenMedia }) {
  return <div className="base-monitoring-overview">
    <section className="base-monitor-card base-live-media">
      <header><div><span><Camera size={14} />田间实时影像</span><small>演示接入数据</small></div><b><span className="live-dot" />10:41 已更新</b></header>
      <button type="button" className="base-main-photo" onClick={() => onOpenMedia(BASE_MEDIA[0])} aria-label="查看基地全景大图">
        <img src={BASE_MEDIA[0].src} alt={BASE_MEDIA[0].alt} />
        <span><strong>{BASE_MEDIA[0].title}</strong><small>{BASE_MEDIA[0].detail}</small></span>
      </button>
      <div className="base-photo-pair">
        {BASE_MEDIA.slice(1).map((media) => <button type="button" key={media.id} onClick={() => onOpenMedia(media)}>
          <img src={media.src} alt={media.alt} /><span><strong>{media.title}</strong><small>{media.detail}</small></span>
        </button>)}
      </div>
    </section>

    <section className="base-monitor-card base-environment-card">
      <header><div><span><ThermometerSun size={14} />环境与水分</span><small>多源传感器</small></div><b><Wifi size={13} />在线</b></header>
      <div className="base-environment-grid">{BASE_ENVIRONMENT.map(({ label, value, unit, icon: Icon, note }) => <article key={label}>
        <Icon size={15} /><span>{label}</span><strong>{value}<small>{unit}</small></strong><em>{note}</em>
      </article>)}</div>
      <div className="base-device-list"><div className="base-card-subtitle"><span>设备接入状态</span><small>69 / 72 在线</small></div>{BASE_DEVICES.map((device) => <div key={device.label}><span>{device.label}</span><strong>{device.value}</strong><em className={device.status.includes("正常") ? "good" : "warn"}>{device.status}</em></div>)}</div>
    </section>

    <section className="base-monitor-card base-operations-card">
      <header><div><span><BarChart3 size={14} />生育与运行</span><small>全基地汇总</small></div><b><Clock3 size={13} />10:42</b></header>
      <div className="base-phenology-bars"><div className="base-card-subtitle"><span>当前生育期分布</span><small>960 个观测单元</small></div>{BASE_PHENOLOGY.map((stage) => <div key={stage.label}><span>{stage.label}</span><i><b style={{ width: `${stage.value * 2.5}%` }} /></i><strong>{stage.value}%</strong></div>)}</div>
      <div className="base-alert-list"><div className="base-card-subtitle"><span>需要关注</span><small>3项</small></div>{BASE_ALERTS.map((alert) => <article key={`${alert.place}-${alert.time}`} className={alert.level}><AlertTriangle size={14} /><div><strong>{alert.place}</strong><span>{alert.text}</span></div><time>{alert.time}</time></article>)}</div>
      <div className="base-quality-strip"><div><span>调查完成率</span><strong>92.4%</strong></div><div><span>图片覆盖率</span><strong>86.7%</strong></div><div><span>质控通过率</span><strong>98.2%</strong></div></div>
    </section>
  </div>;
}

function number(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function WorkspaceLoading({ label }) {
  return <div className="decision-loading"><LoaderCircle className="spin" size={24} />{label}</div>;
}

function WorkspaceEmpty({ title, detail, onReload }) {
  return <section className="decision-empty">
    <Sprout size={34} />
    <h2>{title}</h2>
    <p>{detail}</p>
    <button className="secondary-button" type="button" onClick={onReload}><RefreshCw size={15} />重新读取</button>
  </section>;
}

function SummaryCard({ icon: Icon, label, value, note, tone = "green" }) {
  return <article className={`decision-summary-card ${tone}`}>
    <span className="decision-summary-icon"><Icon size={18} /></span>
    <div><small>{label}</small><strong>{value}</strong><em>{note}</em></div>
  </article>;
}

function RankBadge({ rank }) {
  const Icon = rank === 1 ? Trophy : rank <= 3 ? Medal : Award;
  return <span className={`variety-rank rank-${Math.min(rank, 4)}`}><Icon size={14} />第 {rank} 名</span>;
}

function ScoreBar({ value }) {
  return <div className="variety-score-bar"><span style={{ width: `${Math.max(0, Math.min(100, value || 0))}%` }} /></div>;
}

export function VarietyEvaluationWorkspace({ onNotice, projectId = "" }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [weights, setWeights] = useState({});
  const [selectedIds, setSelectedIds] = useState([]);

  async function load() {
    if (!projectId) {
      setData(null);
      setLoading(false);
      setError("请先选择课题；品种评价只汇总当前课题的已发布证据。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const result = await request(`/api/single-plants/variety-evaluation?project_id=${encodeURIComponent(projectId)}`);
      setData(result);
      setWeights(Object.fromEntries((result.traits || []).map((trait) => [trait.code, Math.round(trait.weight * 100)])));
      setSelectedIds((current) => {
        const available = new Set((result.varieties || []).map((item) => item.material_id));
        const kept = current.filter((id) => available.has(id));
        return kept.length >= 2 ? kept.slice(0, 4) : (result.varieties || []).slice(0, 3).map((item) => item.material_id);
      });
    } catch (requestError) {
      setError(requestError.message || "无法读取品种评价数据。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [projectId]);

  const rankings = useMemo(() => {
    if (!data) return [];
    return data.varieties.map((variety) => {
      let weighted = 0;
      let availableWeight = 0;
      data.traits.forEach((trait) => {
        const score = variety.trait_scores?.[trait.code];
        const weight = Number(weights[trait.code] || 0);
        if (score === null || score === undefined || weight <= 0) return;
        weighted += Number(score) * weight;
        availableWeight += weight;
      });
      return { ...variety, adjustedScore: availableWeight ? weighted / availableWeight : 0 };
    }).sort((a, b) => b.adjustedScore - a.adjustedScore || a.material_code.localeCompare(b.material_code))
      .map((item, index) => ({ ...item, adjustedRank: index + 1 }));
  }, [data, weights]);

  const selectedVarieties = rankings.filter((item) => selectedIds.includes(item.material_id));
  const radarData = (data?.traits || []).map((trait) => {
    const row = { trait: trait.name, fullMark: 100 };
    selectedVarieties.forEach((variety) => { row[variety.material_id] = variety.trait_scores?.[trait.code] ?? 0; });
    return row;
  });
  const totalWeight = Object.values(weights).reduce((sum, value) => sum + Number(value || 0), 0);

  function toggleComparison(materialId) {
    setSelectedIds((current) => {
      if (current.includes(materialId)) return current.filter((id) => id !== materialId);
      if (current.length >= 4) {
        onNotice?.("一次最多比较 4 个品种，请先取消一个已选品种。");
        return current;
      }
      return [...current, materialId];
    });
  }

  function resetWeights() {
    setWeights(Object.fromEntries((data?.traits || []).map((trait) => [trait.code, Math.round(trait.weight * 100)])));
  }

  if (loading) return <WorkspaceLoading label="正在汇总单株证据并计算品种得分…" />;
  if (error) return <WorkspaceEmpty title="品种评价读取失败" detail={error} onReload={load} />;
  if (!data?.varieties?.length) return <WorkspaceEmpty title="还没有可评价的已发布单株数据" detail="请由数据处理员导入并正式发布完整演示数据包。系统会从单株表型实时形成品种评分，不需要再维护一张评价结果表。" onReload={load} />;

  return <div className="variety-evaluation-workspace">
    <header className="decision-hero">
      <div className="decision-hero-title"><span><BarChart3 size={16} />品种评价决策台</span><h2>从 960 株证据汇总到品种判断</h2><p>单项指标先标准化，再按权重形成综合评分。排名、优势短板和横向比较会随权重调整同步更新。</p></div>
      <div className="decision-hero-proof"><ShieldCheck size={19} /><span><b>结果可追溯</b>每个平均值均可回到基地、小区和单株原始观测。</span></div>
    </header>

    <section className="decision-summary-grid">
      <SummaryCard icon={Leaf} label="参评品种" value={number(data.summary.variety_count)} note={`${data.summary.site_count} 个基地覆盖`} />
      <SummaryCard icon={Users} label="有效单株" value={number(data.summary.plant_count)} note={`${data.summary.trial_count} 个试验`} tone="blue" />
      <SummaryCard icon={ScanLine} label="表型观测" value={number(data.summary.observation_count)} note="仅采用最新有效版本" tone="gold" />
      <SummaryCard icon={AlertTriangle} label="质控提醒" value={number(data.summary.quality_warning_count)} note="保留但明确标记" tone="red" />
    </section>

    <section className="variety-weight-panel">
      <header><div><span>评价口径</span><h3>调整综合评分权重</h3><p>权重总计 {totalWeight}%；系统按当前有效权重自动归一化。</p></div><button type="button" className="secondary-button" onClick={resetWeights}><RotateCcw size={14} />恢复默认</button></header>
      <div className="variety-weight-grid">{data.traits.map((trait) => <label key={trait.code}>
        <span><b>{trait.name}</b><em>{weights[trait.code] || 0}%</em></span>
        <input type="range" min="0" max="40" step="1" value={weights[trait.code] || 0} onChange={(event) => setWeights({ ...weights, [trait.code]: Number(event.target.value) })} />
        <small>{trait.direction === "higher" ? "越高越优" : trait.direction === "lower" ? "越低越优" : `目标值 ${trait.target}${trait.unit}`}</small>
      </label>)}</div>
    </section>

    <div className="variety-main-grid">
      <section className="variety-ranking-panel">
        <header><div><span>综合评价</span><h3>品种排名</h3></div><small>勾选 2–4 个品种进行比较</small></header>
        <div className="variety-ranking-list">{rankings.map((variety) => <article key={variety.material_id} className={selectedIds.includes(variety.material_id) ? "selected" : ""}>
          <label className="variety-compare-check"><input type="checkbox" checked={selectedIds.includes(variety.material_id)} onChange={() => toggleComparison(variety.material_id)} /><GitCompareArrows size={14} /></label>
          <RankBadge rank={variety.adjustedRank} />
          <div className="variety-ranking-name"><strong>{variety.material_name}</strong><span>{variety.material_code} · {variety.plant_count} 株 · {variety.site_count} 个基地</span></div>
          <div className="variety-ranking-score"><b>{number(variety.adjustedScore, 1)}</b><span>综合分</span><ScoreBar value={variety.adjustedScore} /></div>
          <div className="variety-strength"><small>优势</small>{variety.strengths.map((item) => <span key={item.code}><CheckCircle2 size={12} />{item.name} {number(item.score, 0)}</span>)}</div>
          <div className="variety-weakness"><small>短板</small>{variety.weaknesses.map((item) => <span key={item.code}><AlertTriangle size={12} />{item.name} {number(item.score, 0)}</span>)}</div>
        </article>)}</div>
      </section>

      <aside className="variety-insight-panel">
        <header><span>解释口径</span><h3>分数如何形成</h3></header>
        <ol><li><b>单株汇总</b><span>按品种汇总各基地有效单株的最新观测。</span></li><li><b>指标标准化</b><span>产量等正向指标越高越优；倒伏等级反向计分；株高按目标值计分。</span></li><li><b>加权评分</b><span>按当前权重合成 0–100 分并重新排名。</span></li><li><b>优势与短板</b><span>显示标准化得分最高和最低的两个指标。</span></li></ol>
        <div className="variety-stability-callout"><Target size={18} /><div><b>稳定性不是总分替代项</b><span>页面另列多基地单株产量波动，帮助识别“单点很高、跨点不稳”的品种。</span></div></div>
      </aside>
    </div>

    <section className="variety-comparison-panel">
      <header><div><span>横向比较</span><h3>已选 {selectedVarieties.length} 个品种</h3><p>雷达图比较标准化得分，表格保留各指标原始均值。</p></div><GitCompareArrows size={22} /></header>
      {selectedVarieties.length < 2 ? <div className="variety-compare-empty">请从排名中至少勾选两个品种。</div> : <div className="variety-comparison-grid">
        <div className="variety-radar-chart"><ResponsiveContainer width="100%" height="100%"><RadarChart data={radarData} outerRadius="68%"><PolarGrid stroke="#d9e7e0" /><PolarAngleAxis dataKey="trait" tick={{ fill: "#55766b", fontSize: 11 }} /><PolarRadiusAxis angle={22.5} domain={[0, 100]} tick={{ fill: "#81958d", fontSize: 9 }} tickCount={5} /><Tooltip formatter={(value) => number(value, 1)} /><Legend />{selectedVarieties.map((variety, index) => <Radar key={variety.material_id} name={variety.material_name} dataKey={variety.material_id} stroke={CHART_COLORS[index]} fill={CHART_COLORS[index]} fillOpacity={0.1} strokeWidth={2} />)}</RadarChart></ResponsiveContainer></div>
        <div className="variety-comparison-table-wrap"><table><thead><tr><th>指标</th>{selectedVarieties.map((item) => <th key={item.material_id}>{item.material_name}<small>{number(item.adjustedScore, 1)} 分 · 第 {item.adjustedRank} 名</small></th>)}</tr></thead><tbody>
          {data.traits.map((trait) => <tr key={trait.code}><td>{trait.name}<small>{trait.unit}</small></td>{selectedVarieties.map((item) => <td key={item.material_id}><strong>{number(item.trait_averages?.[trait.code], 2)}</strong><span>指标分 {number(item.trait_scores?.[trait.code], 1)}</span></td>)}</tr>)}
          <tr><td>跨基地稳定性<small>单株产量波动</small></td>{selectedVarieties.map((item) => <td key={item.material_id}><strong>{number(item.stability_score, 1)}</strong><span>分数越高越稳定</span></td>)}</tr>
        </tbody></table></div>
      </div>}
    </section>
  </div>;
}

function Breadcrumbs({ depth, site, trial, plot, plant, onNavigate }) {
  const crumbs = [
    { level: "site", label: site?.site_name || "基地" },
    ...(site ? [{ level: "trial", label: trial?.trial_name || "试验" }] : []),
    ...(trial ? [{ level: "plot", label: plot?.plot_no ? `小区 ${plot.plot_no}` : "小区" }] : []),
    ...(plot ? [{ level: "variety", label: plot.material_name || "品种" }] : []),
    ...(depth === "plant" ? [{ level: "plant", label: plant?.sample_code || "单株" }] : []),
  ];
  return <nav className="base-breadcrumb" aria-label="基地数据下钻路径">{crumbs.map((crumb, index) => {
    const current = depth === crumb.level;
    return <span key={`${crumb.level}-${index}`}><button type="button" className={current ? "active" : ""} aria-current={current ? "page" : undefined} disabled={current} onClick={() => onNavigate(crumb.level)}>{crumb.label}</button>{index < crumbs.length - 1 && <ChevronRight size={14} />}</span>;
  })}</nav>;
}

const BACK_DESTINATIONS = {
  trial: { level: "site", label: "返回基地列表" },
  plot: { level: "trial", label: "返回试验列表" },
  variety: { level: "plot", label: "返回小区列表" },
  plant: { level: "variety", label: "返回品种概览" },
};

function BaseNavigation({ depth, site, trial, plot, plant, onNavigate }) {
  const back = BACK_DESTINATIONS[depth];
  return <div className={`base-navigation-row ${back ? "has-back" : ""}`}>
    {back && <button className="base-back-button" type="button" onClick={() => onNavigate(back.level)}><ArrowLeft size={16} /><span>{back.label}</span></button>}
    <div className="base-current-location"><small>当前位置</small><Breadcrumbs depth={depth} site={site} trial={trial} plot={plot} plant={plant} onNavigate={onNavigate} /></div>
  </div>;
}

function BaseLevelGuide({ depth }) {
  const levels = [
    ["site", "1", "基地"], ["trial", "2", "试验"], ["plot", "3", "小区"], ["variety", "4", "品种"], ["plant", "5", "单株"],
  ];
  const current = levels.findIndex(([key]) => key === depth);
  return <div className="base-level-guide">{levels.map(([key, numberValue, label], index) => <div key={key} className={index < current ? "done" : index === current ? "active" : ""}><b>{index < current ? "✓" : numberValue}</b><span>{label}</span>{index < levels.length - 1 && <i />}</div>)}</div>;
}

function LatestPlantTraits({ detail }) {
  const traits = useMemo(() => {
    const result = new Map();
    (detail?.observations || []).forEach((item) => {
      if (!result.has(item.trait_code)) result.set(item.trait_code, item);
    });
    return [...result.values()];
  }, [detail]);
  return <div className="base-plant-traits">{traits.map((trait) => <article key={trait.trait_code}><span>{trait.trait_name}</span><strong>{trait.value_numeric ?? trait.value_text ?? "—"} <small>{trait.unit || ""}</small></strong><em className={trait.quality_status === "warning" ? "warning" : ""}>{trait.quality_status === "warning" ? "需复核" : "有效"}</em></article>)}</div>;
}

export function BaseShowcaseWorkspace({ onNotice, projectId = "" }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [depth, setDepth] = useState("site");
  const [site, setSite] = useState(null);
  const [trial, setTrial] = useState(null);
  const [plot, setPlot] = useState(null);
  const [plotData, setPlotData] = useState(null);
  const [plant, setPlant] = useState(null);
  const [plantDetail, setPlantDetail] = useState(null);
  const [drillLoading, setDrillLoading] = useState(false);
  const [selectedMedia, setSelectedMedia] = useState(null);
  const shellRef = useRef(null);

  async function load() {
    if (!projectId) {
      setData(null);
      setLoading(false);
      setError("请先选择课题；基地展示只允许下钻当前课题的试验和单株。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      setData(await request(`/api/single-plants/base-dashboard?project_id=${encodeURIComponent(projectId)}`));
    } catch (requestError) {
      setError(requestError.message || "无法读取基地展示数据。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [projectId]);

  useEffect(() => {
    if (!selectedMedia) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") setSelectedMedia(null); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedMedia]);

  function chooseSite(value) {
    setSite(value); setTrial(null); setPlot(null); setPlotData(null); setPlant(null); setPlantDetail(null); setDepth("trial");
  }

  function chooseTrial(value) {
    setTrial(value); setPlot(null); setPlotData(null); setPlant(null); setPlantDetail(null); setDepth("plot");
  }

  async function choosePlot(value) {
    setPlot(value); setPlant(null); setPlantDetail(null); setDepth("variety"); setDrillLoading(true);
    try {
      setPlotData(await request(`/api/single-plants/base-dashboard/plots/${value.trial_entry_id}/plants?project_id=${encodeURIComponent(projectId)}`));
    } catch (requestError) {
      onNotice?.(requestError.message || "无法读取小区单株。");
      setPlotData(null);
    } finally {
      setDrillLoading(false);
    }
  }

  async function choosePlant(value) {
    setPlant(value); setDepth("plant"); setDrillLoading(true);
    try {
      setPlantDetail(await request(`/api/single-plants/${value.id}?project_id=${encodeURIComponent(projectId)}`));
    } catch (requestError) {
      onNotice?.(requestError.message || "无法读取单株证据。");
      setPlantDetail(null);
    } finally {
      setDrillLoading(false);
    }
  }

  function navigate(level) {
    if (level === "site") { setDepth("site"); setSite(null); setTrial(null); setPlot(null); setPlotData(null); setPlant(null); setPlantDetail(null); }
    if (level === "trial") { setDepth("trial"); setTrial(null); setPlot(null); setPlotData(null); setPlant(null); setPlantDetail(null); }
    if (level === "plot") { setDepth("plot"); setPlot(null); setPlotData(null); setPlant(null); setPlantDetail(null); }
    if (level === "variety" && plot) { setDepth("variety"); setPlant(null); setPlantDetail(null); }
  }

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await shellRef.current?.requestFullscreen();
    } catch {
      onNotice?.("当前浏览器未允许进入全屏展示。");
    }
  }

  if (loading) return <WorkspaceLoading label="正在连接基地、试验、小区和单株数据…" />;
  if (error) return <WorkspaceEmpty title="基地展示数据读取失败" detail={error} onReload={load} />;
  if (!data?.sites?.length) return <WorkspaceEmpty title="还没有可展示的基地数据" detail="请先发布带有试验小区关联的单株主表和表型观测。发布后即可从基地逐层下钻到单株，无需另建大屏专用数据。" onReload={load} />;

  return <div className="base-showcase" ref={shellRef}>
    <header className="base-showcase-header">
      <div><span><span className="live-dot" />隆耘基地实时数据视窗</span><h2>水稻育种基地展示大屏</h2><p>基地 → 试验 → 小区 → 品种 → 单株，五级证据逐层下钻</p></div>
      <button type="button" onClick={toggleFullscreen}><Expand size={17} />全屏展示</button>
    </header>

    <section className="base-kpi-grid">
      <article><Building2 size={20} /><span>基地</span><strong>{number(data.summary.site_count)}</strong><small>个生产试验现场</small></article>
      <article><FlaskConical size={20} /><span>试验 / 小区</span><strong>{number(data.summary.trial_count)} / {number(data.summary.plot_count)}</strong><small>关联到真实试验条目</small></article>
      <article><Leaf size={20} /><span>品种 / 单株</span><strong>{number(data.summary.material_count)} / {number(data.summary.plant_count)}</strong><small>统一材料与样本主键</small></article>
      <article><ScanLine size={20} /><span>表型观测</span><strong>{number(data.summary.observation_count)}</strong><small>{number(data.summary.quality_warning_count)} 条质控提醒</small></article>
      <article><Radio size={20} /><span>设备在线</span><strong>69 / 72</strong><small>气象、土壤、相机与水肥设施</small></article>
      <article className="attention"><AlertTriangle size={20} /><span>运行告警</span><strong>3</strong><small>1项需要现场巡检</small></article>
    </section>

    <BaseLevelGuide depth={depth} />
    <BaseNavigation depth={depth} site={site} trial={trial} plot={plot} plant={plant} onNavigate={navigate} />

    <main className="base-stage">
      {depth === "site" && <section className="base-stage-content">
        <header><div><span>第 1 层 · 基地运行总览</span><h3>基地状态、现场影像与异常处置</h3><p>结构化科研数据来自已发布数据集；传感器、设备与图片为演示接入数据。</p></div><MapPin size={22} /></header>
        <BaseMonitoringOverview onOpenMedia={setSelectedMedia} />
        <div className="base-site-section-heading"><div><span>基地列表</span><h4>继续下钻到试验与单株证据</h4></div><small>点击基地进入第 2 层</small></div>
        <div className="base-site-grid">{data.sites.map((item) => <button type="button" key={item.site_id} onClick={() => chooseSite(item)}>
          <span className="site-pin"><MapPin size={21} /></span><div><small>{item.site_code} · {item.province || "省份未填"}{item.county ? ` ${item.county}` : ""}</small><h4>{item.site_name}</h4><p>{item.ecological_zone || "生态区待补充"} · {item.soil_type || "土壤类型待补充"}</p></div><dl><div><dt>试验</dt><dd>{item.trial_count}</dd></div><div><dt>小区</dt><dd>{item.plot_count}</dd></div><div><dt>单株</dt><dd>{item.plant_count}</dd></div><div><dt>观测</dt><dd>{item.observation_count}</dd></div></dl><span className="drill-action">进入基地 <ChevronRight size={14} /></span>
        </button>)}</div>
      </section>}

      {depth === "trial" && site && <section className="base-stage-content"><header><div><span>第 2 层 · {site.site_code}</span><h3>{site.site_name}的试验</h3><p>{site.ecological_zone || "生态区待补充"} · {site.soil_type || "土壤类型待补充"} · {site.latitude && site.longitude ? `${site.latitude}, ${site.longitude}` : "坐标待补充"}</p></div><FlaskConical size={22} /></header><div className="base-trial-grid">{site.trials.map((item) => <button type="button" key={item.trial_id} onClick={() => chooseTrial(item)}><span><FlaskConical size={18} />{item.trial_year} 年</span><h4>{item.trial_name}</h4><p>{item.trial_code}</p><dl><div><dt>小区</dt><dd>{item.plot_count}</dd></div><div><dt>品种</dt><dd>{item.material_count}</dd></div><div><dt>单株</dt><dd>{item.plant_count}</dd></div><div><dt>质控提醒</dt><dd className={item.quality_warning_count ? "warning" : ""}>{item.quality_warning_count}</dd></div></dl><em>查看试验小区 <ChevronRight size={14} /></em></button>)}</div></section>}

      {depth === "plot" && trial && <section className="base-stage-content"><header><div><span>第 3 层 · {trial.trial_code}</span><h3>{trial.trial_name}的小区分布</h3><p>点击任一小区，查看种植品种、处理方式和聚合表型。</p></div><span className="base-legend"><i className="good" />数据完整<i className="warn" />有质控提醒</span></header><div className="base-plot-grid">{trial.plots.map((item) => <button type="button" className={item.quality_warning_count ? "warning" : ""} key={item.trial_entry_id} onClick={() => void choosePlot(item)}><span className="plot-number">{item.plot_no}</span><strong>{item.material_name}</strong><small>{item.material_code}</small><div><span>{item.plant_count} 株</span><span>产量 {number(item.trait_averages.yield_per_plant, 1)} g/株</span></div><em>{item.treatment_name}</em></button>)}</div></section>}

      {depth === "variety" && plot && <section className="base-stage-content"><header><div><span>第 4 层 · 小区 {plot.plot_no}</span><h3>{plot.material_name}</h3><p>{plot.material_code} · {plot.treatment_name} · 第 {plot.replicate_no} 次重复</p></div><Leaf size={22} /></header>
        <div className="base-variety-layout"><article className="base-variety-profile"><div className="base-variety-score"><span>小区数据质量</span><strong>{number(plot.quality_score, 0)}</strong><small>/ 100</small></div><dl><div><dt>有效单株</dt><dd>{plot.plant_count}</dd></div><div><dt>表型观测</dt><dd>{plot.observation_count}</dd></div><div><dt>基因型关联</dt><dd>{plot.genotype_count}</dd></div><div><dt>晋级 / 保留</dt><dd>{plot.promoted_count} / {plot.retained_count}</dd></div></dl></article>
        <div className="base-trait-grid">{data.traits.map((trait) => <article key={trait.code}><span>{trait.name}</span><strong>{number(plot.trait_averages?.[trait.code], 2)}</strong><small>{trait.unit}</small></article>)}</div></div>
        <button className="base-enter-plants" type="button" disabled={drillLoading} onClick={() => setDepth("plant")}>{drillLoading ? <LoaderCircle className="spin" size={17} /> : <Users size={17} />}进入单株层，查看该小区 {plot.plant_count} 株 <ChevronRight size={16} /></button>
      </section>}

      {depth === "plant" && plot && <section className="base-stage-content"><header><div><span>第 5 层 · {plot.material_name}</span><h3>小区 {plot.plot_no} 单株证据</h3><p>点击单株查看表型、基因型关联和选育记录；任何汇总结果都能回到这里。</p></div><Users size={22} /></header>
        {drillLoading && !plotData ? <WorkspaceLoading label="正在读取小区单株…" /> : <div className="base-plant-layout"><div className="base-plant-grid">{(plotData?.plants || []).map((item) => <button type="button" className={`${plant?.id === item.id ? "active" : ""} ${item.selection_status}`} key={item.id} onClick={() => void choosePlant(item)}><span className="plant-icon"><Sprout size={17} /></span><strong>{item.sample_code}</strong><small>第 {item.plant_no || "—"} 株 · {STATUS_LABELS[item.selection_status] || item.selection_status}</small><div><span>产量 {number(item.traits?.yield_per_plant?.value, 1)} g</span><span>株高 {number(item.traits?.plant_height?.value, 1)} cm</span></div><em>{item.genotype_count ? <><Dna size={12} />已关联基因型</> : "未关联基因型"}</em></button>)}</div>
        <aside className="base-plant-detail">{drillLoading ? <WorkspaceLoading label="正在读取单株证据…" /> : plantDetail ? <><header><div><span>单株档案</span><h4>{plantDetail.sample.sample_code}</h4><p>{plantDetail.sample.material_name} · {plantDetail.sample.generation_label || "世代未填"}</p></div><span className={`plant-status ${plantDetail.sample.selection_status}`}>{STATUS_LABELS[plantDetail.sample.selection_status] || plantDetail.sample.selection_status}</span></header><div className="base-evidence-counts"><span><ScanLine size={14} /><b>{plantDetail.evidence_counts.observations}</b> 表型</span><span><Dna size={14} /><b>{plantDetail.evidence_counts.genotype_mappings}</b> 基因型</span><span><Target size={14} /><b>{plantDetail.evidence_counts.selection_records}</b> 决策</span></div><div className="base-plant-photo-evidence">{BASE_MEDIA.slice(1).map((media) => <button type="button" key={media.id} onClick={() => setSelectedMedia(media)}><img src={media.src} alt={media.alt} /><span><b>{media.id === "single-plant-flowering" ? plantDetail.sample.sample_code : media.title}</b><small>{media.detail}</small></span></button>)}</div><LatestPlantTraits detail={plantDetail} />{plantDetail.genotype_mappings.length > 0 && <div className="base-genotype-proof"><Dna size={16} /><div><b>{plantDetail.genotype_mappings[0].fid}/{plantDetail.genotype_mappings[0].iid}</b><span>{plantDetail.genotype_mappings[0].asset_title} · v{plantDetail.genotype_mappings[0].version_number}</span></div></div>}<div className="base-trace-note"><ShieldCheck size={15} />演示影像用于展示接入形态；正式数据需绑定株号、时间、生育期、角度、设备、模型版本和人工复核状态。</div></> : <div className="base-detail-empty"><Sprout size={28} /><b>选择一株查看证据</b><span>单株表型、基因型和选育决策将在这里汇合。</span></div>}</aside></div>}
      </section>}
    </main>
    {selectedMedia && <div className="base-media-viewer" role="dialog" aria-modal="true" aria-label={selectedMedia.title} onClick={() => setSelectedMedia(null)}>
      <button type="button" className="base-media-close" onClick={() => setSelectedMedia(null)} aria-label="关闭图片"><X size={20} /></button>
      <figure onClick={(event) => event.stopPropagation()}><img src={selectedMedia.src} alt={selectedMedia.alt} /><figcaption><strong>{selectedMedia.title}</strong><span>{selectedMedia.detail}</span><small>演示影像 · 正式接入后保留原图、采集元数据与质控记录</small></figcaption></figure>
    </div>}
  </div>;
}
