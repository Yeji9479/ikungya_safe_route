"""
서비스용 위험모델 재학습 (7조 아이쿵야)
- 평가용 모델: 2023~24 사고로 학습 → 2025로 검증 (이미 완료, 결과 고정)
- 서비스용 모델: 2023~25 사고(3년치)로 같은 방식 재학습 → 웹 서비스에 사용

입력 : links_finall.gpkg   (4,934 LINK, accident_2324·accident_2025·train 포함)
출력 : links_service.gpkg  (입력 + 서비스용 위험 열, QGIS에서 바로 열림)
       link_risk_service.csv, service_constants.csv, service_odds.csv
실행 : python retrain_service.py links_finall.gpkg
필요 : pandas, numpy, scikit-learn  (geopandas 불필요)
"""
import sys, os, shutil, sqlite3
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression

SRC = sys.argv[1] if len(sys.argv) > 1 else 'links_finall.gpkg'
OUT = 'links_service.gpkg'
CAT  = ['int_type4', 'lanes_c', 'spd_c']
BASE = ['int_type4_2지이하', 'lanes_c_1', 'spd_c_30이하']          # 기준 범주
NUM  = ['log_len', 'log_btw', 'shop_log', 'nat_road', 'turn_p', 'sig_int']

# ---------- 1. 읽기 ----------
con = sqlite3.connect(SRC)
TN = pd.read_sql("select table_name from gpkg_contents where data_type='features'", con).table_name[0]
d = pd.read_sql(f'select * from "{TN}"', con).drop(columns=['geom'], errors='ignore')
con.close()
d['LINK_ID'] = d['LINK_ID'].astype('int64')
assert len(d) == 4934, f'LINK 수가 4,934가 아닙니다: {len(d)}'
tr = d['train'] == 1
print(f'학습 LINK {tr.sum()}개')

def design(df):
    Z = pd.concat([pd.get_dummies(df[CAT].astype(str), dtype=float), df[NUM].astype(float)], axis=1)
    return Z.drop(columns=BASE)

def fit_predict(label):
    Zt = design(d[tr]); Za = design(d).reindex(columns=Zt.columns, fill_value=0)
    mu = pd.Series(0.0, index=Zt.columns); sd = pd.Series(1.0, index=Zt.columns)
    CONT = ['log_len', 'log_btw', 'shop_log']           # 연속형만 표준화 (예측값에는 영향 없음, 해석용)
    mu[CONT], sd[CONT] = Zt[CONT].mean(), Zt[CONT].std()
    m = LogisticRegression(C=np.inf, solver='newton-cg', tol=1e-12, max_iter=100000)
    m.fit((Zt - mu) / sd, d.loc[tr, label])
    return m.predict_proba((Za - mu) / sd)[:, 1], m, Zt.columns, sd

# ---------- 2. 확인: 평가용 모델(2023~24)이 기존 risk_score와 같은지 ----------
p_eval, *_ = fit_predict('accident_2324')
if 'risk_score' in d:
    diff = np.abs(p_eval - d['risk_score']).max()
    print(f'평가용 모델 재현 오차 = {diff:.1e}  (1e-6보다 작으면 정상)')

# ---------- 3. 서비스용 라벨: 2023~25년 중 사고 1건 이상 ----------
d['accident_2325'] = ((d['accident_2324'] == 1) | (d['accident_2025'] == 1)).astype(int)
print(f'서비스용 사고 LINK {d.loc[tr, "accident_2325"].sum()}개 '
      f'({d.loc[tr, "accident_2325"].mean():.1%}, 평가용은 {d.loc[tr, "accident_2324"].mean():.1%})')

p, m, cols, sd = fit_predict('accident_2325')
d['risk_score_svc'] = p                                   # 3년 동안 사고가 날 확률
d['risk_h_svc'] = -np.log(1 - p)                          # 경로 비용용 위험도
cut = d.loc[tr, 'risk_score_svc'].quantile([.2, .4, .6, .8]).values   # 학습 LINK 5분위
d['risk_grade_svc'] = 1 + np.searchsorted(cut, d['risk_score_svc'], side='right')

# ---------- 4. 오즈비 (연속형은 1표준편차당) ----------
odds = pd.Series(np.exp(m.coef_[0]), index=cols).round(2).sort_values(ascending=False)
odds.rename('오즈비').to_csv('service_odds.csv', encoding='utf-8-sig')
print('\n[서비스용 오즈비] (연속형은 1표준편차당)'); print(odds.to_string())

# ---------- 5. 경로 비용 정규화 상수 (학습 LINK 평균) ----------
L_MEAN = d.loc[tr, 'LENGTH'].mean(); H_MEAN = d.loc[tr, 'risk_h_svc'].mean()
pd.DataFrame({'항목': ['L_MEAN', 'H_MEAN', 'LAMBDA', '모델'],
              '값': [round(L_MEAN, 2), round(H_MEAN, 4), 0.75, '로지스틱 2023~25 (서비스용)']}
             ).to_csv('service_constants.csv', index=False, encoding='utf-8-sig')
print(f'\n정규화 상수  L_MEAN={L_MEAN:.1f}  H_MEAN={H_MEAN:.4f}  (평가용 0.2095)')

# ---------- 6. 평가용과 비교 ----------
ge = d['risk_grade'] if 'risk_grade' in d else None
print(f'평가용·서비스용 위험점수 순위상관 = {pd.Series(p_eval).rank().corr(pd.Series(p).rank()):.3f}')
if ge is not None:
    same = (ge[tr] == d.loc[tr, 'risk_grade_svc']).mean()
    print(f'학습 LINK 중 위험등급이 그대로인 비율 = {same:.1%}')

# ---------- 7. 저장: 원본 gpkg 복사본에 열 추가 + 경로탐색에 쓰는 열 교체 ----------
shutil.copy(SRC, OUT)
con = sqlite3.connect(OUT); cur = con.cursor()
# gpkg 공간색인 트리거는 QGIS 전용 함수를 써서 일반 Python에서 실패함 → 잠시 떼었다가 다시 붙임
#  (도형은 바꾸지 않으므로 공간색인은 그대로 유효)
trig = cur.execute("select name, sql from sqlite_master where type='trigger' and tbl_name=?", (TN,)).fetchall()
for name, _ in trig: cur.execute(f'drop trigger "{name}"')
have = {r[1] for r in cur.execute(f'pragma table_info("{TN}")')}
newcols = {'accident_2325': 'INTEGER', 'risk_score_svc': 'REAL', 'risk_h_svc': 'REAL', 'risk_grade_svc': 'INTEGER',
           'risk_score_eval': 'REAL', 'risk_h_eval': 'REAL', 'risk_grade_eval': 'INTEGER'}
for c, t in newcols.items():
    if c not in have: cur.execute(f'alter table "{TN}" add column "{c}" {t}')
# 평가용 값은 *_eval로 보관, risk_score/risk_h/risk_grade는 서비스용으로 교체
d['risk_score_eval'] = d.get('risk_score', p_eval); d['risk_h_eval'] = d.get('risk_h', -np.log(1 - p_eval))
d['risk_grade_eval'] = d.get('risk_grade', 0)
rows = [(int(r.accident_2325), float(r.risk_score_svc), float(r.risk_h_svc), int(r.risk_grade_svc),
         float(r.risk_score_eval), float(r.risk_h_eval), int(r.risk_grade_eval),
         float(r.risk_score_svc), float(r.risk_h_svc), int(r.risk_grade_svc), int(r.LINK_ID)) for r in d.itertuples()]
cur.executemany(f'''update "{TN}" set accident_2325=?, risk_score_svc=?, risk_h_svc=?, risk_grade_svc=?,
                    risk_score_eval=?, risk_h_eval=?, risk_grade_eval=?,
                    risk_score=?, risk_h=?, risk_grade=? where LINK_ID=?''', rows)
for _, sql in trig: cur.execute(sql)
con.commit(); con.close()
d[['LINK_ID', 'risk_score_svc', 'risk_h_svc', 'risk_grade_svc', 'train']].rename(
    columns=lambda c: c.replace('_svc', '')).to_csv('link_risk_service.csv', index=False, encoding='utf-8-sig')
print(f'\n저장: {OUT}, link_risk_service.csv, service_constants.csv, service_odds.csv')
