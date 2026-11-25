import hashlib
import json
import os

from dashboards import update_catalog


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

    daily = root / "outputs/observation/observation_20261123.html"
    daily.parent.mkdir(parents=True)
    daily.write_text("daily report", encoding="utf-8")
    publication = root / "outputs/run_day/publication.json"
    publication.parent.mkdir(parents=True)
    publication.write_text(json.dumps({"trade_date": "20261123"}), encoding="utf-8")

    basket_folder = root / "data/audits/baskets"
    basket_folder.mkdir(parents=True)
    basket_report = basket_folder / "basket_compare.html"
    basket_report.write_text("basket report", encoding="utf-8")
    basket_manifest = basket_folder / "comparison_manifest.json"
    basket_manifest.write_text(json.dumps({"end": "2026-11-17"}), encoding="utf-8")
    pointer = root / "data/hub/basket_comparison.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({
        "directory": "data/audits/baskets",
        "report_sha256": _sha(basket_report),
        "manifest_sha256": _sha(basket_manifest),
    }), encoding="utf-8")

    ic500 = root / "data/audits/ic500_trend/ic500_trend_full.html"
    ic500.parent.mkdir(parents=True)
    ic500.write_text("<p>数据截至 2026-11-23</p>", encoding="utf-8")

    ashape = root / "data/audits/a_shape_v2/official-20261123"
    ashape.mkdir(parents=True)
    (ashape / "a_shape_v2_report.html").write_text("ashape report", encoding="utf-8")
    (ashape / "receipt.json").write_text(json.dumps({"indices": {"000905": {"end": "2026-11-20"}}}), encoding="utf-8")

    for path, timestamp in (
        (basket_report, 1_700_000_100),
        (daily, 1_700_000_400),
        (csi_report, 1_700_000_300),
        (latest_report, 1_700_000_200),
        (ic500, 1_700_000_500),
        (ashape / "a_shape_v2_report.html", 1_700_000_600),
    ):
        os.utime(path, (timestamp, timestamp))

    update_catalog.main()

    html = catalog_path.read_text(encoding="utf-8")
    assert "../data/audits/holdings_dashboard/20261124/holdings_daily.html" in html
    assert "../data/audits/holdings_dashboard/20261123/holdings_daily.html" not in html
    assert "2026-11-24" in html
    assert "file://" not in html
    assert "https://" not in html
    assert "holdings_daily.html" in html
    assert "csi500_price_channel.html" in html
    assert "../outputs/observation/observation_20261123.html" in html
    assert "../data/audits/baskets/basket_compare.html" in html
    assert "../data/audits/ic500_trend/ic500_trend_full.html" in html
    assert "../data/audits/a_shape_v2/official-20261123/a_shape_v2_report.html" in html
    assert "发布暂停" in html
    assert "2026-11-17" in html
    assert "2026-11-20" in html
    assert "grid-template-columns: repeat(3" in html
    assert "按数据截止日降序" in html
    assert 'rel="icon"' in html
    assert "%23C0392B" in html
    order = [
        "csi500-price-channel",
        "holdings-dashboard",
        "ic500-trend-full",
        "market-facts-daily",
        "a-shape-v2",
        "basket-compare-v2",
    ]
    positions = [html.index(f'id="{item}"') for item in order]
    assert positions == sorted(positions)
