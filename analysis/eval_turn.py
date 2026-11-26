"""회전제한 반영 전후 OD 300쌍 비교 (저장소 최상위 폴더에서 실행)
   python analysis/eval_turn.py <검증용 links_finall.gpkg>"""
import sys, os; sys.path.insert(0, os.getcwd())
import pandas as pd, numpy as np, sqlite3, time
from engine import load_engine
G=sys.argv[1] if len(sys.argv)>1 else 'links_finall.gpkg'
E=load_engine(G); E.set_turns(pd.read_csv('data/turn_restrictions.csv'))
a25=pd.read_sql('select LINK_ID,accident_2025 from links_risk__links_final',sqlite3.connect(G))
a25=a25.set_index(a25.LINK_ID.astype('int64')).accident_2025.reindex(E.L.LINK_ID).fillna(0).to_numpy()
od=pd.read_csv('data/od_list.csv',index_col='od')
def viol(idx):
    """경로가 TURNINFO 금지 회전·무단 U턴을 몇 번 하는지"""
    b=u=0; appr=idx[0]
    for nx in idx[1:]:
        if E.internal[nx]: continue
        if (appr,nx) in E.banned: b+=1
        elif E._is_uturn(appr,nx) and (appr,nx) not in E.uturn_ok: u+=1
        appr=nx
    return b,u
rows=[]; t=time.time()
for k,r in od.iterrows():
    o,d=int(r.O_NODE),int(r.D_NODE)
    for mode in ['기존','TURNINFO','TURNINFO+U턴엄격']:
        for lam in [0,0.75]:
            if mode=='기존':
                R=E.route(o,d,lam)
            else:
                ban=E.banned
                R=E.route_turn(o,d,lam,strict_uturn=(mode!='TURNINFO'))
            if R is None: rows.append(dict(od=k,mode=mode,lam=lam,ok=0)); continue
            b,u=viol(R.links)
            rows.append(dict(od=k,mode=mode,lam=lam,ok=1,dist=R.dist_m,risk=R.risk,hi=R.high,a25=a25[R.links].sum(),ban=b,ut=u))
df=pd.DataFrame(rows); df.to_csv('analysis/results/turn_eval_routes.csv',index=False)
print('sec',round(time.time()-t,1))
print(df.groupby(['mode','lam']).agg(ok=('ok','sum'),dist=('dist','mean'),risk=('risk','mean'),hi=('hi','mean'),a25=('a25','mean'),
      ban_routes=('ban',lambda s:(s>0).sum()),ut_routes=('ut',lambda s:(s>0).sum())).round(3))
w=df.pivot_table(index=['od','mode'],columns='lam',values=['dist','risk','hi','a25','ok'])
s=[]
for mode in ['기존','TURNINFO','TURNINFO+U턴엄격']:
    x=w.xs(mode,level='mode'); x=x[(x[('ok',0)]==1)&(x[('ok',0.75)]==1)]
    s.append(dict(mode=mode,n=len(x),우회율=(x[('dist',0.75)]/x[('dist',0)]-1).mean()*100,
      누적위험감소=(1-x[('risk',0.75)]/x[('risk',0)]).mean()*100,
      고위험감소=(1-x[('hi',0.75)].mean()/x[('hi',0)].mean())*100,
      사고25감소=(1-x[('a25',0.75)].mean()/x[('a25',0)].mean())*100,
      일반경로평균거리=x[('dist',0)].mean()))
S=pd.DataFrame(s).round(2); print(S.to_string()); S.to_csv('analysis/results/turn_eval_summary.csv',index=False)
