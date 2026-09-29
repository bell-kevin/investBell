import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
build_site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_site)


class SiteTests(unittest.TestCase):
    def test_saved_grid_matches_the_dashboard_request(self):
        script = (ROOT / "static" / "app.js").read_text()
        match = re.search(r"data\.grid=\{dca:\[([^\]]*)\],va:\[([^\]]*)\],capture:\[([^\]]*)\]\}", script)
        self.assertIsNotNone(match)
        dashboard = {key: [float(value) for value in values.split(",")] for key, values in zip(build_site.GRID, match.groups())}
        self.assertEqual(dashboard, {key: [float(value) for value in values] for key, values in build_site.GRID.items()})

    def test_pages_use_relative_paths(self):
        # GitHub Pages serves the site under /investBell/, where root-relative links break.
        for page in (ROOT / "static").glob("*.html"):
            links = re.findall(r'(?:href|src)="(/[^"]*)"', page.read_text())
            self.assertEqual([link for link in links if link != "/api/source"], [], page.name)

    def test_assemble_marks_the_page_and_links_the_source(self):
        with tempfile.TemporaryDirectory() as temp:
            saved = Path(temp) / "snapshot"
            saved.mkdir()
            (saved / "manifest.json").write_text("{}")
            out = Path(temp) / "site"
            with patch.object(build_site, "SAVED", saved):
                build_site.assemble(out, "https://example.org/source")
                html = (out / "index.html").read_text()
                self.assertIn('<meta name="investbell-snapshot" content="snapshot/">', html)
                self.assertIn('href="https://example.org/source"', html)
                self.assertNotIn("/api/source", html)
                self.assertTrue((out / "snapshot" / "manifest.json").is_file())
                self.assertTrue((out / "methodology.html").is_file())
                with self.assertRaises(SystemExit):
                    build_site.assemble(out, "https://example.org/source")

    def test_committed_snapshot_has_every_cell(self):
        manifest = json.loads((build_site.SAVED / "manifest.json").read_text())
        self.assertEqual(manifest["grid"], build_site.GRID)
        names = {build_site.cell_name(dca, va, capture) for dca in build_site.GRID["dca"]
                 for va in build_site.GRID["va"] for capture in build_site.GRID["capture"]}
        self.assertEqual(len(manifest["experiments"]), len(build_site.EXPERIMENTS))
        for experiment in manifest["experiments"]:
            folder = build_site.SAVED / experiment["id"]
            self.assertEqual(len(json.loads((folder / "run.json").read_text())["grid"]), len(names))
            self.assertEqual({path.name for path in (folder / "cells").iterdir()}, names)


if __name__ == "__main__":
    unittest.main()
