"""
고령운전자 안전경로 추천 웹 (7조 아이쿵야)
실행:  python -m streamlit run app.py
데이터: data/ 폴더 (파일만 바꿔 끼우면 됨)
개인정보: 입력한 장소·클릭 좌표는 저장하지 않음 (화면 상태에만 잠시 보관)
"""
import folium
import streamlit as st
from streamlit_folium import st_folium
from engine import load_engine, MAX_SNAP_M

GPKG = 'data/links_service.gpkg'
FACILITIES = 'data/facility_nodes.csv'
TURNS = 'data/turn_restrictions.csv'      # ITS TURNINFO 회전제한 (None이면 미반영)
APP_VERSION = 'v0.1.0-draft'               # 초안 표시 (정식 버전이 되면 v1.0.0)
LAMBDA_DEFAULT = 0.75
CENTER = (37.5795, 127.0560)              # 동대문구 중심
GRADE_COLOR = {4: '#E07A3C', 5: '#B5262E'}
PLAIN_COLOR, SAFE_COLOR = '#6B6F76', '#1F5FBF'
PICK = '— 검색 결과에서 고르세요 —'

st.set_page_config(page_title='고령운전자 안전경로', page_icon='🚗', layout='wide')

# ---------- 큰 글씨 (고령 사용자) ----------
st.markdown("""
<style>
html { font-size: 20px; }
.block-container { padding-top: 1.6rem; }
h1 { font-size: 2.3rem !important; }
h2, h3 { font-size: 1.7rem !important; }
p, li, label, .stMarkdown { font-size: 1.1rem !important; line-height: 1.6; }
section[data-testid="stSidebar"] { min-width: 380px; }
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] label { font-size: 1.15rem !important; }
input, div[data-baseweb="select"] * { font-size: 1.15rem !important; }
.stButton button { font-size: 1.15rem; padding: 0.6rem 1rem; }
div[data-testid="stMetricValue"] { font-size: 2.4rem; }
div[data-testid="stMetricLabel"] p { font-size: 1.15rem !important; }
div[data-testid="stAlert"] p { font-size: 1.15rem !important; }
table.cmp { width: 100%; border-collapse: collapse; font-size: 1.15rem; }
table.cmp th, table.cmp td { padding: 0.45rem 0.4rem; border-bottom: 1px solid #ddd; text-align: right; }
table.cmp th:first-child, table.cmp td:first-child { text-align: left; color: #555; font-weight: 400; }
table.cmp td.safe { color: #1F5FBF; font-weight: 700; }
.legend span { display: inline-block; margin-right: 1.2rem; font-size: 1.05rem; }
</style>""", unsafe_allow_html=True)

# ---------- 데이터: 처음 한 번만 읽음 ----------
@st.cache_resource(show_spinner='도로망을 불러오는 중…')
def get_engine():
    return load_engine(GPKG, FACILITIES, TURNS)

@st.cache_data
def risk_geojson(_E, grades=(4, 5)):
    feats = []
    for r in _E.L[(_E.L.in_ddm == 1) & (_E.L.risk_grade.isin(grades))].itertuples():
        feats.append({'type': 'Feature', 'properties': {'g': int(r.risk_grade), 'name': r.ROAD_NAME},
                      'geometry': {'type': 'LineString', 'coordinates': [[lo, la] for la, lo in r.latlon]}})
    return {'type': 'FeatureCollection', 'features': feats}

E = get_engine()

# ---------- 화면 상태 ----------
ss = st.session_state
for k, v in {'o': None, 'd': None, 'o_name': None, 'd_name': None, 'last_click': None, 'msg': None}.items():
    ss.setdefault(k, v)

# ---------- 사이드바: 장소 입력 ----------
st.sidebar.markdown('## 어디로 가시나요?')
st.sidebar.markdown('장소 이름이나 도로 이름을 입력하세요.  \n예) 경동시장, 경희의료원, 왕산로, 병원')

def place_input(which, title):
    q = st.sidebar.text_input(title, key=f'q_{which}', placeholder='예) 경동시장')
    if not q:
        return
    res = E.search(q)
    if not res:
        st.sidebar.warning('찾는 장소가 없습니다. 다른 이름으로 입력하거나 지도를 눌러 주세요.')
        return
    labels = [PICK] + [r['label'] for r in res]
    key = f'pick_{which}_{q}'                     # 검색어가 바뀌면 새 목록

    def chosen():
        lab = ss[key]
        if lab != PICK:
            it = res[labels.index(lab) - 1]
            ss[which], ss[f'{which}_name'] = it['node'], lab.split('] ', 1)[-1]
            ss.msg = None
    # 결과가 하나뿐이면 바로 정함 (같은 검색어로는 한 번만: 지도 클릭과 다투지 않게)
    if len(res) == 1 and ss.get(f'auto_{which}') != q:
        ss[which], ss[f'{which}_name'] = res[0]['node'], res[0]['label'].split('] ', 1)[-1]
        ss[f'auto_{which}'] = q
    st.sidebar.selectbox('검색 결과', labels, key=key, on_change=chosen,
                         label_visibility='collapsed')

place_input('o', '🔵 출발지')
place_input('d', '🔴 도착지')

def reset_all():
    # last_click은 지우지 않음: 지도가 마지막 클릭을 계속 돌려주므로, 지우면 옛 클릭이 다시 처리됨
    ss.o = ss.d = ss.o_name = ss.d_name = ss.msg = None
    for k in ['q_o', 'q_d', 'auto_o', 'auto_d']:
        ss[k] = ''
    for k in [k for k in ss if str(k).startswith('pick_')]:
        ss[k] = PICK

st.sidebar.button('처음부터 다시', on_click=reset_all, use_container_width=True)
st.sidebar.markdown('---')
st.sidebar.markdown('지도에서 직접 고를 수도 있습니다.  \n첫 번째 클릭 = 출발지, 두 번째 클릭 = 도착지')

with st.sidebar.expander('고급 설정'):
    lam = st.slider('위험회피 강도 λ', 0.0, 2.0, LAMBDA_DEFAULT, 0.05,
                    help='0이면 최단거리, 0.75가 검증된 권장값입니다.')
    show_risk = st.checkbox('위험 도로(4·5등급) 표시', value=True)

# ---------- 계산 ----------
res = E.compare(ss.o, ss.d, lam) if (ss.o is not None and ss.d is not None) else None

def name(which):
    node = ss[which]
    if node is None:
        return '아직 정하지 않음'
    return ss[f'{which}_name'] or '지도에서 고른 곳'

# ---------- 지도 ----------
m = folium.Map(location=CENTER, zoom_start=14, tiles='OpenStreetMap', control_scale=True)
if show_risk:
    folium.GeoJson(risk_geojson(E), name='위험도로',
                   style_function=lambda f: {'color': GRADE_COLOR[f['properties']['g']], 'weight': 3, 'opacity': 0.55},
                   tooltip=folium.GeoJsonTooltip(fields=['name', 'g'], aliases=['도로', '위험등급'])).add_to(m)
if res and 'error' not in res:
    folium.PolyLine(res['plain'].coords, color=PLAIN_COLOR, weight=6, opacity=0.8, dash_array='10 10',
                    tooltip='일반경로 (가장 짧은 길)').add_to(m)
    folium.PolyLine(res['safe'].coords, color=SAFE_COLOR, weight=9, opacity=0.85,
                    tooltip='안전경로 (추천)').add_to(m)
    pts = res['plain'].coords + res['safe'].coords
    m.fit_bounds([[min(a for a, _ in pts), min(b for _, b in pts)],
                  [max(a for a, _ in pts), max(b for _, b in pts)]])
for which, label, color in [('o', '출발', 'darkblue'), ('d', '도착', 'red')]:
    if ss[which] is not None:
        folium.Marker(E.node_latlon(ss[which]), tooltip=f'{label}: {name(which)}',
                      icon=folium.Icon(color=color, icon='flag')).add_to(m)

st.markdown('# 🚗 안전한 길 찾기')
st.warning(f'초안({APP_VERSION}) · 캡스톤 연구용 프로토타입입니다. 실제 운전 안내에 쓰지 마세요.')
left, right = st.columns([2, 1.15], gap='large')
with left:
    out = st_folium(m, height=660, use_container_width=True, returned_objects=['last_clicked'], key='map')
    st.markdown('<div class="legend"><span><b style="color:#1F5FBF">━━</b> 안전경로(추천)</span>'
                '<span><b style="color:#6B6F76">┅┅</b> 일반경로(가장 짧은 길)</span>'
                '<span><b style="color:#B5262E">━</b> 사고 위험이 높은 도로</span></div>', unsafe_allow_html=True)

# 지도 클릭 처리 (같은 클릭을 두 번 처리하지 않음)
click = (out or {}).get('last_clicked')
if click and click != ss.last_click:
    ss.last_click = click
    node, dist = E.snap(click['lat'], click['lng'])
    if dist > MAX_SNAP_M:
        ss.msg = f'누른 곳에서 {dist:,.0f} m 안에 이용할 수 있는 도로가 없습니다. 동대문구 안의 도로 가까이를 눌러 주세요.'
    else:
        ss.msg = None
        if ss.o is None or ss.d is not None:
            ss.o, ss.o_name, ss.d, ss.d_name = node, None, None, None      # 새로 시작
        else:
            ss.d, ss.d_name = node, None
    st.rerun()

# ---------- 결과 ----------
with right:
    st.markdown(f'### 🔵 출발: {name("o")}\n### 🔴 도착: {name("d")}')
    if ss.msg:
        st.warning(ss.msg)
    if res is None:
        st.info('왼쪽에 출발지와 도착지를 입력하거나, 지도에서 두 곳을 차례로 눌러 주세요.')
    elif 'error' in res:
        st.warning(res['error'])
    else:
        p, s = res['plain'], res['safe']
        extra = s.dist_m - p.dist_m
        if res['risk_cut_pct'] < 0.05:
            st.success('가장 짧은 길이 이미 가장 안전한 길입니다.')
        else:
            st.success(f"**{extra:,.0f} m** 더 돌아가면, 사고 위험이 **{res['risk_cut_pct']:.0f}%** 줄어듭니다.")
        c1, c2 = st.columns(2)
        c1.metric('안전경로 거리', f'{s.dist_m/1000:.1f} km', f"{res['detour_pct']:+.1f}%", delta_color='off')
        c2.metric('줄어드는 위험', f"{res['risk_cut_pct']:.0f}%")
        st.markdown(f"""
<table class="cmp">
<tr><th></th><th>일반경로</th><th>안전경로</th></tr>
<tr><td>거리</td><td>{p.dist_m/1000:.1f} km</td><td class="safe">{s.dist_m/1000:.1f} km</td></tr>
<tr><td>위험한 도로(5등급)</td><td>{p.high}곳</td><td class="safe">{s.high}곳</td></tr>
<tr><td>누적 위험점수</td><td>{p.risk:.2f}</td><td class="safe">{s.risk:.2f}</td></tr>
</table>""", unsafe_allow_html=True)
        st.markdown('#### ⚠️ 안전경로에서 조심할 곳')
        w = res['warnings']
        if len(w):
            st.markdown('\n'.join(f'- **{r.ROAD_NAME}** ({r.risk_grade}등급, {r.LENGTH:,.0f} m)' for r in w.itertuples()))
        else:
            st.markdown('- 사고 위험이 높은 도로를 지나지 않습니다.')
    st.caption(f'λ = {lam} · 정규화 상수 L {E.L_MEAN:.1f} m / H {E.H_MEAN:.4f} · '
               + ('ITS TURNINFO 회전제한 반영' if E.banned else '회전제한 미반영')
               + f' · 입력한 장소는 저장하지 않습니다 · {APP_VERSION}')
