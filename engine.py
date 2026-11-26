"""
경로 엔진 (7조 아이쿵야) — 웹 화면과 분리된 계산 모듈
  load_engine(gpkg, facilities_csv) → Engine
  Engine.snap(lat, lon)             → 가장 가까운 탐색 가능 노드
  Engine.compare(o, d, lam)         → 일반경로(λ=0)·안전경로(λ) 결과와 비교 지표
  회전제한: turn_csv(ITS TURNINFO에서 우리 도로망만 남긴 표)를 주면
           좌회전·직진·우회전 금지 등을 지키는 LINK 단위 탐색으로 바뀜 (두 경로 모두 같은 규칙)
비용식: Cost = L / L_MEAN + λ × risk_h / H_MEAN   (L_MEAN, H_MEAN = 학습 LINK 평균)
필요: pandas, numpy, networkx, scipy  (geopandas·pyproj 불필요)
"""
from __future__ import annotations   # 파이썬 3.8에서도 동작하도록
import sqlite3, struct
from dataclasses import dataclass, field
import numpy as np, pandas as pd, networkx as nx
from scipy.spatial import cKDTree

MAX_SNAP_M = 300          # 클릭 지점과 노드가 이보다 멀면 '범위 밖'

# ---------- 좌표 변환: EPSG:5186 (GRS80 횡메르카토르) ↔ WGS84 ----------
_A = 6378137.0; _F = 1 / 298.257222101; _E2 = _F * (2 - _F); _EP2 = _E2 / (1 - _E2)
_LAT0, _LON0, _FE, _FN = np.radians(38.0), np.radians(127.0), 200000.0, 600000.0

def _M(p):
    e2 = _E2
    return _A * ((1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * p
                 - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * np.sin(2*p)
                 + (15*e2**2/256 + 45*e2**3/1024) * np.sin(4*p) - (35*e2**3/3072) * np.sin(6*p))

def to5186(lat, lon):
    phi, lam = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    N = _A / np.sqrt(1 - _E2*np.sin(phi)**2); T = np.tan(phi)**2; C = _EP2*np.cos(phi)**2
    A = (lam - _LON0) * np.cos(phi)
    x = _FE + N*(A + (1-T+C)*A**3/6 + (5-18*T+T**2+72*C-58*_EP2)*A**5/120)
    y = _FN + _M(phi) - _M(_LAT0) + N*np.tan(phi)*(A**2/2 + (5-T+9*C+4*C**2)*A**4/24
                                                  + (61-58*T+T**2+600*C-330*_EP2)*A**6/720)
    return x, y

def to_wgs84(x, y):
    x, y = np.asarray(x, float) - _FE, np.asarray(y, float) - _FN
    e2 = _E2; e1 = (1 - np.sqrt(1-e2)) / (1 + np.sqrt(1-e2))
    mu = (_M(_LAT0) + y) / (_A*(1 - e2/4 - 3*e2**2/64 - 5*e2**3/256))
    p1 = (mu + (3*e1/2 - 27*e1**3/32)*np.sin(2*mu) + (21*e1**2/16 - 55*e1**4/32)*np.sin(4*mu)
          + (151*e1**3/96)*np.sin(6*mu) + (1097*e1**4/512)*np.sin(8*mu))
    N1 = _A/np.sqrt(1-e2*np.sin(p1)**2); R1 = _A*(1-e2)/(1-e2*np.sin(p1)**2)**1.5
    T1 = np.tan(p1)**2; C1 = _EP2*np.cos(p1)**2; D = x/N1
    lat = p1 - (N1*np.tan(p1)/R1)*(D**2/2 - (5+3*T1+10*C1-4*C1**2-9*_EP2)*D**4/24
                                   + (61+90*T1+298*C1+45*T1**2-252*_EP2-3*C1**2)*D**6/720)
    lon = _LON0 + (D - (1+2*T1+C1)*D**3/6 + (5-2*C1+28*T1-3*C1**2+8*_EP2+24*T1**2)*D**5/120)/np.cos(p1)
    return np.degrees(lat), np.degrees(lon)

# ---------- gpkg 도형(WKB) 읽기 ----------
def _wkb_lines(b):
    env = (b[3] >> 1) & 7; w = b[8 + [0, 32, 48, 48, 64][env]:]
    def rd(p):
        bo = '<' if w[p] == 1 else '>'; t = struct.unpack(bo+'I', w[p+1:p+5])[0] % 1000; p += 5
        n = struct.unpack(bo+'I', w[p:p+4])[0]; p += 4
        if t == 2:
            pts = struct.unpack(bo+f'{2*n}d', w[p:p+16*n]); return [list(zip(pts[0::2], pts[1::2]))], p + 16*n
        out = []
        for _ in range(n):
            ls, p = rd(p); out += ls
        return out, p
    return [pt for ls in rd(0)[0] for pt in ls]

# ---------- 엔진 ----------
@dataclass
class Route:
    links: list                     # LINK 행 번호(순서대로)
    dist_m: float
    risk: float                     # Σ risk_h
    high: int                       # 5등급 LINK 수
    coords: list = field(default_factory=list)   # [(lat, lon), ...] 지도용

class Engine:
    def __init__(self, links: pd.DataFrame, facilities: pd.DataFrame | None):
        self.L = links
        tr = links['train'] == 1
        self.L_MEAN = float(links.loc[tr, 'LENGTH'].mean())
        self.H_MEAN = float(links.loc[tr, 'risk_h'].mean())
        # 방향 그래프 (같은 노드쌍은 짧은 LINK 하나만)
        G = nx.DiGraph()
        for i, r in enumerate(links.itertuples()):
            if G.has_edge(r.F_NODE, r.T_NODE) and G[r.F_NODE][r.T_NODE]['L'] <= r.LENGTH:
                continue
            G.add_edge(r.F_NODE, r.T_NODE, i=i, L=r.LENGTH,
                       cd=r.LENGTH / self.L_MEAN, cr=r.risk_h / self.H_MEAN)
        self.G = G
        comp = max(nx.strongly_connected_components(G), key=len)
        self.cand = np.array(sorted(set(links.loc[tr, 'F_NODE']) & comp))
        node_xy = {}
        for xy, f, t in zip(links['xy'], links['F_NODE'], links['T_NODE']):
            node_xy[f] = xy[0]; node_xy[t] = xy[-1]
        self.node_xy = node_xy
        self.tree = cKDTree(np.array([node_xy[n] for n in self.cand]))
        self.facilities = facilities
        # ---- 회전제한용 LINK 단위 탐색 준비 ----
        n = len(links)
        self.F = links['F_NODE'].to_numpy(); self.T = links['T_NODE'].to_numpy()
        self.cd = (links['LENGTH'] / self.L_MEAN).to_numpy(); self.cr = (links['risk_h'] / self.H_MEAN).to_numpy()
        # 교차로 내부 연결 LINK: 시작·끝 노드가 같은 교차로(노드 ID 앞 8자리 동일)이고 짧은 것
        self.internal = ((self.F // 100) == (self.T // 100)) & (links['LENGTH'].to_numpy() < 60)
        self.out = {}
        for i, f in enumerate(self.F):
            self.out.setdefault(f, []).append(i)
        # 진행 방향(도): LINK 끝 구간과 시작 구간의 방위각 → U턴 판단용
        def head(p, q):
            return np.degrees(np.arctan2(q[0] - p[0], q[1] - p[1]))
        self.h_end = np.array([head(xy[-2], xy[-1]) if len(xy) > 1 else 0 for xy in links['xy']])
        self.h_start = np.array([head(xy[0], xy[1]) if len(xy) > 1 else 0 for xy in links['xy']])
        self.row = {lid: i for i, lid in enumerate(links['LINK_ID'])}
        self.banned, self.uturn_ok = set(), set()
        self.strict_uturn = False   # True: U턴 기록이 없는 곳의 U턴 금지 (출발 직후 제외)

    def set_turns(self, df):
        """TURNINFO(NODE_ID, ST_LINK, ED_LINK, TURN_TYPE) 등록"""
        ban = {'101', '102', '103', '003', '002'}        # 좌·직·우 금지, 회전금지, 버스만
        ok = {'011', '012'}                               # U턴·P턴 가능
        for r in df.itertuples():
            a, b = self.row.get(int(r.ST_LINK)), self.row.get(int(r.ED_LINK))
            if a is None or b is None:
                continue
            tt = str(r.TURN_TYPE).zfill(3)
            if tt in ban: self.banned.add((a, b))
            elif tt in ok: self.uturn_ok.add((a, b))

    def _is_uturn(self, a, b):
        d = abs((self.h_start[b] - self.h_end[a] + 180) % 360 - 180)
        return d > 150

    def route_turn(self, o, d, lam, strict_uturn=True):
        """LINK 단위 Dijkstra. 상태 = (지금 LINK, 교차로에 들어온 LINK)"""
        import heapq
        if o == d:
            return None
        H, best, prev = [], {}, {}
        for i in self.out.get(o, []):
            c = self.cd[i] + lam * self.cr[i]; st = (i, i)
            best[st] = c; prev[st] = None; heapq.heappush(H, (c, st))
        goal = None
        while H:
            c, st = heapq.heappop(H)
            if c > best.get(st, 1e18): continue
            cur, appr = st
            if self.T[cur] == d:
                goal = st; break
            for nx in self.out.get(self.T[cur], []):
                if self.internal[nx]:
                    nst = (nx, appr)                       # 교차로 안: 들어온 LINK 기억
                else:
                    if (appr, nx) in self.banned: continue
                    if (strict_uturn and self.F[appr] != o and self._is_uturn(appr, nx)
                            and (appr, nx) not in self.uturn_ok): continue   # 출발 직후 방향 돌리기는 허용
                    nst = (nx, nx)
                nc = c + self.cd[nx] + lam * self.cr[nx]
                if nc < best.get(nst, 1e18):
                    best[nst] = nc; prev[nst] = st; heapq.heappush(H, (nc, nst))
        if goal is None:
            return None
        idx = []; st = goal
        while st is not None:
            idx.append(st[0]); st = prev[st]
        idx.reverse()
        sub = self.L.iloc[idx]
        return Route(idx, float(sub['LENGTH'].sum()), float(sub['risk_h'].sum()),
                     int((sub['risk_grade'] == 5).sum()), [pt for ll in sub['latlon'] for pt in ll])

    # ---------- 이름으로 찾기 (외부 API 없이: 생활시설 + 도로명) ----------
    def _build_search(self):
        items = []
        if self.facilities is not None:
            for r in self.facilities.itertuples():
                items.append({'label': f'[{r.종류}] {r.이름}', 'node': int(r.NODE_ID),
                              'keys': f'{r.이름} {r.종류} {getattr(r, "주소", "")}'})
        tr = self.L[(self.L['train'] == 1) & (self.L['ROAD_NAME'] != '이름 없는 도로')]
        cand = set(self.cand.tolist())
        for name, g in tr.groupby('ROAD_NAME'):
            nodes = [n for n in g['F_NODE'] if n in cand]
            if not nodes:
                continue
            xy = np.array([self.node_xy[n] for n in nodes])
            mid = nodes[int(np.argmin(((xy - xy.mean(0)) ** 2).sum(1)))]   # 도로 가운데쯤 노드
            items.append({'label': f'[도로] {name}', 'node': int(mid), 'keys': name})
        self._search_items = items

    def search(self, query, limit=30):
        """'경동시장', '경동 시장', '병원', '왕산로' 등. 띄어쓰기 무시, 정확히 맞는 것부터"""
        if not hasattr(self, '_search_items'):
            self._build_search()
        q = str(query).replace(' ', '').strip()
        if not q:
            return []
        scored = []
        for it in self.items_iter():
            name = it['label'].split('] ', 1)[-1].replace(' ', '')
            keys = it['keys'].replace(' ', '')
            if name == q: s = 0
            elif name.startswith(q): s = 1
            elif q in name: s = 2
            elif q in keys: s = 3
            else: continue
            scored.append((s, len(name), it))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [it for _, _, it in scored[:limit]]

    def items_iter(self):
        return self._search_items

    # 클릭 좌표 → 노드
    def snap(self, lat, lon):
        x, y = to5186(lat, lon)
        d, k = self.tree.query([float(x), float(y)])
        return int(self.cand[k]), float(d)

    def node_latlon(self, node):
        x, y = self.node_xy[node]; lat, lon = to_wgs84(x, y); return float(lat), float(lon)

    def route(self, o, d, lam):
        try:
            path = nx.dijkstra_path(self.G, o, d, weight=lambda u, v, e: e['cd'] + lam * e['cr'])
        except nx.NetworkXNoPath:
            return None
        idx = [self.G[u][v]['i'] for u, v in zip(path[:-1], path[1:])]
        sub = self.L.iloc[idx]
        coords = [pt for ll in sub['latlon'] for pt in ll]
        return Route(idx, float(sub['LENGTH'].sum()), float(sub['risk_h'].sum()),
                     int((sub['risk_grade'] == 5).sum()), coords)

    def compare(self, o, d, lam=0.75):
        """일반경로(λ=0)와 안전경로(λ) 비교. 오류는 ('error', 메시지)로 돌려줌"""
        if o == d:
            return {'error': '출발지와 도착지가 같은 지점입니다. 다른 곳을 골라 주세요.'}
        if self.banned or self.uturn_ok:      # 회전제한이 등록돼 있으면 LINK 단위 탐색
            p, s = self.route_turn(o, d, 0.0, self.strict_uturn), self.route_turn(o, d, lam, self.strict_uturn)
        else:
            p, s = self.route(o, d, 0.0), self.route(o, d, lam)
        if p is None or s is None:
            return {'error': '두 지점을 잇는 경로를 찾지 못했습니다. 다른 지점을 골라 주세요.'}
        pct = lambda a, b: (a / b - 1) * 100 if b else 0.0
        warn = (self.L.iloc[s.links].query('risk_grade >= 4')
                .sort_values('risk_h', ascending=False).head(5)[['ROAD_NAME', 'risk_grade', 'LENGTH']])
        return {'plain': p, 'safe': s, 'lam': lam,
                'detour_pct': pct(s.dist_m, p.dist_m), 'risk_cut_pct': -pct(s.risk, p.risk),
                'high_cut': p.high - s.high, 'warnings': warn}

def load_engine(gpkg_path, facilities_csv=None, turn_csv=None, strict_uturn=False):
    con = sqlite3.connect(gpkg_path)
    tn = pd.read_sql("select table_name from gpkg_contents where data_type='features'", con).table_name[0]
    df = pd.read_sql(f'select geom, LINK_ID, F_NODE, T_NODE, LENGTH, ROAD_NAME, in_ddm, train, '
                     f'risk_score, risk_h, risk_grade from "{tn}"', con)
    con.close()
    for c in ['LINK_ID', 'F_NODE', 'T_NODE']:
        df[c] = df[c].astype('int64')
    df = df.sort_values('LINK_ID').reset_index(drop=True)
    df['xy'] = [_wkb_lines(b) for b in df.pop('geom')]
    allxy = np.array([p for xy in df['xy'] for p in xy])
    lat, lon = to_wgs84(allxy[:, 0], allxy[:, 1])
    k = 0; ll = []
    for xy in df['xy']:
        n = len(xy); ll.append(list(zip(lat[k:k+n].round(6), lon[k:k+n].round(6)))); k += n
    df['latlon'] = ll
    df['ROAD_NAME'] = df['ROAD_NAME'].fillna('이름 없는 도로')
    fac = None
    if facilities_csv:
        fac = pd.read_csv(facilities_csv, encoding='utf-8-sig')
        fac = fac[fac['연결여부'] == '연결'].reset_index(drop=True)
    E = Engine(df, fac)
    import os
    if turn_csv and os.path.exists(turn_csv):      # 파일이 없으면 회전제한 없이 실행
        E.set_turns(pd.read_csv(turn_csv))
        E.strict_uturn = strict_uturn
    return E
