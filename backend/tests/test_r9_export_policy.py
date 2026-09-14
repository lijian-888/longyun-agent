import inspect
import unittest
from pathlib import Path

from app import breeding_dossier, breeding_intelligence, research_report, sandbox_artifacts


class R9ExportPolicyTests(unittest.TestCase):
    def test_business_builders_do_not_offer_disable_watermark_switch(self):
        builders = (
            breeding_intelligence.build_intelligence_pdf,
            breeding_intelligence.recommendation_csv,
            research_report.build_research_report_pdf,
            research_report.build_analysis_chart_png,
            breeding_dossier.build_breeding_report_pdf,
            sandbox_artifacts.build_trial_ledger_xlsx,
            sandbox_artifacts.build_project_application_draft_pdf,
        )
        for builder in builders:
            parameters = inspect.signature(builder).parameters
            self.assertFalse({"watermark", "disable_watermark", "unwatermarked"} & set(parameters), builder.__name__)

    def test_backend_has_no_downloadable_file_response_bypass(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
        file_response_lines = [line.strip() for line in source.splitlines() if "return FileResponse(" in line]
        self.assertEqual(file_response_lines, [
            'return FileResponse(path, media_type=media_type, headers={"Content-Disposition": "inline"})',
        ])

    def test_browser_exports_fail_closed_through_server_contract(self):
        frontend = Path(__file__).resolve().parents[2] / "frontend" / "src"
        app_source = (frontend / "App.jsx").read_text(encoding="utf-8")
        assistant_source = (frontend / "ResearchAssistant.jsx").read_text(encoding="utf-8")
        self.assertIn('/api/artifacts/compliance', app_source)
        self.assertIn('已阻止无水印导出', app_source)
        self.assertIn('/api/artifacts/compliance', assistant_source)
        self.assertIn('已阻止无水印导出', assistant_source)


if __name__ == "__main__":
    unittest.main()
