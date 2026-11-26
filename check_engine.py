"""엔진만 따로 점검 (웹 없이):  python check_engine.py [data/links_service.gpkg]
회전제한 반영 기준"""
import sys, time, pandas as pd
from engine import load_engine
gpkg = sys.argv[1] if len(sys.argv) > 1 else 'data/links_service.gpkg'
t = time.time(); E = load_engine(gpkg, 'data/facility_nodes.csv', 'data/turn_restrictions.csv')
print(f'회전제한 금지 {len(E.banned)}건 · U턴 허용 {len(E.uturn_ok)}건')
print(f'불러오기 {time.time()-t:.1f}초 · L_MEAN {E.L_MEAN:.1f} · H_MEAN {E.H_MEAN:.4f} · 탐색 노드 {len(E.cand)}개')
O, D = 1050035300, 1050121900          # od_list.csv의 OD 129
try:
    od = pd.read_csv('od_list.csv', index_col='od'); O, D = int(od.O_NODE[129]), int(od.D_NODE[129])
except FileNotFoundError:
    pass
r = E.compare(O, D)
if 'error' in r: print(r['error'])
else:
    print(f"OD 129: 일반 {r['plain'].dist_m:,.0f} m → 안전 {r['safe'].dist_m:,.0f} m "
          f"(우회 {r['detour_pct']:+.1f}%, 위험 −{r['risk_cut_pct']:.1f}%)")
f = E.facilities.iloc[0]; n, dist = E.snap(f['위도'], f['경도'])
print(f"시설 연결 확인: {f['이름']} → 노드 {n} ({dist:.0f} m), 목록 값 {f['NODE_ID']}")
