"""Operational AC3.1-AC3.6 acceptance check against a configured data plane.

Run only in an explicitly selected environment. The script writes a uniquely
scoped acceptance project so it cannot collide with normal project data.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

import fitz
from openpyxl import Workbook
from sqlalchemy import text

from app.institution_data import (
    GENOTYPE_ARCHIVE_MAX_BYTES,
    REGULAR_MAX_BYTES,
    InstitutionDataSettings,
    InstitutionDatabaseManager,
    MinioInstitutionStore,
    StagedUpload,
    import_into_institution_database,
    list_batches,
    object_key_for,
    trace_entity,
    upload_limit,
    validate_file_contract,
)


def staged(path: Path) -> StagedUpload:
    payload = path.read_bytes()
    suffix = ".vcf.gz" if path.name.lower().endswith(".vcf.gz") else path.suffix.lower()
    return StagedUpload(path, path.name, len(payload), hashlib.sha256(payload).hexdigest(), suffix)


def generated_fixtures(root: Path, demo_root: Path, chinese_xlsx: Path) -> list[tuple[str, Path]]:
    fixtures: list[tuple[str, Path]] = [
        ("germplasm", demo_root / "germplasm.csv"),
        ("pedigree", demo_root / "pedigree.csv"),
        ("phenotype", demo_root / "phenotype.csv"),
        ("environment", demo_root / "environment.json"),
        ("genotype", demo_root / "genotype.vcf"),
        ("literature", demo_root / "literature.txt"),
        ("germplasm", chinese_xlsx),
    ]

    json_path = root / "种质资源验收.json"
    json_path.write_text(json.dumps([{"germplasm_id": "ACCEPT-JSON-1", "name": "JSON验收材料"}], ensure_ascii=False), encoding="utf-8")
    fixtures.append(("germplasm", json_path))

    xlsx_path = root / "多工作表环境验收.xlsx"
    workbook = Workbook()
    workbook.active.title = "填写说明"
    workbook.active.append(["项目", "要求"])
    workbook.active.append(["说明", "数据位于第二工作表"])
    sheet = workbook.create_sheet("环境数据")
    sheet.append(["标题", None, None])
    sheet.append(["environment_id", "location", "year"])
    sheet.append(["ACCEPT-ENV-1", "三亚", 2026])
    workbook.save(xlsx_path)
    fixtures.append(("environment", xlsx_path))

    vcf_gz = root / "南繁基因型验收.vcf.gz"
    with gzip.open(vcf_gz, "wb") as handle:
        handle.write((demo_root / "genotype.vcf").read_bytes())
    fixtures.append(("genotype", vcf_gz))

    plink = root / "南繁PLINK验收.zip"
    with zipfile.ZipFile(plink, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("acceptance.bed", b"\x6c\x1b\x01")
        archive.writestr("acceptance.bim", "1 rs1 0 1 A G\n")
        archive.writestr("acceptance.fam", "F1 HNNF-G001 0 0 0 -9\n")
    fixtures.append(("genotype", plink))

    pdf_path = root / "南繁公开文献验收.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Longyun public rice breeding literature acceptance")
    document.save(pdf_path)
    document.close()
    fixtures.append(("literature", pdf_path))

    docx_path = root / "南繁公开文献验收.docx"
    with zipfile.ZipFile(docx_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:document><w:p><w:t>南繁公开文献验收</w:t></w:p></w:document>")
    fixtures.append(("literature", docx_path))

    anomaly = root / "关联异常验收.csv"
    anomaly.write_text(
        "germplasm_id,trait_code,value,environment_id\nUNKNOWN-MATERIAL,plant_height,100,UNKNOWN-ENV\n",
        encoding="utf-8",
    )
    fixtures.append(("phenotype", anomaly))
    return fixtures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--institution-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--demo-root", type=Path, required=True)
    parser.add_argument("--chinese-xlsx", type=Path, required=True)
    args = parser.parse_args()

    settings = InstitutionDataSettings.from_env()
    store = MinioInstitutionStore(settings)
    databases = InstitutionDatabaseManager(settings)
    databases.ensure_database(args.database)
    engine = databases.engine(args.database)
    prefix = f"acceptance/{args.project_id}/"
    results = []

    with tempfile.TemporaryDirectory(prefix="longyun-ac3-") as temp_dir:
        fixtures = generated_fixtures(Path(temp_dir), args.demo_root, args.chinese_xlsx)
        for dataset_type, path in fixtures:
            upload = staged(path)
            validate_file_contract(dataset_type, upload.file_name, upload.size_bytes)
            batch_id = str(uuid.uuid4())
            key = f"{prefix}{object_key_for(args.institution_id, args.project_id, dataset_type, batch_id, upload.file_name)}"
            store.put_file(args.bucket, key, upload, "application/octet-stream")
            result = import_into_institution_database(
                engine,
                batch_id=batch_id,
                institution_id=args.institution_id,
                project_id=args.project_id,
                dataset_type=dataset_type,
                upload=upload,
                bucket_name=args.bucket,
                object_key=key,
            )
            results.append({"dataset_type": dataset_type, "file": path.name, **result})

    assert upload_limit("germplasm", "demo.xlsx") == REGULAR_MAX_BYTES
    assert upload_limit("genotype", "demo.vcf.gz") == GENOTYPE_ARCHIVE_MAX_BYTES
    required_suffixes = {".csv", ".xlsx", ".json", ".vcf", ".vcf.gz", ".zip", ".pdf", ".docx", ".txt"}
    imported_suffixes = {
        ".vcf.gz" if item["file"].lower().endswith(".vcf.gz") else Path(item["file"]).suffix.lower()
        for item in results
    }
    assert required_suffixes <= imported_suffixes, (required_suffixes, imported_suffixes)

    batches = list_batches(engine, args.institution_id, args.project_id, 100)
    trace = trace_entity(engine, args.institution_id, args.project_id, "HNNF-G001")
    with engine.connect() as connection:
        counts = dict(connection.execute(text("""
            SELECT 'entities', count(*) FROM data_entity WHERE institution_id=:institution_id AND project_id=:project_id
            UNION ALL SELECT 'relations', count(*) FROM data_relation WHERE institution_id=:institution_id AND project_id=:project_id
            UNION ALL SELECT 'issues', count(*) FROM data_issue WHERE institution_id=:institution_id AND project_id=:project_id
        """), {"institution_id": args.institution_id, "project_id": args.project_id}).all())
    objects = list(store.client.list_objects(args.bucket, prefix=prefix, recursive=True))

    assert len(batches) == len(results)
    assert len(objects) == len(results)
    assert counts["entities"] > 0 and counts["relations"] > 0
    assert counts["issues"] > 0
    assert trace["entities"] and trace["relations"]
    assert any(item["file"] == args.chinese_xlsx.name and item["entity_count"] == 10 for item in results)
    assert any(item["issue_count"] > 0 for item in results)

    print(json.dumps({
        "status": "passed",
        "institution_id": args.institution_id,
        "project_id": args.project_id,
        "bucket": args.bucket,
        "database": args.database,
        "formats": sorted(imported_suffixes),
        "batch_count": len(batches),
        "object_count": len(objects),
        **counts,
        "trace_entities": len(trace["entities"]),
        "trace_relations": len(trace["relations"]),
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
