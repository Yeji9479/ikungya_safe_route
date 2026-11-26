"""
생활시설 → 가장 가까운 '탐색 가능 노드' 연결 (7조 아이쿵야)
입력 : links_service.gpkg, facilities.csv
       facilities.csv 열:  이름, 종류, 그리고 좌표 (둘 중 하나)
         - 위도, 경도         (WGS84, 공공데이터 표준데이터 기본)
         - x, y               (EPSG:5186, QGIS에서 내보낸 경우)
출력 : facility_nodes.csv   (이름, 종류, NODE_ID, 연결거리_m, 연결여부)
실행 : python facility_nodes.py links_service.gpkg facilities.csv
필요 : pandas, numpy, networkx, scipy  (pyproj 불필요)
"""
import sys, sqlite3, struct
import numpy as np, pandas as pd, networkx as nx
from scipy.spatial import cKDTree

GPKG = sys.argv[1] if len(sys.argv) > 1 else 'links_service.gpkg'
FAC  = sys.argv[2] if len(sys.argv) > 2 else 'facilities.csv'
MAX_SNAP = 300      # 이 거리(m)보다 멀면 연결하지 않음

# ---------- 1. LINK 읽기 + 노드 좌표(LINK 시작·끝점) ----------
def line_ends(b):
    """gpkg 도형(WKB)에서 첫 점과 끝 점만 꺼냄 (LineString / MultiLineString)"""
    env = (b[3] >> 1) & 7; w = b[8 + [0, 32, 48, 48, 64][env]:]
    def rd(p):
        bo = '<' if w[p] == 1 else '>'; t = struct.unpack(bo + 'I', w[p+1:p+5])[0] % 1000; p += 5
        n = struct.unpack(bo + 'I', w[p:p+4])[0]; p += 4
        if t == 2:
            pts = struct.unpack(bo + f'{2*n}d', w[p:p+16*n]); return [pts[:2], pts[-2:]], p + 16*n
        out = []
        for _ in range(n):
            e, p = rd(p); out += e
        return out, p
    e, _ = rd(0); return e[0], e[-1]

con = sqlite3.connect(GPKG)
TN = pd.read_sql("select table_name from gpkg_contents where data_type='features'", con).table_name[0]
g = pd.read_sql(f'select geom, LINK_ID, F_NODE, T_NODE, LENGTH, train from "{TN}"', con); con.close()
for c in ['LINK_ID', 'F_NODE', 'T_NODE']: g[c] = g[c].astype('int64')
xy = {}
for b, f, t in zip(g.geom, g.F_NODE, g.T_NODE):
    s, e = line_ends(b); xy[f] = s; xy[t] = e

# ---------- 2. 탐색 가능 노드 = 서로 오갈 수 있는 가장 큰 도로망 ∩ 동대문구 학습 LINK 시작점 ----------
G = nx.DiGraph(); G.add_edges_from(zip(g.F_NODE, g.T_NODE))
comp = max(nx.strongly_connected_components(G), key=len)
cand = np.array(sorted(set(g.loc[g.train == 1, 'F_NODE']) & comp))
P = np.array([xy[n] for n in cand]); tree = cKDTree(P)
print(f'탐색 가능 노드 {len(cand)}개')

# ---------- 3. 시설 좌표 → EPSG:5186 (pyproj 없이 직접 변환) ----------
def to5186(lat, lon):
    """WGS84/GRS80 위경도 → EPSG:5186 (Korea 2000 Central Belt 2010), 횡메르카토르 공식"""
    a=6378137.0; f=1/298.257222101; e2=f*(2-f); ep2=e2/(1-e2)
    k0=1.0; lat0=np.radians(38.0); lon0=np.radians(127.0); FE=200000.0; FN=600000.0
    phi=np.radians(np.asarray(lat,float)); lam=np.radians(np.asarray(lon,float))
    def M(p): return a*((1-e2/4-3*e2**2/64-5*e2**3/256)*p-(3*e2/8+3*e2**2/32+45*e2**3/1024)*np.sin(2*p)
                        +(15*e2**2/256+45*e2**3/1024)*np.sin(4*p)-(35*e2**3/3072)*np.sin(6*p))
    N=a/np.sqrt(1-e2*np.sin(phi)**2); T=np.tan(phi)**2; C=ep2*np.cos(phi)**2; A=(lam-lon0)*np.cos(phi)
    x=FE+k0*N*(A+(1-T+C)*A**3/6+(5-18*T+T**2+72*C-58*ep2)*A**5/120)
    y=FN+k0*(M(phi)-M(lat0)+N*np.tan(phi)*(A**2/2+(5-T+9*C+4*C**2)*A**4/24+(61-58*T+T**2+600*C-330*ep2)*A**6/720))
    return x,y

f = pd.read_csv(FAC, encoding='utf-8-sig')
if {'x', 'y'} <= set(f.columns):
    X = f[['x', 'y']].to_numpy(float)
elif {'위도', '경도'} <= set(f.columns):
    X = np.column_stack(to5186(f['위도'].to_numpy(float), f['경도'].to_numpy(float)))
else:
    sys.exit('facilities.csv에 (위도, 경도) 또는 (x, y) 열이 있어야 합니다.')

# ---------- 4. 가장 가까운 노드 연결 ----------
dist, idx = tree.query(X)
f['NODE_ID'] = cand[idx]; f['연결거리_m'] = dist.round(1)
f['연결여부'] = np.where(dist <= MAX_SNAP, '연결', f'제외({MAX_SNAP}m 초과)')
f['x5186'], f['y5186'] = X[:, 0].round(1), X[:, 1].round(1)
f.to_csv('facility_nodes.csv', index=False, encoding='utf-8-sig')
print(f.groupby(['종류', '연결여부']).size().to_string())
print(f'연결거리 중앙값 {np.median(dist):.0f} m / 최대 {dist.max():.0f} m')
print('저장: facility_nodes.csv')
