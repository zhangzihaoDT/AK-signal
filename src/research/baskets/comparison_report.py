"""Offline HTML renderer for the reviewed comparable-basket dataset."""
from __future__ import annotations
import html
import json
from pathlib import Path
from plotly.offline import get_plotlyjs

COLORS = ['#FF7A2F', '#2878A9', '#437C68', '#174A7C', '#8A6BA8', '#7F743E', '#279EAD', '#9D5965']


def _static_svg(series, dates, limits):
    lo, hi = limits
    pad = max((hi - lo) * .06, 1)
    lo, hi = lo - pad, hi + pad
    x = lambda i: 60 + i / max(len(dates) - 1, 1) * 880
    y = lambda v: 300 - (float(v) - lo) / (hi - lo) * 270
    grid = ''.join(f'<line x1="60" x2="940" y1="{y(lo+(hi-lo)*i/4):.2f}" y2="{y(lo+(hi-lo)*i/4):.2f}" stroke="#e6edf1"/><text x="50" y="{y(lo+(hi-lo)*i/4)+4:.2f}" text-anchor="end" font-size="12" fill="#556270">{lo+(hi-lo)*i/4:.0f}</text>' for i in range(5))
    lines = []
    for label, values, color, dashed in series:
        path = ' '.join(f'{"M" if i == 0 else "L"}{x(i):.2f},{y(v):.2f}' for i, v in enumerate(values))
        lines.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-dasharray="{7 if dashed else 0}"><title>{html.escape(label)}</title></path>')
    return '<svg class="chart-fallback" viewBox="0 0 960 340" role="img" aria-label="离线静态走势预览" style="width:100%;height:100%">' + grid + ''.join(lines) + f'<text x="60" y="330" font-size="12">{dates[0]}</text><text x="940" y="330" font-size="12" text-anchor="end">{dates[-1]}</text></svg>'


def render_comparison(results, receipt, output: Path):
    rows = []
    for r in results:
        m = r['metrics']
        rows.append('<tr><td>' + html.escape(r['label']) + '</td>' +
                    ''.join(f'<td>{v:+.2f}{u}</td>' for v, u in [
                        (m['return_pct'], '%'), (m['excess_pp'], ' pp'), (m['max_drawdown_pct'], '%')]) +
                    f'<td>{m["assets"]}</td></tr>')
    payload = {'dates': results[0]['nav'].index.strftime('%Y-%m-%d').tolist(),
               'benchmark': results[0]['nav'].benchmark.tolist(),
               'series': [{'key': r['key'], 'label': r['label'], 'values': r['nav'].basket.tolist(),
                           'weights': r['weights'], 'color': COLORS[i], 'short': {'hk_resources_shipping':'AZ 仓位观察','scania_china_watch':'商用车产业链','china_auto_global_ex_oem':'汽车全球化 · 剔除 OEM','ai_capex':'AI Capex','china_auto_global':'汽车全球化 · 仅 OEM','memory_and_storage':'半导体存储','auto_tier1_ai_infra':'汽车供应链 → AI 液冷','auto_tier1_embodied':'汽车供应链 → Physical AI'}[r['key']]} for i, r in enumerate(results)]}
    limits = (min(min(r['nav'].min()) for r in results), max(max(r['nav'].max()) for r in results))
    bm = ('沪深300', payload['benchmark'], '#374957', True)
    static_main = _static_svg([(r['label'], r['nav'].basket.tolist(), COLORS[i], False) for i, r in enumerate(results)] + [bm], payload['dates'], limits)
    cards = ''.join(f'<section class="card small"><h3>{html.escape(r["label"])}</h3><div id="small-{i}" class="small-chart">{_static_svg([(r["label"], r["nav"].basket.tolist(), COLORS[i], False), bm], payload["dates"], (min(r["nav"].min()), max(r["nav"].max())))}</div></section>' for i, r in enumerate(results))
    weights = []
    for r in results:
        items = ''.join('<tr><td>' + html.escape(a['name']) + f'</td><td>{html.escape(a["market"])} {html.escape(a["symbol"])}</td>' +
                        f'<td>{a["initial_weight"]*100:.2f}%</td><td>{a["first_price_date"]}</td><td>{a["contribution_pp"]:+.2f} pp</td></tr>' for a in r['weights'])
        weights.append(f'<details><summary>{html.escape(r["label"])} · 成分与初始权重</summary><div class="scroll"><table><thead><tr><th>股票</th><th>代码</th><th>初始权重</th><th>窗口首个价格日</th><th>区间贡献</th></tr></thead><tbody>{items}</tbody></table></div></details>')
    document = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>观察篮子 · 长期走势</title><style>
:root{--bg:#F8F6F0;--text:#253746;--muted:#556270;--line:#dce4e8;--accent:#FF7A2F}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}.wrap{max-width:1360px;margin:auto;padding:38px 28px 64px}.eyebrow{font-size:12px;letter-spacing:.15em;color:var(--muted)}h1{font-size:32px;margin:8px 0}h2{font-size:21px;margin:32px 0 12px}h3{font-size:15px;font-weight:600;margin:2px 10px}.muted{color:var(--muted)}.meta{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0 22px;font-variant-numeric:tabular-nums}.meta strong{display:block;font-size:22px}.card{background:white;border:1px solid var(--line);border-radius:10px;padding:20px;margin:16px 0}.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}button{background:white;border:1px solid var(--line);border-radius:6px;padding:8px 13px;color:var(--text);font:inherit;cursor:pointer}button.active{background:#e7f1f8;border-color:#2878A9}button:focus-visible{outline:2px solid #FF7A2F;outline-offset:3px}.caption{font-size:13px;color:var(--muted);margin:10px 0}.main-chart{height:560px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.small{min-width:0;margin:0;padding:14px 8px}.small-chart{height:265px}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:right;padding:12px 14px;border-bottom:1px solid var(--line);white-space:nowrap}td:first-child,th:first-child{text-align:left;white-space:normal;min-width:180px}th{background:#f0f6fa;color:var(--muted)}details{margin:12px 0;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer;font-weight:600}.note{border-left:3px solid var(--accent);padding:8px 16px;background:#fff}li{margin:8px 0}footer{font-size:12px;color:var(--muted);margin-top:28px}@media(max-width:700px){.wrap{padding:22px 14px}h1{font-size:26px}.card{padding:12px}.grid{grid-template-columns:1fr}.main-chart{height:650px}.meta{gap:18px}.meta strong{font-size:19px}}
</style><script>__PLOTLY__</script></head><body><main class="wrap"><div class="eyebrow">AKSIGNAL / RESEARCH BASKETS</div><h1>观察篮子 · 长期走势</h1><p class="muted">八个观察篮子与沪深300，从同一起点比较价格表现。</p><div class="meta"><div><span class="muted">完整研究窗口</span><strong>__START__ — __END__</strong></div><div><span class="muted">观察范围</span><strong>8 篮子 / 54 只股票</strong></div><div><span class="muted">比较交易日</span><strong>__ROWS__ 日</strong></div></div><p class="note">当前观察名单的历史重建，按初始固定权重买入持有。AZ 为研究篮子，未使用实际仓位。使用各资产本币前复权价格，不计汇率、交易费用；不宣称为经验证的含息总回报或历史可执行策略。</p><section class="card"><div class="controls" aria-label="图表区间"><button class="active" data-range="all">完整区间</button><button data-range="1y">近一年</button><button data-range="ytd">2026 年</button><button data-range="3m">近三个月</button><button id="mode">切换：超额走势</button><button id="reset">显示全部篮子</button></div><p class="caption" id="window-caption"></p><div id="main-chart" class="main-chart">__STATIC__</div><p class="caption">沪深300为深灰虚线。单击图例隐藏曲线，双击单独查看；悬停查看同一日期。切换区间后将已有篮子净值重新归一化，保持原持仓路径。</p></section><h2>完整区间比较</h2><p class="caption">下表固定为完整研究窗口，不随图表筛选改变。超额 = 篮子涨幅 − 沪深300涨幅（百分点）。沪深300同期涨幅：__BENCHMARK__。</p><section class="card scroll"><table><thead><tr><th>观察篮子</th><th>区间涨幅</th><th>相对沪深300超额</th><th>最大回撤</th><th>股票数</th></tr></thead><tbody>__TABLE__</tbody></table></section><h2>逐篮子对比</h2><p class="caption">与主图使用相同区间和口径；各小图默认展开，纵轴按该篮子与沪深300自动缩放。</p><div class="grid">__CARDS__</div><h2>计算口径与数据质量</h2><section class="card"><ul><li>子组等权、组内等权，初始权重固定；随后买入持有，权重自然漂移，不做日度或季度再平衡。</li><li>晚上市成分预留资金作零收益现金，在首个真实收盘价投入。联芸科技自2024-11-29、禾赛自2025-09-16参与；上市前没有股票价格。</li><li>禾赛代码已由02510纠正为02525。2026年7月临时柜台02983的十条实际日线补齐缺口；一拆八通过供应商复权因子处理，不制造暴跌。</li><li>以沪深300交易日展示，港股独有交易日的价格变动计入下一展示日；已核验的休市和普冉股份停牌期间沿用最近价格，仅作估值，不补造原始行情。</li><li>54只股票重新获取供应商前复权序列或复权因子，独立保存。当前供应商调整不是历史时点证据，CN/HK复权方法仍有差异，不能视为经独立验证的总回报。</li><li>原始窗口内东岳集团1条、东方海外国际2条OHLC关系异常已记录；本图只读取收盘价，未篡改供应商开高低收。跨篮子持有同一股票时分别计入各自篮子，八个篮子不是互斥投资组合。</li></ul><details><summary>数据来源与公司行动依据</summary><p>CN：AKShare 多源前复权日线（每只成功来源记入快照）；HK：新浪原始日线 × 供应商前复权因子；基准：已有沪深300指数缓存。</p><p><a href="https://www.hkexnews.hk/listedco/listconews/sehk/2025/0915/2025091501459_c.pdf">禾赛上市及代码公告</a> · <a href="https://www.hkexnews.hk/listedco/listconews/sehk/2026/0526/2026052601791_c.pdf">禾赛股份拆细公告</a> · <a href="https://static.cninfo.com.cn/finalpage/2025-12-02/1224839861.PDF">普冉股份停牌进展</a></p><p>可复核文件：comparison_nav.csv、comparison_manifest.json、各篮子_weights.csv。完整参考快照及来源：data/audits/research_baskets/20261014/adjusted_references/manifest.json。</p></details>__WEIGHTS__</section><footer>研究观察 / 静态名单重建 / 价格表现不等于真实组合收益 · 数据截至 __END__</footer></main><script id="reviewed-data" type="application/json">__DATA__</script><script>
document.querySelectorAll('.chart-fallback').forEach(el=>el.remove());const data=JSON.parse(document.getElementById('reviewed-data').textContent);let range='all',mode='nav';
const config={responsive:true,displaylogo:false,modeBarButtonsToRemove:['select2d','lasso2d']};
function seriesWindow(){const last=data.dates.at(-1);let start=data.dates[0];if(range==='ytd')start=last.slice(0,4)+'-01-01';if(range==='1y'){let d=new Date(last+'T00:00:00Z');d.setUTCFullYear(d.getUTCFullYear()-1);start=d.toISOString().slice(0,10)}if(range==='3m'){let d=new Date(last+'T00:00:00Z');d.setUTCMonth(d.getUTCMonth()-3);start=d.toISOString().slice(0,10)}let i=data.dates.findIndex(d=>d>=start);let bm=data.benchmark.slice(i).map(v=>v/data.benchmark[i]*100);let s=data.series.map(x=>({...x,values:x.values.slice(i).map((v,k)=>v/x.values[i]*100-(mode==='excess'?bm[k]:0))}));return {dates:data.dates.slice(i),bm:mode==='excess'?bm.map(()=>0):bm,s}}
function draw(){const w=seriesWindow();document.getElementById('window-caption').textContent=w.dates[0]+' — '+w.dates.at(-1)+' · '+(mode==='excess'?'区间相对沪深300超额（百分点），起点=0':'区间起点=100，篮子沿用原持仓，不重新等权');const trace=x=>({x:w.dates,y:x.values,name:x.short,mode:'lines',line:{color:x.color,width:x.key==='hk_resources_shipping'?3:2},hovertemplate:'%{y:.2f}<extra>%{fullData.name}</extra>'});const bt={x:w.dates,y:w.bm,name:'沪深300',mode:'lines',line:{color:'#374957',width:2.6,dash:'dash'},hovertemplate:'%{y:.2f}<extra>沪深300</extra>'};let narrow=document.documentElement.clientWidth<700;let layout={width:document.getElementById('main-chart').clientWidth,height:document.getElementById('main-chart').clientHeight,autosize:true,paper_bgcolor:'#fff',plot_bgcolor:'#fff',font:{family:'-apple-system, PingFang SC, sans-serif',size:13,color:'#556270'},margin:{l:62,r:20,t:15,b:narrow?230:105},hovermode:'x unified',legend:{orientation:'h',y:-.2,x:0,font:{size:12}},xaxis:{gridcolor:'#edf1f4',nticks:7,tickformat:'%Y-%m'},yaxis:{title:{text:mode==='nav'?'归一化净值':'超额（百分点）'},gridcolor:'#e6edf1'},uirevision:mode+range};Plotly.react('main-chart',[...w.s.map(trace),bt],layout,config);w.s.forEach((s,i)=>Plotly.react('small-'+i,[trace(s),bt],{...layout,width:document.getElementById('small-'+i).clientWidth,height:265,margin:{l:55,r:16,t:12,b:40},showlegend:false,yaxis:{...layout.yaxis,title:undefined,autorange:true,range:undefined},xaxis:{...layout.xaxis,nticks:4}},config))}
document.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>{range=b.dataset.range;document.querySelectorAll('[data-range]').forEach(x=>x.classList.toggle('active',x===b));draw()});document.getElementById('mode').onclick=()=>{mode=mode==='nav'?'excess':'nav';document.getElementById('mode').textContent=mode==='nav'?'切换：超额走势':'切换：净值走势';draw()};document.getElementById('reset').onclick=()=>{Plotly.restyle('main-chart',{visible:true})};window.addEventListener('resize',draw);draw();
</script></body></html>'''
    for key, value in {'__START__': receipt['start'], '__END__': receipt['end'], '__ROWS__': str(receipt['rows']),
                       '__BENCHMARK__': f'{results[0]["metrics"]["benchmark_return_pct"]:+.2f}%', '__TABLE__': ''.join(rows),
                       '__STATIC__': static_main, '__CARDS__': cards, '__WEIGHTS__': ''.join(weights), '__DATA__': json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c'),
                       '__PLOTLY__': get_plotlyjs()}.items():
        document = document.replace(key, value)
    output = Path(output)
    output.write_text(document, encoding='utf-8')
    return output
