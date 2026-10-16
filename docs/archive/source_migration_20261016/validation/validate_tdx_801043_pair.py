"""Read-only reproduction of the decisive identity evidence for 801043 / 881062.
Only downloads the public configuration bundle; no price queries or production writes.
Requires temporary tdxpy under /private/tmp/aksignal-tdx-validation.
The earlier K-line compatibility attempts failed and were discontinued once
881062 was identified as a TDX research-industry index, rather than SW2021.
"""
import sys,zipfile,io,json
sys.path.append('/private/tmp/aksignal-tdx-validation')
from tdxpy.hq import TdxHq_API
api=TdxHq_API(auto_retry=False,raise_exception=True)
try:
 api.connect('180.153.18.170',7709,time_out=4); chunks=[];size=0
 for _ in range(60):
  r=api.get_report_file('zhb.zip',size);b=r.get('chunkdata',b'');chunks.append(b);size+=len(b)
  if len(b)<30000:break
 assert len(b)<30000
 z=zipfile.ZipFile(io.BytesIO(b''.join(chunks)))
 evidence={'source':'180.153.18.170:7709/zhb.zip','bytes':size,'classifications':[],'config_index':[]}
 for name in ['incon.dat','tdxzs.cfg','tdxzs3.cfg']:
  data=z.read(name)
  print(name,'bytes',len(data),'head_hex',data[:16].hex())
  enc='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'gb18030'
  text=data.decode(enc,errors='replace')
  section=''
  for line in text.splitlines():
   if line.startswith('#') or line.startswith('['):section=line
   if any(k in line for k in ['881062','801043','X1301','230300|','冶钢原料']):
    row={'file':name,'section':section,'line':line}
    print(json.dumps(row,ensure_ascii=False));evidence['classifications' if name=='incon.dat' else 'config_index'].append(row)
 with open('/private/tmp/tdx_pair_identity_config.json','w') as f:json.dump(evidence,f,ensure_ascii=False,indent=2)
finally:api.disconnect()
