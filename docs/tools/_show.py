import sys; sys.path.insert(0,'docs/tools'); sys.path.insert(0,'.')
from dictlib import load_model
m=load_model(); T={t['name']:t for t in m['tables']}
std={'id','created_at','created_by','updated_at','updated_by','deleted_at','deleted_by'}
for n in sys.argv[1:]:
    t=T.get(n)
    if not t: print('NOTABLE',n); continue
    print(f"## {n} [{t['module']}/{t['kind']}/{t['volume']}/{t['delete_policy']}] ttl={t.get('ttl')}")
    print('  cols:', ', '.join(f"{c['name']}:{c['type']}"+('?' if c.get('null') else '') for c in t['columns'] if c['name'] not in std))
    for c in t['constraints']:
        if c['type']=='unique': print('  UQ',c['name'],c['columns'])
    for i in t['indexes']:
        print('  IX',i['name'],i['columns'],'HASH%s'%i.get('bucket_count') if i.get('hash') else '', 'UNIQUE' if i.get('unique') else '', 'GIN' if i.get('inverted') else '', 'WHERE '+i['where'] if i.get('where') else '', 'STORING %s'%i['storing'] if i.get('storing') else '')
