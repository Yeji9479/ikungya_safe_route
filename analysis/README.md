# 분석 스크립트
저장소 최상위 폴더에서 실행합니다. 검증용 `links_finall.gpkg`는 원본 데이터라 저장소에 없으니 따로 받아 두세요.

| 스크립트 | 하는 일 |
|---|---|
| `retrain_service.py` | 서비스용 위험모델 재학습 (2023~25) → `links_service.gpkg` |
| `lambda_sweep.py` | λ 후보 13개 실험 (OD 300쌍, `data/od_list.csv`) |
| `eval_turn.py` | 회전제한 반영 전후 비교 |
| `facility_nodes.py` | 생활시설을 탐색 가능 노드에 연결 |
