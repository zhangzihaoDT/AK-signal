import requests,zipfile,io,json,csv,concurrent.futures,datetime,hashlib,pathlib
codes=[r['tdx_code'] for r in csv.DictReader(open('docs/validation/tdx_research_industry_universe_20261016.csv')) if len(r['taxonomy_key'])==5]
def check(day):
 u=f'https://www.tdx.com.cn/products/data/data/g3day/{day}.zip';r=requests.get(u,timeout=60);r.raise_for_status();z=zipfile.ZipFile(io.BytesIO(r.content)); cod=b''.join(z.read(n) for n in z.namelist() if n.endswith('.cod')); found=[c for c in codes if c.encode() in cod]
 return dict(date=day,url=u,status=r.status_code,bytes=len(r.content),sha256=hashlib.sha256(r.content).hexdigest(),crc_ok=z.testzip() is None,code_strings_found=len(found),last_modified=r.headers.get('Last-Modified'),members=z.namelist())
results=list(concurrent.futures.ThreadPoolExecutor(max_workers=3).map(check,['20261009','20261012','20261013','20261014','20261015']))
p=pathlib.Path('docs/validation/tdx_package_20261016/evidence');(p/'dated_archive_checks.json').write_text(json.dumps(dict(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),checks=results,limitation='Retrospective accessibility and code-string presence only; does not validate MDT prices or establish daily publication SLA.'),ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False,indent=2))
