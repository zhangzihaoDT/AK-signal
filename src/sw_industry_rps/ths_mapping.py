"""同花顺行业板块 → 申万二级 行业映射。

provisional 阶段使用同花顺 90 个行业板块的日线数据（stock_board_industry_index_ths），
通过名称映射对齐到申万二级（124 个 active）中的子集（约 78 个）。

映射规则：
  1. 优先走 THS_TO_SW 精确表（人工+自动生成，含口径近似项）。
  2. 运行时不自动猜测——未映射板块直接跳过，避免污染口径。

字段说明：
  code      申万二级代码（带 .SI）
  confidence  high=名称精确匹配；medium=口径近似（如板块更粗）
  note      映射备注
"""

from __future__ import annotations

THS_TO_SW: dict[str, dict] = {
    # ── 自动精确匹配（名称去 Ⅱ/Ⅲ 后唯一对应） ─────────────────────────
    "半导体": {"code": "801081.SI", "confidence": "high"},
    "白酒": {"code": "801125.SI", "confidence": "high"},
    "白色家电": {"code": "801111.SI", "confidence": "high"},
    "保险": {"code": "801194.SI", "confidence": "high"},
    "包装印刷": {"code": "801141.SI", "confidence": "high"},
    "厨卫电器": {"code": "801114.SI", "confidence": "high"},
    "电池": {"code": "801737.SI", "confidence": "high"},
    "电机": {"code": "801731.SI", "confidence": "high"},
    "电力": {"code": "801161.SI", "confidence": "high"},
    "电网设备": {"code": "801738.SI", "confidence": "high"},
    "多元金融": {"code": "801191.SI", "confidence": "high"},
    "电子化学品": {"code": "801086.SI", "confidence": "high"},
    "风电设备": {"code": "801736.SI", "confidence": "high"},
    "非金属材料": {"code": "801039.SI", "confidence": "high"},
    "服装家纺": {"code": "801132.SI", "confidence": "high"},
    "纺织制造": {"code": "801131.SI", "confidence": "high"},
    "工程机械": {"code": "801077.SI", "confidence": "high"},
    "光伏设备": {"code": "801735.SI", "confidence": "high"},
    "贵金属": {"code": "801053.SI", "confidence": "high"},
    "轨交设备": {"code": "801076.SI", "confidence": "high"},
    "光学光电子": {"code": "801084.SI", "confidence": "high"},
    "工业金属": {"code": "801055.SI", "confidence": "high"},
    "环保设备": {"code": "801972.SI", "confidence": "high"},
    "环境治理": {"code": "801971.SI", "confidence": "high"},
    "互联网电商": {"code": "801206.SI", "confidence": "high"},
    "黑色家电": {"code": "801112.SI", "confidence": "high"},
    "化学纤维": {"code": "801032.SI", "confidence": "high"},
    "化学原料": {"code": "801033.SI", "confidence": "high"},
    "化学制品": {"code": "801034.SI", "confidence": "high"},
    "化学制药": {"code": "801151.SI", "confidence": "high"},
    "IT服务": {"code": "801103.SI", "confidence": "high"},
    "军工电子": {"code": "801745.SI", "confidence": "high"},
    "家居用品": {"code": "801142.SI", "confidence": "high"},
    "计算机设备": {"code": "801101.SI", "confidence": "high"},
    "金属新材料": {"code": "801051.SI", "confidence": "high"},
    "教育": {"code": "801994.SI", "confidence": "high"},
    "贸易": {"code": "801202.SI", "confidence": "high"},
    "农产品加工": {"code": "801012.SI", "confidence": "high"},
    "农化制品": {"code": "801038.SI", "confidence": "high"},
    "能源金属": {"code": "801056.SI", "confidence": "high"},
    "汽车零部件": {"code": "801093.SI", "confidence": "high"},
    "其他电源设备": {"code": "801733.SI", "confidence": "high"},
    "其他电子": {"code": "801082.SI", "confidence": "high"},
    "软件开发": {"code": "801104.SI", "confidence": "high"},
    "燃气": {"code": "801163.SI", "confidence": "high"},
    "生物制品": {"code": "801152.SI", "confidence": "high"},
    "通信服务": {"code": "801223.SI", "confidence": "high"},
    "通信设备": {"code": "801102.SI", "confidence": "high"},
    "通用设备": {"code": "801072.SI", "confidence": "high"},
    "物流": {"code": "801178.SI", "confidence": "high"},
    "消费电子": {"code": "801085.SI", "confidence": "high"},
    "小家电": {"code": "801113.SI", "confidence": "high"},
    "小金属": {"code": "801054.SI", "confidence": "high"},
    "元件": {"code": "801083.SI", "confidence": "high"},
    "医疗服务": {"code": "801156.SI", "confidence": "high"},
    "医疗器械": {"code": "801153.SI", "confidence": "high"},
    "影视院线": {"code": "801766.SI", "confidence": "high"},
    "游戏": {"code": "801764.SI", "confidence": "high"},
    "医药商业": {"code": "801154.SI", "confidence": "high"},
    "养殖业": {"code": "801017.SI", "confidence": "high"},
    "自动化设备": {"code": "801078.SI", "confidence": "high"},
    "综合": {"code": "801231.SI", "confidence": "high"},
    "证券": {"code": "801193.SI", "confidence": "high"},
    "中药": {"code": "801155.SI", "confidence": "high"},
    "专用设备": {"code": "801074.SI", "confidence": "high"},
    "造纸": {"code": "801143.SI", "confidence": "high"},
    # ── 人工映射（口径近似，同花顺板块更粗） ──────────────────────────
    "房地产": {"code": "801181.SI", "confidence": "medium", "note": "THS房地产板块≈申万房地产开发"},
    "港口航运": {"code": "801992.SI", "confidence": "medium", "note": "THS港口航运≈申万航运港口"},
    "公路铁路运输": {"code": "801179.SI", "confidence": "medium", "note": "THS公路铁路运输≈申万铁路公路"},
    "机场航运": {"code": "801991.SI", "confidence": "medium", "note": "THS机场航运≈申万航空机场"},
    "煤炭开采加工": {"code": "801951.SI", "confidence": "medium", "note": "THS煤炭开采加工≈申万煤炭开采（不含焦炭）"},
    "汽车服务及其他": {"code": "801092.SI", "confidence": "medium", "note": "THS汽车服务及其他≈申万汽车服务"},
    "塑料制品": {"code": "801036.SI", "confidence": "medium", "note": "THS塑料制品≈申万塑料"},
    "食品加工制造": {"code": "801124.SI", "confidence": "medium", "note": "THS食品加工制造≈申万食品加工"},
    "石油加工贸易": {"code": "801963.SI", "confidence": "medium", "note": "THS石油加工贸易≈申万炼化及贸易"},
    "橡胶制品": {"code": "801037.SI", "confidence": "medium", "note": "THS橡胶制品≈申万橡胶"},
    "油气开采及服务": {"code": "801961.SI", "confidence": "medium", "note": "THS油气开采及服务≈申万油气开采Ⅱ"},
    "种植业与林业": {"code": "801016.SI", "confidence": "medium", "note": "THS种植业与林业≈申万种植业"},
}

# 口径语义歧义、无法单一映射的同花顺板块 → 跳过（provisional 缺省）
THS_UNMAPPED: tuple[str, ...] = (
    "钢铁", "军工装备", "建筑材料", "建筑装饰", "零售", "旅游及酒店",
    "美容护理", "汽车整车", "其他社会服务", "文化传媒", "饮料制造", "银行",
)


def lookup_sw_code(ths_board_name: str) -> str | None:
    """同花顺板块名 → 申万二级 code；未映射返回 None。"""
    entry = THS_TO_SW.get(ths_board_name)
    return entry["code"] if entry else None


def mapped_boards() -> list[str]:
    return sorted(THS_TO_SW.keys())


def mapped_sw_codes() -> set[str]:
    return {entry["code"] for entry in THS_TO_SW.values()}
