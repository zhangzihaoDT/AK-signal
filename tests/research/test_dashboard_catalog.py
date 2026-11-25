import json

from dashboards import update_catalog


def test_catalog_uses_latest_local_report_paths_and_metadata(tmp_path, monkeypatch):
    root = tmp_path
    catalog_path = root / "dashboards/index.html"
    catalog_path.parent.mkdir(parents=True)
    monkeypatch.setattr(update_catalog, "ROOT", root)
    monkeypatch.setattr(update_catalog, "CATALOG", catalog_path)

    csi_report = root / "data/audits/a_share_index_technical_analysis/csi500_price_channel.html"
    csi_report.parent.mkdir(parents=True)
    csi_report.write_text("local csi report", encoding="utf-8")
    sources_path = root / "scripts/exploration/a_share_index_technical_analysis/sources.json"
    sources_path.parent.mkdir(parents=True)
    sources_path.write_text(json.dumps({"indices": {
        "000905": {"end": "2026-11-24"},
        "000852": {"end": "2026-11-24"},
    }}), encoding="utf-8")

    old_report = root / "data/audits/holdings_dashboard/20261123/holdings_daily.html"
    latest_report = root / "data/audits/holdings_dashboard/20261124/holdings_daily.html"
    old_report.parent.mkdir(parents=True)
    latest_report.parent.mkdir(parents=True)
    old_report.write_text("old", encoding="utf-8")
    latest_report.write_text("latest", encoding="utf-8")

    update_catalog.main()

    html = catalog_path.read_text(encoding="utf-8")
    assert "../data/audits/holdings_dashboard/20261124/holdings_daily.html" in html
    assert "../data/audits/holdings_dashboard/20261123/holdings_daily.html" not in html
    assert "2026-11-24" in html
    assert "file://" not in html
    assert "https://" not in html
    assert "holdings_daily.html" in html
    assert "csi500_price_channel.html" in html
