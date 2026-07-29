"""Traceable multi-environment rice trial demo data and analytical helpers.

This module deliberately uses a trial-level data model instead of reusing the
legacy variety phenotype table.  A phenotype value is only meaningful together
with the material, field trial, treatment, replicate, environment and source
that produced it.

The generated values are clearly labelled as simulated research data.  They
exist to demonstrate how a scattered regional-trial data package can become a
reusable, evidence-backed dataset; they must not be used as real breeding
conclusions.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session


DEMO_PACKAGE_CODE = "RICE-MET-2023-2025-DEMO"
DEMO_PACKAGE_NAME = "模拟三年四点水稻区域试验资料包"
NAMESPACE = uuid.UUID("e621ac77-6f21-4e20-9a4f-7f228d1a4a2a")


def _id(*parts: object) -> str:
    """Create stable UUIDs so re-seeding cannot duplicate a demo record."""
    return str(uuid.uuid5(NAMESPACE, "|".join(str(item) for item in parts)))


def ensure_trial_demo_schema(session: Session) -> None:
    """Create isolated trial-level tables and a read-only material summary view."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS trial_data_package (
            id VARCHAR(36) PRIMARY KEY,
            package_code VARCHAR(100) NOT NULL UNIQUE,
            package_name VARCHAR(300) NOT NULL,
            dataset_type VARCHAR(100) NOT NULL,
            governance_status VARCHAR(30) NOT NULL DEFAULT 'governed',
            description TEXT,
            is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS breeding_material (
            id VARCHAR(36) PRIMARY KEY,
            material_code VARCHAR(100) NOT NULL UNIQUE,
            material_name VARCHAR(200) NOT NULL,
            material_type VARCHAR(100) NOT NULL,
            is_check BOOLEAN NOT NULL DEFAULT FALSE,
            aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
            pedigree_summary TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_site (
            id VARCHAR(36) PRIMARY KEY,
            site_code VARCHAR(80) NOT NULL UNIQUE,
            site_name VARCHAR(200) NOT NULL,
            province VARCHAR(100),
            county VARCHAR(100),
            ecological_zone VARCHAR(200) NOT NULL,
            soil_type VARCHAR(200),
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS field_trial (
            id VARCHAR(36) PRIMARY KEY,
            trial_code VARCHAR(120) NOT NULL UNIQUE,
            package_id VARCHAR(36) NOT NULL REFERENCES trial_data_package(id),
            site_id VARCHAR(36) NOT NULL REFERENCES trial_site(id),
            trial_year INTEGER NOT NULL,
            trial_name VARCHAR(300) NOT NULL,
            crop_name VARCHAR(80) NOT NULL DEFAULT '水稻',
            experiment_type VARCHAR(100) NOT NULL DEFAULT '区域试验',
            design_type VARCHAR(120) NOT NULL DEFAULT '随机区组设计',
            replicate_count INTEGER NOT NULL,
            design_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            design_validation_status VARCHAR(30) NOT NULL DEFAULT 'unverified',
            data_status VARCHAR(30) NOT NULL DEFAULT 'published',
            source_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_environment_metric (
            id VARCHAR(36) PRIMARY KEY,
            trial_id VARCHAR(36) NOT NULL REFERENCES field_trial(id) ON DELETE CASCADE,
            metric_code VARCHAR(100) NOT NULL,
            metric_name VARCHAR(200) NOT NULL,
            value_numeric DOUBLE PRECISION NOT NULL,
            unit VARCHAR(50) NOT NULL,
            original_value TEXT NOT NULL,
            collection_method VARCHAR(200),
            source_locator VARCHAR(300),
            UNIQUE(trial_id, metric_code)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_treatment (
            id VARCHAR(36) PRIMARY KEY,
            trial_id VARCHAR(36) NOT NULL REFERENCES field_trial(id) ON DELETE CASCADE,
            treatment_code VARCHAR(80) NOT NULL,
            treatment_name VARCHAR(200) NOT NULL,
            treatment_description TEXT,
            UNIQUE(trial_id, treatment_code)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_management_event (
            id VARCHAR(36) PRIMARY KEY,
            treatment_id VARCHAR(36) NOT NULL REFERENCES trial_treatment(id) ON DELETE CASCADE,
            event_type VARCHAR(100) NOT NULL,
            input_name VARCHAR(200) NOT NULL,
            rate_per_mu DOUBLE PRECISION,
            unit VARCHAR(50),
            event_stage VARCHAR(100),
            notes TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_entry (
            id VARCHAR(36) PRIMARY KEY,
            trial_id VARCHAR(36) NOT NULL REFERENCES field_trial(id) ON DELETE CASCADE,
            treatment_id VARCHAR(36) NOT NULL REFERENCES trial_treatment(id) ON DELETE CASCADE,
            material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
            replicate_no INTEGER NOT NULL,
            block_no INTEGER NOT NULL,
            plot_no VARCHAR(50) NOT NULL,
            raw_material_name VARCHAR(200) NOT NULL,
            source_locator VARCHAR(300) NOT NULL,
            UNIQUE(trial_id, treatment_id, material_id, replicate_no, block_no, plot_no)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_phenotype_observation (
            id VARCHAR(36) PRIMARY KEY,
            entry_id VARCHAR(36) NOT NULL REFERENCES trial_entry(id) ON DELETE CASCADE,
            trait_code VARCHAR(100) NOT NULL,
            trait_name VARCHAR(200) NOT NULL,
            trait_category VARCHAR(100) NOT NULL,
            value_numeric DOUBLE PRECISION,
            value_text TEXT,
            unit VARCHAR(50) NOT NULL,
            original_value TEXT NOT NULL,
            observation_stage VARCHAR(100) NOT NULL DEFAULT '成熟期',
            evaluation_method VARCHAR(200),
            source_locator VARCHAR(300) NOT NULL,
            quality_status VARCHAR(30) NOT NULL DEFAULT 'passed',
            publish_status VARCHAR(30) NOT NULL DEFAULT 'published',
            UNIQUE(entry_id, trait_code, observation_stage)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_source_file (
            id VARCHAR(36) PRIMARY KEY,
            package_id VARCHAR(36) NOT NULL REFERENCES trial_data_package(id) ON DELETE CASCADE,
            file_name VARCHAR(500) NOT NULL,
            source_role VARCHAR(120) NOT NULL,
            source_format VARCHAR(50) NOT NULL,
            relative_path TEXT NOT NULL,
            raw_schema_note TEXT,
            processing_status VARCHAR(30) NOT NULL DEFAULT 'governed',
            checksum VARCHAR(128),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(package_id, file_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trial_analysis_run (
            id VARCHAR(36) PRIMARY KEY,
            package_id VARCHAR(36) NOT NULL REFERENCES trial_data_package(id) ON DELETE CASCADE,
            analysis_type VARCHAR(100) NOT NULL,
            analysis_version VARCHAR(50) NOT NULL,
            requested_by VARCHAR(200),
            request_question TEXT,
            filters JSONB NOT NULL DEFAULT '{}'::jsonb,
            model_formula TEXT,
            engine_name VARCHAR(100),
            source_record_count INTEGER,
            source_trial_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(30) NOT NULL DEFAULT 'completed',
            result_json JSONB NOT NULL,
            limitation_note TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_field_trial_package_year ON field_trial(package_id, trial_year)",
        "CREATE INDEX IF NOT EXISTS ix_trial_entry_trial_material ON trial_entry(trial_id, material_id)",
        "CREATE INDEX IF NOT EXISTS ix_trial_observation_trait ON trial_phenotype_observation(trait_code)",
        "CREATE INDEX IF NOT EXISTS ix_trial_environment_trial_metric ON trial_environment_metric(trial_id, metric_code)",
        """
        CREATE OR REPLACE VIEW v_trial_material_summary AS
        SELECT
            trial.id AS trial_id,
            trial.trial_code,
            trial.trial_year,
            site.site_code,
            site.site_name,
            site.ecological_zone,
            treatment.id AS treatment_id,
            treatment.treatment_code,
            treatment.treatment_name,
            material.id AS material_id,
            material.material_code,
            material.material_name,
            material.is_check,
            COUNT(DISTINCT entry.id) AS replicate_count,
            ROUND(AVG(CASE WHEN observation.trait_code = 'yield_per_mu' THEN observation.value_numeric END)::numeric, 2) AS yield_per_mu,
            ROUND(AVG(CASE WHEN observation.trait_code = 'plant_height' THEN observation.value_numeric END)::numeric, 2) AS plant_height,
            ROUND(AVG(CASE WHEN observation.trait_code = 'thousand_grain_weight' THEN observation.value_numeric END)::numeric, 2) AS thousand_grain_weight,
            ROUND(AVG(CASE WHEN observation.trait_code = 'seed_setting_rate' THEN observation.value_numeric END)::numeric, 2) AS seed_setting_rate,
            ROUND(AVG(CASE WHEN observation.trait_code = 'head_rice_rate' THEN observation.value_numeric END)::numeric, 2) AS head_rice_rate,
            ROUND(AVG(CASE WHEN observation.trait_code = 'chalkiness_degree' THEN observation.value_numeric END)::numeric, 2) AS chalkiness_degree,
            ROUND(AVG(CASE WHEN observation.trait_code = 'panicle_blast_score' THEN observation.value_numeric END)::numeric, 2) AS panicle_blast_score,
            ROUND(AVG(CASE WHEN observation.trait_code = 'lodging_score' THEN observation.value_numeric END)::numeric, 2) AS lodging_score
        FROM field_trial trial
        JOIN trial_site site ON site.id = trial.site_id
        JOIN trial_treatment treatment ON treatment.trial_id = trial.id
        JOIN trial_entry entry ON entry.trial_id = trial.id AND entry.treatment_id = treatment.id
        JOIN breeding_material material ON material.id = entry.material_id
        JOIN trial_phenotype_observation observation ON observation.entry_id = entry.id
        WHERE observation.publish_status = 'published'
        GROUP BY trial.id, trial.trial_code, trial.trial_year, site.site_code, site.site_name,
                 site.ecological_zone, treatment.id, treatment.treatment_code, treatment.treatment_name,
                 material.id, material.material_code, material.material_name, material.is_check
        """,
    )
    for statement in statements:
        session.execute(text(statement))
    # Existing local deployments predate the RCBD governance and formal-analysis
    # fields.  PostgreSQL keeps their history and adds only the new columns.
    migrations = (
        "ALTER TABLE field_trial ADD COLUMN IF NOT EXISTS design_metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE field_trial ADD COLUMN IF NOT EXISTS design_validation_status VARCHAR(30) NOT NULL DEFAULT 'unverified'",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS requested_by VARCHAR(200)",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS request_question TEXT",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS filters JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS model_formula TEXT",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS engine_name VARCHAR(100)",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS source_record_count INTEGER",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS source_trial_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'completed'",
        "ALTER TABLE trial_analysis_run ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
    )
    for statement in migrations:
        session.execute(text(statement))
    session.commit()


def _material_profiles() -> list[dict[str, Any]]:
    return [
        {"code": "ME-A01", "name": "候选A01", "aliases": ["HZ-01", "候选A-01"], "check": False, "yield": 585, "height": 103, "weight": 26.2, "setting": 85.4, "head": 62.0, "chalk": 4.5, "blast": 3.0, "lodging": 2.0, "n_response": 1.05, "acid_tolerance": 0.45, "blast_susceptibility": 0.65},
        {"code": "ME-A02", "name": "候选A02", "aliases": ["HZ02", "高产2号"], "check": False, "yield": 603, "height": 114, "weight": 26.8, "setting": 83.0, "head": 58.5, "chalk": 6.5, "blast": 5.0, "lodging": 4.4, "n_response": 1.55, "acid_tolerance": 0.20, "blast_susceptibility": 1.15},
        {"code": "ME-A03", "name": "候选A03", "aliases": ["HZ-03", "稳产3号"], "check": False, "yield": 570, "height": 99, "weight": 25.6, "setting": 87.0, "head": 63.0, "chalk": 3.8, "blast": 2.4, "lodging": 1.8, "n_response": 0.70, "acid_tolerance": 0.60, "blast_susceptibility": 0.45},
        {"code": "ME-A04", "name": "候选A04", "aliases": ["HZ04", "优质4号"], "check": False, "yield": 553, "height": 108, "weight": 25.0, "setting": 80.0, "head": 66.0, "chalk": 2.8, "blast": 5.5, "lodging": 3.0, "n_response": 0.85, "acid_tolerance": 0.10, "blast_susceptibility": 1.25},
        {"code": "ME-A05", "name": "候选A05", "aliases": ["HZ-05", "耐酸5号"], "check": False, "yield": 563, "height": 101, "weight": 26.0, "setting": 84.0, "head": 61.0, "chalk": 4.2, "blast": 3.6, "lodging": 2.2, "n_response": 0.80, "acid_tolerance": 1.10, "blast_susceptibility": 0.60},
        {"code": "ME-A06", "name": "候选A06", "aliases": ["HZ06"], "check": False, "yield": 540, "height": 96, "weight": 24.8, "setting": 85.0, "head": 60.0, "chalk": 5.3, "blast": 3.2, "lodging": 1.5, "n_response": 0.65, "acid_tolerance": 0.70, "blast_susceptibility": 0.55},
        {"code": "ME-A07", "name": "候选A07", "aliases": ["HZ-07"], "check": False, "yield": 580, "height": 110, "weight": 25.8, "setting": 82.4, "head": 57.0, "chalk": 5.0, "blast": 4.3, "lodging": 3.0, "n_response": 1.15, "acid_tolerance": 0.35, "blast_susceptibility": 0.95},
        {"code": "ME-A08", "name": "候选A08", "aliases": ["HZ08"], "check": False, "yield": 555, "height": 105, "weight": 26.5, "setting": 85.5, "head": 64.0, "chalk": 3.5, "blast": 2.8, "lodging": 2.0, "n_response": 0.90, "acid_tolerance": 0.55, "blast_susceptibility": 0.50},
        {"code": "CK-01", "name": "对照CK01", "aliases": ["CK1", "对照一号"], "check": True, "yield": 535, "height": 108, "weight": 25.2, "setting": 82.0, "head": 59.0, "chalk": 5.6, "blast": 4.2, "lodging": 3.0, "n_response": 0.85, "acid_tolerance": 0.30, "blast_susceptibility": 0.90},
        {"code": "CK-02", "name": "对照CK02", "aliases": ["CK2", "对照二号"], "check": True, "yield": 552, "height": 103, "weight": 25.6, "setting": 83.0, "head": 60.0, "chalk": 4.8, "blast": 3.7, "lodging": 2.5, "n_response": 0.80, "acid_tolerance": 0.50, "blast_susceptibility": 0.70},
    ]


def _sites() -> list[dict[str, Any]]:
    return [
        {"code": "NC", "name": "南昌试验点", "county": "南昌县", "zone": "赣北平原稻作区", "soil": "红壤性水稻土", "lat": 28.54, "lon": 115.93, "yield_effect": -2, "height_effect": 1.0, "ph": 5.6, "available_p": 20.1, "organic_matter": 25.6, "rainfall": 1028, "temperature": 24.4, "disease_pressure": 4.0},
        {"code": "GZ", "name": "赣州试验点", "county": "信丰县", "zone": "赣南丘陵双季稻区", "soil": "酸性红壤水稻土", "lat": 25.39, "lon": 114.92, "yield_effect": -13, "height_effect": 0.5, "ph": 4.9, "available_p": 16.8, "organic_matter": 22.1, "rainfall": 1235, "temperature": 25.1, "disease_pressure": 6.1},
        {"code": "JJ", "name": "九江试验点", "county": "永修县", "zone": "鄱阳湖平原稻作区", "soil": "潮土性水稻土", "lat": 29.07, "lon": 115.82, "yield_effect": 8, "height_effect": -1.0, "ph": 6.4, "available_p": 23.4, "organic_matter": 27.9, "rainfall": 955, "temperature": 24.0, "disease_pressure": 3.0},
        {"code": "FZ", "name": "抚州试验点", "county": "东乡区", "zone": "赣东丘陵稻作区", "soil": "红黄壤水稻土", "lat": 28.16, "lon": 116.61, "yield_effect": -4, "height_effect": 0.0, "ph": 5.3, "available_p": 14.9, "organic_matter": 23.8, "rainfall": 1142, "temperature": 24.8, "disease_pressure": 5.2},
    ]


YEAR_EFFECTS = {
    2023: {"yield": 0, "rainfall": 0, "disease": 0},
    2024: {"yield": -12, "rainfall": 95, "disease": 1.0},
    2025: {"yield": 6, "rainfall": -30, "disease": -0.3},
}
TRAIT_META = {
    "yield_per_mu": ("亩产", "产量表现", "kg/亩", "成熟期"),
    "plant_height": ("株高", "农艺性状", "cm", "成熟期"),
    "thousand_grain_weight": ("千粒重", "产量构成", "g", "成熟期"),
    "seed_setting_rate": ("结实率", "产量构成", "%", "成熟期"),
    "head_rice_rate": ("整精米率", "加工品质", "%", "成熟期"),
    "chalkiness_degree": ("垩白度", "外观品质", "%", "成熟期"),
    "panicle_blast_score": ("穗瘟等级", "抗病性", "级", "成熟期"),
    "lodging_score": ("倒伏等级", "抗倒伏性", "级", "成熟期"),
}


def seed_multi_environment_trial_demo(session: Session) -> dict[str, int]:
    """Seed a deterministic 3-year, 4-site, 10-material regional-trial demo."""
    existing = session.scalar(
        text("SELECT id FROM trial_data_package WHERE package_code = :code"),
        {"code": DEMO_PACKAGE_CODE},
    )
    if existing:
        return trial_demo_overview(session)

    package_id = _id("package", DEMO_PACKAGE_CODE)
    session.execute(
        text("""
            INSERT INTO trial_data_package (
                id, package_code, package_name, dataset_type, governance_status,
                description, is_simulated
            ) VALUES (
                :id, :code, :name, '多环境区域试验', 'governed', :description, TRUE
            )
        """),
        {
            "id": package_id,
            "code": DEMO_PACKAGE_CODE,
            "name": DEMO_PACKAGE_NAME,
            "description": "模拟资料：用于演示三年四点区域试验从分散原始表到可追溯试验级数据及分析结果的治理过程。",
        },
    )

    material_profiles = _material_profiles()
    for item in material_profiles:
        item["id"] = _id("material", item["code"])
        session.execute(
            text("""
                INSERT INTO breeding_material (
                    id, material_code, material_name, material_type, is_check, aliases, pedigree_summary
                ) VALUES (
                    :id, :code, :name, '水稻育种材料', :is_check, CAST(:aliases AS jsonb), :pedigree
                )
            """),
            {
                "id": item["id"], "code": item["code"], "name": item["name"],
                "is_check": item["check"], "aliases": _json(item["aliases"]),
                "pedigree": "模拟育种材料；仅用于平台演示，不代表真实系谱。",
            },
        )

    site_records = _sites()
    for item in site_records:
        item["id"] = _id("site", item["code"])
        session.execute(
            text("""
                INSERT INTO trial_site (
                    id, site_code, site_name, province, county, ecological_zone, soil_type, latitude, longitude
                ) VALUES (
                    :id, :code, :name, '江西省', :county, :zone, :soil, :lat, :lon
                )
            """),
            {
                "id": item["id"], "code": item["code"], "name": item["name"], "county": item["county"],
                "zone": item["zone"], "soil": item["soil"], "lat": item["lat"], "lon": item["lon"],
            },
        )

    source_rows = []
    for year in YEAR_EFFECTS:
        source_rows.extend([
            (f"{year}_区域试验材料与小区布局.xlsx", "材料参试与小区布局", "xlsx", f"samples/multi_environment_trial_demo/raw/{year}_区域试验材料与小区布局.xlsx", "材料列使用候选A01、HZ-01、CK1等别名；含处理、重复、区组和小区号。"),
            (f"{year}_区域试验环境与土壤检测.xlsx", "环境与土壤", "xlsx", f"samples/multi_environment_trial_demo/raw/{year}_区域试验环境与土壤检测.xlsx", "pH、有效磷、有机质、降雨量、病害压力；环境指标与试验点分表保存。"),
            (f"{year}_区域试验管理记录.xlsx", "栽培管理", "xlsx", f"samples/multi_environment_trial_demo/raw/{year}_区域试验管理记录.xlsx", "施氮量存在 kg/亩 和 kg/ha 两种原始单位。"),
            (f"{year}_区域试验农艺品质记录.xlsx", "表型观测", "xlsx", f"samples/multi_environment_trial_demo/raw/{year}_区域试验农艺品质记录.xlsx", "产量、株高、千粒重、结实率、米质和抗病等级分散记录。"),
        ])
    for index, (file_name, role, fmt, relative_path, note) in enumerate(source_rows, start=1):
        session.execute(
            text("""
                INSERT INTO trial_source_file (
                    id, package_id, file_name, source_role, source_format, relative_path, raw_schema_note, processing_status
                ) VALUES (:id, :package_id, :file_name, :role, :fmt, :relative_path, :note, 'governed')
            """),
            {
                "id": _id("source", file_name), "package_id": package_id, "file_name": file_name,
                "role": role, "fmt": fmt, "relative_path": relative_path, "note": note,
            },
        )

    entry_count = 0
    observation_count = 0
    for year, year_effect in YEAR_EFFECTS.items():
        for site in site_records:
            trial_code = f"RRT-{year}-{site['code']}"
            trial_id = _id("trial", trial_code)
            session.execute(
                text("""
                    INSERT INTO field_trial (
                        id, trial_code, package_id, site_id, trial_year, trial_name, replicate_count, source_note
                    ) VALUES (
                        :id, :code, :package_id, :site_id, :year, :name, 3,
                        '模拟区域试验：随机区组设计，两个施氮处理；全部结果均为演示数据。'
                    )
                """),
                {
                    "id": trial_id, "code": trial_code, "package_id": package_id, "site_id": site["id"],
                    "year": year, "name": f"{year}年{site['name']}水稻区域试验",
                },
            )
            environment = {
                "soil_ph": ("土壤pH", round(site["ph"] + (0.05 if year == 2025 else -0.03 if year == 2024 else 0), 2), "pH", "pH计测定"),
                "available_phosphorus": ("土壤有效磷", round(site["available_p"] + (1.1 if year == 2025 else -0.8 if year == 2024 else 0), 1), "mg/kg", "土壤速效磷测定"),
                "organic_matter": ("土壤有机质", round(site["organic_matter"] + (0.5 if year == 2025 else -0.4 if year == 2024 else 0), 1), "g/kg", "重铬酸钾氧化法"),
                "rainfall": ("生育期降雨量", site["rainfall"] + year_effect["rainfall"], "mm", "试验点气象汇总"),
                "mean_temperature": ("生育期平均温度", round(site["temperature"] + (0.2 if year == 2024 else -0.1 if year == 2025 else 0), 1), "°C", "试验点气象汇总"),
                "disease_pressure": ("穗瘟病害压力", round(site["disease_pressure"] + year_effect["disease"], 1), "级", "田间自然诱发综合记载"),
            }
            for metric_code, (metric_name, value, unit, method) in environment.items():
                session.execute(
                    text("""
                        INSERT INTO trial_environment_metric (
                            id, trial_id, metric_code, metric_name, value_numeric, unit,
                            original_value, collection_method, source_locator
                        ) VALUES (
                            :id, :trial_id, :code, :name, :value, :unit,
                            :original_value, :method, :locator
                        )
                    """),
                    {
                        "id": _id("environment", trial_code, metric_code), "trial_id": trial_id,
                        "code": metric_code, "name": metric_name, "value": value, "unit": unit,
                        "original_value": f"{value} {unit}", "method": method,
                        "locator": f"{year}_区域试验环境与土壤检测.xlsx/{site['name']}",
                    },
                )

            for treatment_code, treatment_name, nitrogen_rate in (
                ("M1", "标准施氮", 10.0),
                ("M2", "较高施氮", 14.0),
            ):
                treatment_id = _id("treatment", trial_code, treatment_code)
                session.execute(
                    text("""
                        INSERT INTO trial_treatment (
                            id, trial_id, treatment_code, treatment_name, treatment_description
                        ) VALUES (
                            :id, :trial_id, :code, :name, :description
                        )
                    """),
                    {
                        "id": treatment_id, "trial_id": trial_id, "code": treatment_code,
                        "name": treatment_name,
                        "description": f"模拟管理处理：全生育期纯氮 {nitrogen_rate} kg/亩；其他管理按试验点统一执行。",
                    },
                )
                session.execute(
                    text("""
                        INSERT INTO trial_management_event (
                            id, treatment_id, event_type, input_name, rate_per_mu, unit, event_stage, notes
                        ) VALUES (
                            :id, :treatment_id, '施肥', '纯氮', :rate, 'kg/亩', '基肥+分蘖肥+穗肥',
                            '模拟管理事件；用于分析管理差异的相关性，不构成真实生产建议。'
                        )
                    """),
                    {"id": _id("management", trial_code, treatment_code), "treatment_id": treatment_id, "rate": nitrogen_rate},
                )

                for material_index, material in enumerate(material_profiles, start=1):
                    for replicate_no, replicate_effect in ((1, -4.0), (2, 0.0), (3, 4.0)):
                        entry_id = _id("entry", trial_code, treatment_code, material["code"], replicate_no)
                        raw_name = material["aliases"][0] if (material_index + replicate_no + year) % 3 == 0 else material["name"]
                        session.execute(
                            text("""
                                INSERT INTO trial_entry (
                                    id, trial_id, treatment_id, material_id, replicate_no, block_no, plot_no,
                                    raw_material_name, source_locator
                                ) VALUES (
                                    :id, :trial_id, :treatment_id, :material_id, :replicate_no, :block_no,
                                    :plot_no, :raw_material_name, :source_locator
                                )
                            """),
                            {
                                "id": entry_id, "trial_id": trial_id, "treatment_id": treatment_id,
                                "material_id": material["id"], "replicate_no": replicate_no,
                                "block_no": replicate_no, "plot_no": f"{treatment_code}-{material_index:02d}-{replicate_no}",
                                "raw_material_name": raw_name,
                                "source_locator": f"{year}_区域试验材料与小区布局.xlsx/{site['name']}/第{replicate_no}重复",
                            },
                        )
                        values = _observation_values(
                            material=material,
                            site=site,
                            year=year,
                            year_effect=year_effect,
                            treatment_code=treatment_code,
                            replicate_effect=replicate_effect,
                        )
                        for trait_code, value in values.items():
                            trait_name, category, unit, stage = TRAIT_META[trait_code]
                            raw_unit = unit
                            raw_value = value
                            # Simulate the unit and naming disorder an institute often brings.
                            if trait_code == "yield_per_mu" and (material_index + replicate_no) % 4 == 0:
                                raw_value = round(value * 15, 1)
                                raw_unit = "kg/ha"
                            elif trait_code == "thousand_grain_weight" and replicate_no == 3:
                                raw_unit = "克"
                            session.execute(
                                text("""
                                    INSERT INTO trial_phenotype_observation (
                                        id, entry_id, trait_code, trait_name, trait_category, value_numeric, unit,
                                        original_value, observation_stage, evaluation_method, source_locator
                                    ) VALUES (
                                        :id, :entry_id, :trait_code, :trait_name, :trait_category, :value, :unit,
                                        :original_value, :stage, :method, :locator
                                    )
                                """),
                                {
                                    "id": _id("observation", entry_id, trait_code), "entry_id": entry_id,
                                    "trait_code": trait_code, "trait_name": trait_name, "trait_category": category,
                                    "value": value, "unit": unit, "original_value": f"{raw_value} {raw_unit}",
                                    "stage": stage,
                                    "method": "模拟区域试验统一测定方法",
                                    "locator": f"{year}_区域试验农艺品质记录.xlsx/{site['name']}/{raw_name}/第{replicate_no}重复",
                                },
                            )
                            observation_count += 1
                        entry_count += 1

    session.commit()
    return {
        "packages": 1,
        "materials": len(material_profiles),
        "trials": len(YEAR_EFFECTS) * len(site_records),
        "entries": entry_count,
        "observations": observation_count,
    }


def _observation_values(
    *,
    material: dict[str, Any],
    site: dict[str, Any],
    year: int,
    year_effect: dict[str, float],
    treatment_code: str,
    replicate_effect: float,
) -> dict[str, float]:
    """Generate values from documented demo factors, not arbitrary random numbers."""
    nitrogen_delta = 0.0 if treatment_code == "M1" else 14.0 * material["n_response"]
    acidity_penalty = max(0.0, 5.5 - site["ph"]) * (14.0 - 8.0 * material["acid_tolerance"])
    disease_pressure = site["disease_pressure"] + year_effect["disease"]
    disease_penalty = max(0.0, disease_pressure - 3.0) * (3.3 * material["blast_susceptibility"])
    special_decline = -11.0 if material["code"] == "ME-A04" and year == 2024 else 0.0
    yield_value = material["yield"] + site["yield_effect"] + year_effect["yield"] + nitrogen_delta - acidity_penalty - disease_penalty + special_decline + replicate_effect
    height_value = material["height"] + site["height_effect"] + (0.0 if treatment_code == "M1" else 3.2 + 2.2 * material["n_response"]) + replicate_effect * 0.12
    lodging_value = material["lodging"] + (0.0 if treatment_code == "M1" else 0.75 * material["n_response"]) + max(0.0, disease_pressure - 4.0) * 0.12 + replicate_effect * 0.03
    blast_value = material["blast"] + max(0.0, disease_pressure - 3.0) * material["blast_susceptibility"] * 0.5 + replicate_effect * 0.02
    return {
        "yield_per_mu": round(yield_value, 1),
        "plant_height": round(height_value, 1),
        "thousand_grain_weight": round(material["weight"] + site["yield_effect"] * 0.015 + (0.15 if treatment_code == "M2" else 0) + replicate_effect * 0.015, 2),
        "seed_setting_rate": round(material["setting"] - max(0.0, disease_pressure - 3.0) * material["blast_susceptibility"] * 0.65 + (0.4 if treatment_code == "M2" else 0) + replicate_effect * 0.04, 1),
        "head_rice_rate": round(material["head"] - max(0.0, site["temperature"] - 24.5) * 0.45 + replicate_effect * 0.03, 1),
        "chalkiness_degree": round(max(0.5, material["chalk"] + max(0.0, site["temperature"] - 24.2) * 0.55 + replicate_effect * 0.02), 1),
        "panicle_blast_score": round(min(9.0, max(0.0, blast_value)), 1),
        "lodging_score": round(min(9.0, max(0.0, lodging_value)), 1),
    }


def _json(value: Any) -> str:
    import json

    # PostgreSQL NUMERIC columns arrive as Decimal values. Convert them to
    # ordinary JSON numbers before using the governed evidence as LLM context.
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda item: float(item) if isinstance(item, Decimal) else str(item),
    )


def _rows(session: Session, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text(statement), params or {}).mappings().all()]


def trial_demo_overview(session: Session) -> dict[str, int]:
    return {
        "packages": int(session.scalar(text("SELECT count(*) FROM trial_data_package WHERE package_code = :code"), {"code": DEMO_PACKAGE_CODE}) or 0),
        "materials": int(session.scalar(text("SELECT count(*) FROM breeding_material")) or 0),
        "trials": int(session.scalar(text("SELECT count(*) FROM field_trial WHERE package_id = (SELECT id FROM trial_data_package WHERE package_code = :code)"), {"code": DEMO_PACKAGE_CODE}) or 0),
        "entries": int(session.scalar(text("SELECT count(*) FROM trial_entry")) or 0),
        "observations": int(session.scalar(text("SELECT count(*) FROM trial_phenotype_observation")) or 0),
    }


def list_trial_demo_trials(session: Session) -> list[dict[str, Any]]:
    return _rows(session, """
        SELECT trial_code, trial_year, site_name, ecological_zone, trial_name
        FROM field_trial trial
        JOIN trial_site site ON site.id = trial.site_id
        WHERE trial.package_id = (SELECT id FROM trial_data_package WHERE package_code = :code)
        ORDER BY trial_year DESC, site_name
    """, {"code": DEMO_PACKAGE_CODE})


def trial_demo_package(session: Session) -> dict[str, Any]:
    package = _rows(session, """
        SELECT package_code, package_name, dataset_type, governance_status, description, is_simulated
        FROM trial_data_package WHERE package_code = :code
    """, {"code": DEMO_PACKAGE_CODE})
    sources = _rows(session, """
        SELECT file_name, source_role, source_format, relative_path, raw_schema_note, processing_status
        FROM trial_source_file
        WHERE package_id = (SELECT id FROM trial_data_package WHERE package_code = :code)
        ORDER BY file_name
    """, {"code": DEMO_PACKAGE_CODE})
    return {
        "package": package[0] if package else None,
        "raw_sources": sources,
        "governed_tables": [
            {"table": "field_trial", "purpose": "每年每地点的一次区域试验，承接试验设计与生态区。"},
            {"table": "trial_entry", "purpose": "某材料在某试验、某处理、某重复、某小区中的参试记录。"},
            {"table": "trial_environment_metric", "purpose": "该试验的土壤、气象、病害压力等环境观测。"},
            {"table": "trial_management_event", "purpose": "处理层面的施肥与管理事件。"},
            {"table": "trial_phenotype_observation", "purpose": "与参试记录绑定的长表观测值和原始值、方法、来源位置。"},
        ],
        "counts": trial_demo_overview(session),
    }


def same_trial_comparison(session: Session, trial_code: str, treatment_code: str) -> dict[str, Any]:
    rows = _rows(session, """
        SELECT * FROM v_trial_material_summary
        WHERE trial_code = :trial_code AND treatment_code = :treatment_code
        ORDER BY yield_per_mu DESC NULLS LAST, plant_height ASC NULLS LAST
    """, {"trial_code": trial_code, "treatment_code": treatment_code})
    if not rows:
        return {"trial_code": trial_code, "treatment_code": treatment_code, "records": [], "note": "未找到该试验与处理的已发布模拟数据。"}
    context = _rows(session, """
        SELECT metric_name, value_numeric, unit
        FROM trial_environment_metric metric
        JOIN field_trial trial ON trial.id = metric.trial_id
        WHERE trial.trial_code = :trial_code
        ORDER BY metric.metric_code
    """, {"trial_code": trial_code})
    high_yield_low_height = [item for item in rows if float(item["yield_per_mu"] or 0) >= statistics.quantiles([float(row["yield_per_mu"]) for row in rows], n=4)[2] and float(item["plant_height"] or 999) <= statistics.median([float(row["plant_height"]) for row in rows])]
    return {
        "trial_code": trial_code,
        "treatment_code": treatment_code,
        "records": rows,
        "environment": context,
        "highlight_materials": [item["material_name"] for item in high_yield_low_height],
        "interpretation": "同一试验、同一处理下比较，环境与管理条件在设计上保持一致；仍应结合重复数、区组和显著性检验后作正式结论。",
        "trace": {"package_code": DEMO_PACKAGE_CODE, "view": "v_trial_material_summary", "source_role": "材料参试、环境、管理、表型观测原始表"},
    }


def stability_analysis(session: Session, treatment_code: str = "M1") -> dict[str, Any]:
    rows = _rows(session, """
        SELECT material_code, material_name, is_check, trial_code, trial_year, site_name, ecological_zone, yield_per_mu,
               plant_height, head_rice_rate, panicle_blast_score, lodging_score
        FROM v_trial_material_summary
        WHERE treatment_code = :treatment_code
        ORDER BY material_code, trial_year, site_name
    """, {"treatment_code": treatment_code})
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["material_code"])].append(row)
    result = []
    for material_code, items in grouped.items():
        yields = [float(item["yield_per_mu"]) for item in items if item["yield_per_mu"] is not None]
        mean_yield = statistics.fmean(yields)
        std = statistics.stdev(yields) if len(yields) > 1 else 0.0
        cv = std / mean_yield * 100 if mean_yield else 0.0
        score = mean_yield - cv * 2.6 - statistics.fmean(float(item["lodging_score"]) for item in items) * 3.0 - statistics.fmean(float(item["panicle_blast_score"]) for item in items) * 2.0
        result.append({
            "material_code": material_code,
            "material_name": items[0]["material_name"],
            "is_check": bool(items[0]["is_check"]),
            "trial_count": len(items),
            "mean_yield": round(mean_yield, 2),
            "yield_std": round(std, 2),
            "yield_cv_percent": round(cv, 2),
            "min_yield": round(min(yields), 2),
            "max_yield": round(max(yields), 2),
            "mean_height": round(statistics.fmean(float(item["plant_height"]) for item in items), 2),
            "mean_head_rice_rate": round(statistics.fmean(float(item["head_rice_rate"]) for item in items), 2),
            "mean_blast_score": round(statistics.fmean(float(item["panicle_blast_score"]) for item in items), 2),
            "mean_lodging_score": round(statistics.fmean(float(item["lodging_score"]) for item in items), 2),
            "composite_score": round(score, 2),
            "site_records": items,
        })
    result.sort(key=lambda item: item["composite_score"], reverse=True)
    for rank, item in enumerate(result, start=1):
        item["rank"] = rank
    checks = [item for item in result if item["is_check"]]
    baseline = statistics.fmean(item["mean_yield"] for item in checks) if checks else 0.0
    candidates = [
        {**item, "yield_advantage_vs_checks": round(item["mean_yield"] - baseline, 2)}
        for item in result
        if not item["is_check"] and item["mean_yield"] >= baseline and item["yield_cv_percent"] <= 4.5
    ][:5]
    return {
        "treatment_code": treatment_code,
        "records": result,
        "candidate_materials": candidates,
        "check_mean_yield": round(baseline, 2),
        "method": "以每材料12个‘试验×地点’均值计算平均产量、标准差和变异系数；综合分只用于演示排序。",
        "limitation": "三年四点的模拟数据可用于演示稳定性分析流程，但正式育种决策应补充显著性、GGE/AMMI模型、试验误差和更完整多年数据。",
        "trace": {"view": "v_trial_material_summary", "filters": {"treatment_code": treatment_code}, "package_code": DEMO_PACKAGE_CODE},
    }


def _pearson(pairs: Iterable[tuple[float, float]]) -> float | None:
    values = list(pairs)
    if len(values) < 3:
        return None
    xs, ys = zip(*values)
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = math.sqrt(sum((item - mean_x) ** 2 for item in xs) * sum((item - mean_y) ** 2 for item in ys))
    return None if not denominator else sum((x - mean_x) * (y - mean_y) for x, y in values) / denominator


def environment_association_analysis(session: Session) -> dict[str, Any]:
    rows = _rows(session, """
        WITH environment AS (
            SELECT trial_id,
                max(value_numeric) FILTER (WHERE metric_code = 'soil_ph') AS soil_ph,
                max(value_numeric) FILTER (WHERE metric_code = 'available_phosphorus') AS available_phosphorus,
                max(value_numeric) FILTER (WHERE metric_code = 'disease_pressure') AS disease_pressure
            FROM trial_environment_metric GROUP BY trial_id
        )
        SELECT summary.*, environment.soil_ph, environment.available_phosphorus, environment.disease_pressure
        FROM v_trial_material_summary summary
        JOIN environment ON environment.trial_id = summary.trial_id
        WHERE summary.treatment_code = 'M1'
        ORDER BY summary.trial_year, summary.site_name, summary.material_code
    """)
    measures = [
        ("土壤 pH 与亩产", "soil_ph", "yield_per_mu"),
        ("有效磷与千粒重", "available_phosphorus", "thousand_grain_weight"),
        ("病害压力与结实率", "disease_pressure", "seed_setting_rate"),
        ("病害压力与穗瘟等级", "disease_pressure", "panicle_blast_score"),
    ]
    associations = []
    for label, left, right in measures:
        coefficient = _pearson((float(row[left]), float(row[right])) for row in rows if row[left] is not None and row[right] is not None)
        associations.append({"label": label, "correlation": round(coefficient, 3) if coefficient is not None else None, "sample_size": len(rows)})
    return {
        "records": rows,
        "associations": associations,
        "interpretation": "相关系数仅描述这批试验级观测中的共同变化，不证明土壤或环境变量造成了性状变化。正式分析需控制品种、地点、年份和管理等混杂因素。",
        "trace": {"tables": ["trial_environment_metric", "v_trial_material_summary"], "treatment_code": "M1", "package_code": DEMO_PACKAGE_CODE},
    }


def management_effect_analysis(session: Session) -> dict[str, Any]:
    rows = _rows(session, """
        SELECT material_code, material_name, is_check,
            ROUND(AVG(yield_per_mu) FILTER (WHERE treatment_code = 'M1')::numeric, 2) AS m1_yield,
            ROUND(AVG(yield_per_mu) FILTER (WHERE treatment_code = 'M2')::numeric, 2) AS m2_yield,
            ROUND(AVG(plant_height) FILTER (WHERE treatment_code = 'M1')::numeric, 2) AS m1_height,
            ROUND(AVG(plant_height) FILTER (WHERE treatment_code = 'M2')::numeric, 2) AS m2_height,
            ROUND(AVG(lodging_score) FILTER (WHERE treatment_code = 'M1')::numeric, 2) AS m1_lodging,
            ROUND(AVG(lodging_score) FILTER (WHERE treatment_code = 'M2')::numeric, 2) AS m2_lodging
        FROM v_trial_material_summary
        GROUP BY material_code, material_name, is_check
        ORDER BY material_code
    """)
    for row in rows:
        row["yield_delta"] = round(float(row["m2_yield"] or 0) - float(row["m1_yield"] or 0), 2)
        row["height_delta"] = round(float(row["m2_height"] or 0) - float(row["m1_height"] or 0), 2)
        row["lodging_delta"] = round(float(row["m2_lodging"] or 0) - float(row["m1_lodging"] or 0), 2)
        row["risk_flag"] = "高氮下倒伏风险上升" if row["lodging_delta"] >= 0.8 and row["m2_lodging"] >= 4 else "未触发演示阈值"
    return {
        "records": rows,
        "interpretation": "比较同一材料在同一组试验、相同环境下两种施氮处理的平均差异；高氮增产与株高、倒伏风险需同时呈现。",
        "limitation": "该结果来自模拟设计，正式试验应结合方差分析、施肥时期和实际田间管理记录。",
        "trace": {"tables": ["trial_treatment", "trial_management_event", "v_trial_material_summary"], "package_code": DEMO_PACKAGE_CODE},
    }


def trait_tradeoff_analysis(session: Session) -> dict[str, Any]:
    rows = _rows(session, """
        SELECT material_code, material_name, is_check,
            ROUND(AVG(yield_per_mu)::numeric, 2) AS mean_yield,
            ROUND(AVG(head_rice_rate)::numeric, 2) AS mean_head_rice_rate,
            ROUND(AVG(chalkiness_degree)::numeric, 2) AS mean_chalkiness_degree,
            ROUND(AVG(plant_height)::numeric, 2) AS mean_height,
            ROUND(AVG(lodging_score)::numeric, 2) AS mean_lodging_score
        FROM v_trial_material_summary
        WHERE treatment_code = 'M1'
        GROUP BY material_code, material_name, is_check
        ORDER BY material_code
    """)
    yield_head = _pearson((float(row["mean_yield"]), float(row["mean_head_rice_rate"])) for row in rows)
    yield_lodging = _pearson((float(row["mean_yield"]), float(row["mean_lodging_score"])) for row in rows)
    for row in rows:
        row["tradeoff_label"] = "高产且米质较优" if row["mean_yield"] >= 565 and row["mean_head_rice_rate"] >= 61 else "需结合目标性状权衡"
    return {
        "records": rows,
        "associations": [
            {"label": "平均亩产与整精米率", "correlation": round(yield_head, 3) if yield_head is not None else None},
            {"label": "平均亩产与倒伏等级", "correlation": round(yield_lodging, 3) if yield_lodging is not None else None},
        ],
        "interpretation": "用同一管理处理下的材料均值展示性状权衡；图表可帮助筛选候选材料，但不应把相关性直接解释为性状因果机制。",
        "trace": {"view": "v_trial_material_summary", "treatment_code": "M1", "package_code": DEMO_PACKAGE_CODE},
    }


def decline_evidence_analysis(session: Session, material_code: str = "ME-A04") -> dict[str, Any]:
    rows = _rows(session, """
        WITH environment AS (
            SELECT trial_id,
                max(value_numeric) FILTER (WHERE metric_code = 'soil_ph') AS soil_ph,
                max(value_numeric) FILTER (WHERE metric_code = 'available_phosphorus') AS available_phosphorus,
                max(value_numeric) FILTER (WHERE metric_code = 'disease_pressure') AS disease_pressure,
                max(value_numeric) FILTER (WHERE metric_code = 'rainfall') AS rainfall
            FROM trial_environment_metric GROUP BY trial_id
        )
        SELECT summary.*, environment.soil_ph, environment.available_phosphorus,
               environment.disease_pressure, environment.rainfall
        FROM v_trial_material_summary summary
        JOIN environment ON environment.trial_id = summary.trial_id
        WHERE summary.material_code = :material_code AND summary.treatment_code = 'M1'
        ORDER BY summary.trial_year, summary.site_name
    """, {"material_code": material_code})
    if not rows:
        return {"records": [], "evidence": [], "limitation": "未找到该材料的模拟试验记录。"}
    annual: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        annual[int(row["trial_year"])].append(row)
    annual_summary = []
    for year, values in sorted(annual.items()):
        annual_summary.append({
            "year": year,
            "mean_yield": round(statistics.fmean(float(item["yield_per_mu"]) for item in values), 2),
            "mean_disease_pressure": round(statistics.fmean(float(item["disease_pressure"]) for item in values), 2),
            "mean_soil_ph": round(statistics.fmean(float(item["soil_ph"]) for item in values), 2),
            "mean_lodging_score": round(statistics.fmean(float(item["lodging_score"]) for item in values), 2),
        })
    baseline = next((item for item in annual_summary if item["year"] == 2023), annual_summary[0])
    lowest = min(annual_summary, key=lambda item: item["mean_yield"])
    evidence = [
        {"factor": "表现变化", "evidence": f"{lowest['year']}年平均亩产 {lowest['mean_yield']} kg/亩，较基线年 {baseline['mean_yield']} kg/亩变化 {round(lowest['mean_yield'] - baseline['mean_yield'], 2)} kg/亩。", "type": "观测结果"},
        {"factor": "病害环境", "evidence": f"{lowest['year']}年同批试验的平均穗瘟病害压力为 {lowest['mean_disease_pressure']} 级；该材料同期穗瘟等级记录可回溯至每个试验与重复。", "type": "环境关联证据"},
        {"factor": "土壤环境", "evidence": f"{lowest['year']}年同批试验平均土壤 pH 为 {lowest['mean_soil_ph']}，且赣州试验点酸性更强。", "type": "环境关联证据"},
        {"factor": "管理一致性", "evidence": "本分析固定在 M1（标准施氮）处理，避免把施氮处理差异直接混入年份对比。", "type": "控制条件"},
    ]
    return {
        "material_code": material_code,
        "material_name": rows[0]["material_name"],
        "annual_summary": annual_summary,
        "records": rows,
        "evidence": evidence,
        "interpretation": "平台可把表现变差拆成材料在不同年份和地点的观测、同期环境和固定管理条件；这是证据拆解，不是对单一原因的因果判定。",
        "trace": {"tables": ["field_trial", "trial_environment_metric", "trial_treatment", "trial_entry", "trial_phenotype_observation"], "package_code": DEMO_PACKAGE_CODE},
    }


def candidate_report(session: Session) -> dict[str, Any]:
    stability = stability_analysis(session, "M1")
    tradeoff = trait_tradeoff_analysis(session)
    tradeoff_by_code = {item["material_code"]: item for item in tradeoff["records"]}
    candidates = []
    for item in stability["candidate_materials"]:
        tradeoff_row = tradeoff_by_code.get(item["material_code"], {})
        risks = []
        if float(item["mean_lodging_score"]) >= 3.5:
            risks.append("倒伏等级偏高，建议查看高氮处理差异")
        if float(item["mean_blast_score"]) >= 4.5:
            risks.append("穗瘟等级偏高，需结合病害压力与抗性鉴定复核")
        if float(tradeoff_row.get("mean_head_rice_rate") or 0) < 60:
            risks.append("整精米率相对较低，可能存在产量与加工品质权衡")
        candidates.append({
            "material_code": item["material_code"],
            "material_name": item["material_name"],
            "mean_yield": item["mean_yield"],
            "yield_advantage_vs_checks": item["yield_advantage_vs_checks"],
            "yield_cv_percent": item["yield_cv_percent"],
            "adapted_ecological_zones": sorted({record["ecological_zone"] for record in item["site_records"] if float(record["yield_per_mu"]) >= item["mean_yield"]}),
            "risks": risks or ["未触发本演示的主要风险阈值，仍需结合完整试验统计检验。"],
            "evidence_summary": f"M1处理下覆盖 {item['trial_count']} 个试验×地点均值，平均亩产 {item['mean_yield']} kg/亩，产量CV {item['yield_cv_percent']}%。",
        })
    return {
        "title": "模拟三年四点区域试验：高产稳产候选材料初筛报告",
        "is_simulated": True,
        "candidate_materials": candidates,
        "report_sections": [
            "问题与范围", "数据来源与治理路径", "候选材料与稳定性", "适应生态区", "环境与管理关联", "风险与局限", "可追溯来源",
        ],
        "limitation": "结果全部基于模拟数据，评分、阈值和候选排序仅演示平台能力。真实项目应由科研人员确认试验设计、数据质量、显著性检验与育种目标后再用于决策。",
        "trace": {"package_code": DEMO_PACKAGE_CODE, "source_files": "12份模拟原始Excel资料", "governed_tables": ["field_trial", "trial_entry", "trial_environment_metric", "trial_management_event", "trial_phenotype_observation"]},
    }


def build_trial_demo_research_evidence(session: Session, question: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Provide governed multi-environment evidence only for matching research questions."""
    normalized = question.lower()
    markers = ("区域试验", "多点", "多年", "稳定性", "生态区", "同一试验", "土壤", "施氮", "氮肥", "管理措施", "性状权衡", "表现变差", "高产稳产", "候选材料")
    if not any(marker in normalized for marker in markers):
        return None, []
    report = candidate_report(session)
    environment = environment_association_analysis(session)
    management = management_effect_analysis(session)
    payload = {
        "demo_label": "模拟三年四点水稻区域试验数据，不能作为真实育种结论",
        "candidate_report": report,
        "environment_associations": environment["associations"],
        "management_records": management["records"],
        "rules": [
            "只能根据给出的试验级数据说明结果。",
            "环境与性状关系必须表述为相关或待验证假设，不得直接宣称因果。",
            "报告中必须保留模拟数据和统计局限说明。",
        ],
    }
    card = {
        "priority": 1,
        "type": "trial_level_governed_data",
        "title": "平台已发布标准数据：模拟三年四点区域试验",
        "detail": "已联结材料、试验、环境、管理、重复和表型观测；本轮仅引用与多环境问题相关的治理结果。",
        "query_template": "trial_demo_controlled_analysis",
        "query_parameters": {"package_code": DEMO_PACKAGE_CODE},
    }
    return "平台已发布试验级治理数据（模拟数据，受控分析结果）：\n" + _json(payload), [card]
