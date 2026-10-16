"""Read-only TDX identity probe. Dependencies are isolated under /private/tmp/aksignal-tdx-validation.
No credential, production writer, Daily Pipeline, or price substitution is used.
Reproduction writes only temporary JSON evidence under /private/tmp.
Run using project .venv/bin/python. SDKs: pytdx 1.72, tdxpy 0.2.7.
"""


def preflight():
    import sys,json,time
    sys.path.append('/private/tmp/aksignal-tdx-validation')
    from pytdx.hq import TdxHq_API
    from pytdx.exhq import TdxExHq_API
    records=[]
    for kind,hosts in [('standard',[('180.153.18.170',7709),('218.85.139.19',7709),('202.108.253.139',80)]),('extended',[('61.152.107.141',7727),('121.14.110.210',7727)])]:
     for ip,port in hosts:
      api=(TdxHq_API if kind=='standard' else TdxExHq_API)(auto_retry=False,raise_exception=True)
      t=time.monotonic();record={'kind':kind,'host':ip,'port':port}
      try:
       connected=api.connect(ip,port,time_out=4)
       record['connected']=bool(connected)
       if connected:
        if kind=='extended':
         record['markets']=api.get_markets()
         record['instrument_count']=api.get_instrument_count()
         record['first_page']=api.get_instrument_info(0,100)
        else:
         record['counts']={str(m):api.get_security_count(m) for m in [0,1]}
         record['first_pages']={str(m):api.get_security_list(m,0) for m in [0,1]}
       record['status']='SUCCESS' if connected else 'CONNECTION_FAILED'
      except Exception as exc:
       record['status']='SOURCE_ERROR';record['exception_type']=type(exc).__name__;record['summary']=str(exc)[:200]
      finally:
       api.disconnect();record['elapsed_s']=round(time.monotonic()-t,3);records.append(record)
       print(json.dumps({k:v for k,v in record.items() if k not in ['markets','first_page','first_pages']},ensure_ascii=False),flush=True)
      if record['status']=='SUCCESS':break
    with open('/private/tmp/aksignal_tdx_probe.json','w') as f:json.dump(records,f,ensure_ascii=False,indent=2)


def identity():
    import sys,json,time
    sys.path.append('/private/tmp/aksignal-tdx-validation')
    from tdxpy.hq import TdxHq_API
    api=TdxHq_API(auto_retry=False,raise_exception=True)
    out={'library':'tdxpy 0.2.7','host':'180.153.18.170','port':7709,'markets':[]}
    t=time.monotonic()
    try:
     out['connected']=bool(api.connect(out['host'],out['port'],time_out=4))
     for market in [0,1]:
      item={'market':market};out['markets'].append(item)
      try:
       count=api.get_security_count(market);item['count']=count
       first=api.get_security_list(market,0);item['first_page']=first
       print('market',market,'count',count,'first_page',len(first),flush=True)
       if not first or any(not str(r['code']).isdigit() for r in first):
        item['status']='INVALID_METADATA';continue
       rows=first
       for start in range(len(first),count,len(first)):
        page=api.get_security_list(market,start)
        if not page:break
        rows.extend(page)
       item['rows']=rows;item['status']='SUCCESS';print('metadata_rows',market,len(rows),flush=True)
      except Exception as e:
       item.update(status='SOURCE_ERROR',exception_type=type(e).__name__,summary=str(e),original_type=type(getattr(e,'original_exception',None)).__name__,original_summary=str(getattr(e,'original_exception',''))[:200])
       print({k:v for k,v in item.items() if k not in ['rows','first_page']},flush=True)
    finally:
     api.disconnect();out['elapsed_s']=round(time.monotonic()-t,3)
     with open('/private/tmp/aksignal_tdx_identity.json','w') as f:json.dump(out,f,ensure_ascii=False,indent=2)


def config_mapping():
    import sys,json,time,zipfile,io
    sys.path.append('/private/tmp/aksignal-tdx-validation')
    from tdxpy.hq import TdxHq_API
    api=TdxHq_API(auto_retry=False,raise_exception=True);out=[]
    try:
     api.connect('180.153.18.170',7709,time_out=4)
     for filename in ['tdxzs.cfg','zhb.zip']:
      t=time.monotonic();r={'file':filename};out.append(r)
      try:
       chunks=[];size=0
       for _ in range(400):
        response=api.get_report_file(filename,size)
        data=response.get('chunkdata',b'');chunks.append(data);size+=len(data)
        if len(data)<30000:break
       raw=b''.join(chunks);r['bytes']=len(raw);r['complete']=len(data)<30000
       if filename.endswith('.zip') and raw and r['complete']:
        z=zipfile.ZipFile(io.BytesIO(raw));r['members']=z.namelist()
        for name in z.namelist():
         if name.lower().endswith(('tdxzs.cfg','incon.dat')):
          txt=z.read(name).decode('gb18030',errors='replace')
          r[name]=[line for line in txt.splitlines() if any(w in line for w in ['SWHY','申万','801012','881123','881062','881087','881294','881019','化学纤维','农产品','通用设备','冶钢原料','金属新材'])][:40]
       elif raw:
        txt=raw.decode('gb18030',errors='replace')
        r['matching_lines']=[line for line in txt.splitlines() if any(w in line for w in ['SWHY','申万','801012','881123','881062','881087','881294','881019'])][:40]
      except Exception as e:r.update(exception_type=type(e).__name__,summary=str(e)[:150])
      r['elapsed_s']=round(time.monotonic()-t,3);print(json.dumps(r,ensure_ascii=False),flush=True)
    finally:
     api.disconnect()
     with open('/private/tmp/aksignal_tdx_config.json','w') as f:json.dump(out,f,ensure_ascii=False,indent=2)



if __name__ == '__main__':
    preflight()
    identity()
    config_mapping()
