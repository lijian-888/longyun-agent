import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const input = await FileBlob.load("raw/2024_区域试验农艺品质记录.xlsx");
const workbook = await SpreadsheetFile.importXlsx(input);
const inspection = await workbook.inspect({
  kind: "workbook,sheet,table,region",
  sheetId: "农艺品质记录",
  range: "A1:L10",
  maxChars: 5000,
  tableMaxRows: 10,
  tableMaxCols: 12,
});
console.log(inspection.ndjson || inspection);
const preview = await workbook.render({ sheetName: "农艺品质记录", range: "A1:L16", scale: 1.5, format: "png" });
await fs.writeFile("raw/2024_区域试验农艺品质记录.preview.png", new Uint8Array(await preview.arrayBuffer()));
