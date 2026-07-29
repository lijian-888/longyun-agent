import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const outputDir = path.resolve("raw");

const materials = [
  { code: "ME-A01", name: "候选A01", aliases: ["HZ-01", "候选A-01"], yield: 585, height: 103, weight: 26.2, setting: 85.4, head: 62.0, chalk: 4.5, blast: 3.0, lodging: 2.0, nResponse: 1.05, acid: 0.45, susceptible: 0.65 },
  { code: "ME-A02", name: "候选A02", aliases: ["HZ02", "高产2号"], yield: 603, height: 114, weight: 26.8, setting: 83.0, head: 58.5, chalk: 6.5, blast: 5.0, lodging: 4.4, nResponse: 1.55, acid: 0.20, susceptible: 1.15 },
  { code: "ME-A03", name: "候选A03", aliases: ["HZ-03", "稳产3号"], yield: 570, height: 99, weight: 25.6, setting: 87.0, head: 63.0, chalk: 3.8, blast: 2.4, lodging: 1.8, nResponse: 0.70, acid: 0.60, susceptible: 0.45 },
  { code: "ME-A04", name: "候选A04", aliases: ["HZ04", "优质4号"], yield: 553, height: 108, weight: 25.0, setting: 80.0, head: 66.0, chalk: 2.8, blast: 5.5, lodging: 3.0, nResponse: 0.85, acid: 0.10, susceptible: 1.25 },
  { code: "ME-A05", name: "候选A05", aliases: ["HZ-05", "耐酸5号"], yield: 563, height: 101, weight: 26.0, setting: 84.0, head: 61.0, chalk: 4.2, blast: 3.6, lodging: 2.2, nResponse: 0.80, acid: 1.10, susceptible: 0.60 },
  { code: "ME-A06", name: "候选A06", aliases: ["HZ06"], yield: 540, height: 96, weight: 24.8, setting: 85.0, head: 60.0, chalk: 5.3, blast: 3.2, lodging: 1.5, nResponse: 0.65, acid: 0.70, susceptible: 0.55 },
  { code: "ME-A07", name: "候选A07", aliases: ["HZ-07"], yield: 580, height: 110, weight: 25.8, setting: 82.4, head: 57.0, chalk: 5.0, blast: 4.3, lodging: 3.0, nResponse: 1.15, acid: 0.35, susceptible: 0.95 },
  { code: "ME-A08", name: "候选A08", aliases: ["HZ08"], yield: 555, height: 105, weight: 26.5, setting: 85.5, head: 64.0, chalk: 3.5, blast: 2.8, lodging: 2.0, nResponse: 0.90, acid: 0.55, susceptible: 0.50 },
  { code: "CK-01", name: "对照CK01", aliases: ["CK1", "对照一号"], yield: 535, height: 108, weight: 25.2, setting: 82.0, head: 59.0, chalk: 5.6, blast: 4.2, lodging: 3.0, nResponse: 0.85, acid: 0.30, susceptible: 0.90 },
  { code: "CK-02", name: "对照CK02", aliases: ["CK2", "对照二号"], yield: 552, height: 103, weight: 25.6, setting: 83.0, head: 60.0, chalk: 4.8, blast: 3.7, lodging: 2.5, nResponse: 0.80, acid: 0.50, susceptible: 0.70 },
];

const sites = [
  { code: "NC", name: "南昌试验点", county: "南昌县", zone: "赣北平原稻作区", soil: "红壤性水稻土", yieldEffect: -2, heightEffect: 1.0, ph: 5.6, availableP: 20.1, organicMatter: 25.6, rainfall: 1028, temperature: 24.4, disease: 4.0 },
  { code: "GZ", name: "赣州试验点", county: "信丰县", zone: "赣南丘陵双季稻区", soil: "酸性红壤水稻土", yieldEffect: -13, heightEffect: 0.5, ph: 4.9, availableP: 16.8, organicMatter: 22.1, rainfall: 1235, temperature: 25.1, disease: 6.1 },
  { code: "JJ", name: "九江试验点", county: "永修县", zone: "鄱阳湖平原稻作区", soil: "潮土性水稻土", yieldEffect: 8, heightEffect: -1.0, ph: 6.4, availableP: 23.4, organicMatter: 27.9, rainfall: 955, temperature: 24.0, disease: 3.0 },
  { code: "FZ", name: "抚州试验点", county: "东乡区", zone: "赣东丘陵稻作区", soil: "红黄壤水稻土", yieldEffect: -4, heightEffect: 0.0, ph: 5.3, availableP: 14.9, organicMatter: 23.8, rainfall: 1142, temperature: 24.8, disease: 5.2 },
];

const yearEffects = {
  2023: { yield: 0, rainfall: 0, disease: 0, label: "常年" },
  2024: { yield: -12, rainfall: 95, disease: 1.0, label: "偏湿年" },
  2025: { yield: 6, rainfall: -30, disease: -0.3, label: "适温年" },
};

const fills = {
  title: "#0E604B",
  header: "#DFF0E9",
  light: "#F7FBF9",
  note: "#FFF8E3",
  grid: "#D7E5DE",
};

function rawMaterialName(material, year, index) {
  const names = [material.name, ...material.aliases, material.code];
  return names[(year + index) % names.length];
}

function valuesFor({ material, site, year, treatmentCode, rep }) {
  const effect = yearEffects[year];
  const nitrogenEffect = treatmentCode === "M2" ? material.nResponse * 10 : 0;
  const acidPenalty = Math.max(0, 5.2 - site.ph) * (6 - material.acid * 5);
  const rainPenalty = Math.max(0, site.rainfall + effect.rainfall - 1180) * 0.025;
  const replicationNoise = (rep - 2) * 3.2;
  const yieldPerMu = material.yield + site.yieldEffect + effect.yield + nitrogenEffect - acidPenalty - rainPenalty + replicationNoise;
  const height = material.height + site.heightEffect + (treatmentCode === "M2" ? 2.0 : 0) + (rep - 2) * 0.55;
  const disease = Math.max(0, Math.min(9, material.blast + site.disease * material.susceptible * 0.34 + effect.disease - (treatmentCode === "M2" ? 0.1 : 0) + (rep - 2) * 0.15));
  return {
    yield: round(yieldPerMu, 1),
    height: round(height, 1),
    weight: round(material.weight + (rep - 2) * 0.12, 1),
    setting: round(material.setting - disease * 0.25 + (rep - 2) * 0.3, 1),
    head: round(material.head + (rep - 2) * 0.4, 1),
    chalk: round(Math.max(0.5, material.chalk + (site.rainfall + effect.rainfall - 1000) * 0.002), 1),
    blast: round(disease, 1),
    lodging: round(Math.max(0, Math.min(9, material.lodging + (treatmentCode === "M2" ? 0.75 : 0) + (height - material.height) * 0.09)), 1),
  };
}

function round(value, digits = 1) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function applySheetStyle(sheet, headerRange, dataEndRow, columnWidths) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  headerRange.format = {
    fill: fills.header,
    font: { bold: true, color: "#0A4B3B" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#AFCBBF" },
  };
  headerRange.format.rowHeight = 28;
  const used = sheet.getUsedRange();
  used.format.verticalAlignment = "center";
  used.format.borders = { insideHorizontal: { style: "thin", color: fills.grid } };
  if (dataEndRow > 1) {
    sheet.getRange(`A2:${String.fromCharCode(64 + columnWidths.length)}${dataEndRow}`).format.wrapText = false;
  }
  columnWidths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width;
  });
}

async function exportWorkbook(fileName, sheetName, rows, widths, guideRows) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, rows.length, rows[0].length).values = rows;
  const headerEnd = String.fromCharCode(64 + rows[0].length);
  applySheetStyle(sheet, sheet.getRange(`A1:${headerEnd}1`), rows.length, widths);
  const guide = workbook.worksheets.add("资料说明");
  guide.showGridLines = false;
  guide.getRange(`A1:D1`).merge();
  guide.getRange("A1").values = [["模拟原始资料说明"]];
  guide.getRange("A1").format = { fill: fills.title, font: { bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "left", verticalAlignment: "center" };
  guide.getRange("A1").format.rowHeight = 30;
  guide.getRangeByIndexes(2, 0, guideRows.length, 2).values = guideRows;
  guide.getRange(`A3:B3`).format = { fill: fills.header, font: { bold: true, color: "#0A4B3B" } };
  guide.getRange(`A3:B${guideRows.length + 2}`).format.wrapText = true;
  guide.getRange(`A3:B${guideRows.length + 2}`).format.borders = { preset: "all", style: "thin", color: fills.grid };
  guide.getRange("A:A").format.columnWidth = 22;
  guide.getRange("B:B").format.columnWidth = 72;
  guide.freezePanes.freezeRows(3);
  const file = await SpreadsheetFile.exportXlsx(workbook);
  await file.save(path.join(outputDir, fileName));
  return { workbook, sheet };
}

function layoutRows(year) {
  const rows = [["试验地点", "材料名称/材料代号", "处理", "重复", "区组", "小区号", "试验年份", "备注"]];
  let plot = 1;
  sites.forEach((site, siteIndex) => {
    ["M1", "M2"].forEach((treatmentCode, treatmentIndex) => {
      materials.forEach((material, materialIndex) => {
        for (let rep = 1; rep <= 3; rep += 1) {
          rows.push([
            site.name,
            rawMaterialName(material, year, materialIndex + rep),
            treatmentCode === "M1" ? "常规N" : "高氮处理",
            `R${rep}`,
            `B${rep}`,
            `${site.code}-${year}-${String(plot).padStart(3, "0")}`,
            year,
            siteIndex === 0 && treatmentIndex === 0 && materialIndex === 0 && rep === 1 ? "材料名称可能为别名，需与材料台账匹配" : "",
          ]);
          plot += 1;
        }
      });
    });
  });
  return rows;
}

function environmentRows(year) {
  const effect = yearEffects[year];
  return [["测试地点", "县区", "生态区", "土类", "pH", "速效磷(mg/kg)", "有机质", "生育期降雨", "平均温度", "病害压力", "采样说明"], ...sites.map((site) => [
    site.name,
    site.county,
    site.zone,
    site.soil,
    round(site.ph + (year === 2025 ? 0.05 : year === 2024 ? -0.03 : 0), 2),
    round(site.availableP + (year === 2025 ? 1.1 : year === 2024 ? -0.8 : 0), 1),
    `${round(site.organicMatter + (year === 2025 ? 0.5 : year === 2024 ? -0.4 : 0), 1)} g/kg`,
    `${site.rainfall + effect.rainfall} mm`,
    `${round(site.temperature + (year === 2024 ? 0.2 : year === 2025 ? -0.1 : 0), 1)}℃`,
    `${round(site.disease + effect.disease, 1)}级`,
    `0-20 cm 混合土样；${effect.label}`,
  ])];
}

function managementRows(year) {
  const headers = year === 2024
    ? ["试验点", "氮处理", "氮肥用量(kg/ha)", "施肥时期", "灌溉方式", "栽培密度", "管理备注"]
    : ["试验点", "处理名称", "施氮量", "施肥时期", "水分管理", "种植密度", "备注"];
  const rows = [headers];
  sites.forEach((site) => {
    [["M1", "标准施氮", 10], ["M2", "较高施氮", 14]].forEach(([code, name, nitrogen]) => {
      const nValue = year === 2024 ? nitrogen * 15 : `${nitrogen} kg/亩`;
      rows.push([
        site.name,
        year === 2024 ? code : name,
        nValue,
        "基肥:分蘖肥:穗肥=5:3:2",
        site.code === "JJ" ? "浅湿交替" : "常规灌溉",
        site.code === "GZ" ? "1.7万穴/亩" : "1.8万穴/亩",
        year === 2024 ? "单位为 kg/ha，需统一换算" : "",
      ]);
    });
  });
  return rows;
}

function phenotypeRows(year) {
  const headers = year === 2023
    ? ["地点", "材料", "氮处理", "重复", "亩产", "株高(cm)", "千粒质量(克)", "结实率", "整精米率", "垩白度", "穗瘟", "倒伏级"]
    : year === 2024
      ? ["试验点", "材料代号", "处理", "Rep", "产量kg/ha", "株高", "粒重", "结实率(%)", "整精米", "垩白度%", "穗瘟等级", "倒伏"]
      : ["地点名称", "供试材料", "N处理", "重复号", "产量(kg/亩)", "株高cm", "千粒重", "结实率", "整精米率%", "垩白度", "穗瘟", "倒伏等级"];
  const rows = [headers];
  sites.forEach((site) => {
    ["M1", "M2"].forEach((code) => {
      materials.forEach((material, materialIndex) => {
        for (let rep = 1; rep <= 3; rep += 1) {
          const value = valuesFor({ material, site, year, treatmentCode: code, rep });
          const materialName = rawMaterialName(material, year, materialIndex + rep);
          if (year === 2023) {
            rows.push([site.name, materialName, code === "M1" ? "常规N" : "高氮处理", `R${rep}`, `${value.yield} kg/亩`, `${value.height} cm`, `${value.weight} 克`, `${value.setting}%`, `${value.head}%`, `${value.chalk}%`, `${value.blast}级`, `${value.lodging}级`]);
          } else if (year === 2024) {
            rows.push([site.name, materialName, code, rep, round(value.yield * 15, 1), `${value.height}厘米`, value.weight, value.setting, value.head, value.chalk, value.blast, value.lodging]);
          } else {
            rows.push([site.name, materialName, code === "M1" ? "M1-常规" : "M2-高氮", rep, value.yield, value.height, `${value.weight}g`, value.setting, value.head, value.chalk, value.blast, value.lodging]);
          }
        }
      });
    });
  });
  return rows;
}

async function build() {
  await fs.mkdir(outputDir, { recursive: true });
  for (const year of [2023, 2024, 2025]) {
    const commonGuide = [
      ["项目", "说明"],
      ["资料性质", "模拟的区域试验原始资料；用于演示数据治理，不代表真实材料表现或真实推荐结论。"],
      ["原始数据保留", "本文件为原始资料副本，不应直接改写；平台应将清洗建议、字段映射和单位换算记录在治理日志中。"],
      ["关联线索", "可通过试验年份、试验地点、处理、重复和材料名称/别名，与同年度其他资料表匹配。"],
      ["预期治理", "材料名称映射至材料主数据；单位统一；形成试验、环境、管理、参试材料和观测记录。"],
    ];
    await exportWorkbook(`${year}_区域试验材料与小区布局.xlsx`, "材料与小区布局", layoutRows(year), [18, 22, 14, 10, 10, 22, 12, 36], commonGuide);
    await exportWorkbook(`${year}_区域试验环境与土壤检测.xlsx`, "环境与土壤", environmentRows(year), [18, 14, 24, 22, 10, 16, 16, 16, 14, 14, 34], commonGuide);
    await exportWorkbook(`${year}_区域试验管理记录.xlsx`, "管理记录", managementRows(year), [18, 16, 20, 30, 16, 18, 32], commonGuide);
    await exportWorkbook(`${year}_区域试验农艺品质记录.xlsx`, "农艺品质记录", phenotypeRows(year), [18, 20, 14, 10, 16, 14, 16, 14, 14, 14, 12, 12], commonGuide);
  }
  console.log(`Created 12 raw demo workbooks in ${outputDir}`);
}

await build();
