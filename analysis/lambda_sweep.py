"""
안전경로 λ 실험 재현 코드 (7조 아이쿵야)
입력 : links_finall.gpkg  (4,934개 LINK, risk_h·risk_grade·train·accident_2025 포함)
출력 : od_list.csv (OD 300쌍), lambda_results.csv (λ별 집계), od_routes.csv (OD×λ별 경로 지표)

실행 : python lambda_sweep.py links_finall.gpkg [od_list.csv]
       같은 폴더에 od_list.csv가 있으면 그 OD 300쌍을 그대로 씀(팀 공통 기준).
       없을 때만 새로 뽑아 저장함.
필요 : pandas, numpy, networkx  (geopandas는 필요 없음)
"""
import sys, os, sqlite3
import numpy as np, pandas as pd, networkx as nx

GPKG  = sys.argv[1] if len(sys.argv) > 1 else 'links_finall.gpkg'
OD_CSV = sys.argv[2] if len(sys.argv) > 2 else 'od_list.csv'
LAMS  = [0, 0.1, 0.25, 0.5, 0.6, 0.75, 0.9, 1, 1.5, 2, 4, 8, 16]
SEED, N_OD, D_MIN, D_MAX = 42, 300, 1000, 4000      # OD: 최단거리 1~4km

# ---------- 1. LINK 표 읽기 (도형은 쓰지 않음) ----------
def read_links(path):
    # gpkg는 SQLite 파일이라 geopandas 없이 표만 바로 읽음 (도형은 쓰지 않음)
    if not os.path.exists(path):
        sys.exit(f'파일을 찾을 수 없습니다: {path}  (현재 폴더: {os.getcwd()})')
    con = sqlite3.connect(path)
    meta = pd.read_sql("select table_name from gpkg_contents where data_type = 'features'", con)
    tn = meta.table_name[0]
    df = pd.read_sql(f'select * from "{tn}"', con)
    con.close()
    return df.drop(columns=[c for c in ['geom', 'geometry'] if c in df.columns])

g = read_links(GPKG)
# 노드 ID를 반드시 정수로: 문자로 읽히면 정렬 순서가 달라져 OD가 다르게 뽑힘
for c in ['F_NODE', 'T_NODE', 'LINK_ID']:
    g[c] = g[c].astype('int64')
g = g.sort_values('LINK_ID').reset_index(drop=True)   # 파일 행 순서와 무관하게
assert len(g) == 4934 and g['risk_h'].notna().all(), '4,934개 전체 LINK에 risk_h가 있어야 합니다'

# ---------- 2. 정규화 상수: 학습 LINK(train=1, 2,183개) 평균 ----------
tr = g[g['train'] == 1]
L_MEAN = tr['LENGTH'].mean()      # ≈ 80.0 m
H_MEAN = tr['risk_h'].mean()      # ≈ 0.2095
print(f'L_MEAN={L_MEAN:.1f}  H_MEAN={H_MEAN:.4f}')

# ---------- 3. 방향 그래프 (F_NODE → T_NODE, 일방통행 반영) ----------
G = nx.DiGraph()
for r in g.itertuples():
    e = dict(L=r.LENGTH, cd=r.LENGTH / L_MEAN, cr=r.risk_h / H_MEAN, h=r.risk_h,
             hi=int(r.risk_grade == 5), a25=int(r.accident_2025))
    # 같은 노드 쌍에 LINK가 2개 이상이면 더 짧은 것 하나만 남김
    if G.has_edge(r.F_NODE, r.T_NODE) and G[r.F_NODE][r.T_NODE]['cd'] <= e['cd']:
        continue
    G.add_edge(r.F_NODE, r.T_NODE, **e)

# 서로 오갈 수 있는 가장 큰 연결 덩어리 안의 노드만 OD 후보로 사용
comp = max(nx.strongly_connected_components(G), key=len)
cand = sorted(set(tr['F_NODE']) & comp)      # 동대문구 학습 LINK의 시작 노드

# ---------- 4. OD 300쌍: 공통 파일이 있으면 그대로 사용 ----------
if os.path.exists(OD_CSV):
    od = pd.read_csv(OD_CSV, index_col='od')
    ods = [(int(s), int(t), d) for s, t, d in zip(od['O_NODE'], od['D_NODE'], od['최단거리_m'])]
    print(f'OD 목록 불러옴: {OD_CSV} ({len(ods)}쌍)')
else:
    rng = np.random.default_rng(SEED)
    ods = []
    while len(ods) < N_OD:
        s, t = rng.choice(cand, 2, replace=False)
        d0 = nx.dijkstra_path_length(G, s, t, weight='L')
        if D_MIN <= d0 <= D_MAX:
            ods.append((int(s), int(t), round(d0, 1)))
    od = pd.DataFrame(ods, columns=['O_NODE', 'D_NODE', '최단거리_m'])
    od.index.name = 'od'
    od.to_csv(OD_CSV, encoding='utf-8-sig')
    print(f'OD 목록 새로 만듦: {OD_CSV}')
# OD 목록 확인용 지문: 팀원끼리 이 숫자가 같아야 같은 OD를 쓴 것
print('OD 지문 =', sum(s * (i + 1) + t for i, (s, t, _) in enumerate(ods)) % 1_000_003)

# ---------- 5. λ별 경로 탐색: Cost = L/L_MEAN + λ × risk_h/H_MEAN ----------
rows = []
for k, (s, t, _) in enumerate(ods):
    for lam in LAMS:
        path = nx.dijkstra_path(G, s, t, weight=lambda u, v, d: d['cd'] + lam * d['cr'])
        ed = [G[u][v] for u, v in zip(path[:-1], path[1:])]
        rows.append(dict(od=k, lam=lam,
                         거리_m=sum(e['L'] for e in ed),          # 경로 길이
                         누적위험=sum(e['h'] for e in ed),        # Σ risk_h
                         고위험LINK=sum(e['hi'] for e in ed),     # 위험 5등급 LINK 수
                         사고25LINK=sum(e['a25'] for e in ed)))   # 2025 사고 LINK 수
R = pd.DataFrame(rows)

# ---------- 6. 집계: OD마다 λ=0(일반경로) 대비 비율을 구한 뒤 평균·중앙값 ----------
base = R[R.lam == 0].set_index('od')[['거리_m', '누적위험', '고위험LINK', '사고25LINK']]
R = R.join(base, on='od', rsuffix='_0')
R['우회율%']   = (R['거리_m'] / R['거리_m_0'] - 1) * 100
R['위험감소%'] = (1 - R['누적위험'] / R['누적위험_0']) * 100
R.to_csv('od_routes.csv', index=False, encoding='utf-8-sig')

S = R.groupby('lam').agg(우회율_평균=('우회율%', 'mean'), 우회율_중앙=('우회율%', 'median'),
                         위험감소_평균=('위험감소%', 'mean'), 위험감소_중앙=('위험감소%', 'median'),
                         고위험LINK_평균=('고위험LINK', 'mean'), 사고25LINK_평균=('사고25LINK', 'mean'),
                         총거리_km=('거리_m', lambda x: x.sum() / 1000), 총사고25LINK=('사고25LINK', 'sum'))
# 고위험 LINK·2025 사고: 300쌍 평균 개수를 λ=0과 비교
S['고위험LINK_감소%'] = (1 - S['고위험LINK_평균'] / S.loc[0, '고위험LINK_평균']) * 100
# 2025 사고 LINK: 경로당 평균 개수를 λ=0과 비교 (보고에 쓰는 지표)
S['사고25_경로당_감소%'] = (1 - S['사고25LINK_평균'] / S.loc[0, '사고25LINK_평균']) * 100
# 참고용: 1km당 (우회로 거리가 늘면 부풀어 보이므로 보고에는 쓰지 않음)
S['사고25_per_km']    = S['총사고25LINK'] / S['총거리_km']
S['사고25_km당_감소%'] = (1 - S['사고25_per_km'] / S.loc[0, '사고25_per_km']) * 100
# 선택 기준: 우회율(평균) 1%p 늘 때 위험감소(평균)가 몇 %p 늘었나
S['한계효율'] = S['위험감소_평균'].diff() / S['우회율_평균'].diff()
eff = S['한계효율'].dropna()
pick = eff[eff >= 1].index.max()
S.round(3).to_csv('lambda_results.csv', encoding='utf-8-sig')

print(S[['우회율_평균', '위험감소_평균', '고위험LINK_감소%', '사고25_경로당_감소%', '한계효율']].round(2).to_string())
print(f'\n선택 λ (한계효율 ≥ 1인 마지막 λ) = {pick}')
