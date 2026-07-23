# Traceability Matrix

## 追溯鏈 BG → FEA → FR → 實作/驗證

| ID | 內容 | 上游 | 下游 / 實作 | 驗證 |
|----|------|------|------------|------|
| BG-001 | 各 app 匯出格式互不相容，資料被鎖定 | — | FEA-060, BG-002 | feature-landscape.md |
| BG-002 | 以單一事實來源中介模型建立資料中樞 | BG-001 | FR-001~012 | ir-spec.md |
| FEA-001~061 | 差異化功能盤點（61 項，8 類） | BG-001 | feature-landscape.md | 市場來源 + 一手 API 分析 |
| FR-001 | IR 涵蓋 exercise/plan/session/body-metric 四 kind | BG-002 | ir/schema.py | 冒煙測試 1-4 |
| FR-002 | 冗餘精粹 R1~R12 全部有 SSOT 決策 | BG-002 | api-analysis.md §4 | 12/12 決策表 |
| FR-003 | canonical 單位 (kg/m/s/cm/percent) | R2 | Quantity 型別 | 冒煙測試 1 |
| FR-004 | effort 保留原始尺度、轉換不落地 | R3 | Effort 型別 + WgerExporter._rir | 冒煙測試 6 |
| FR-005 | superset 以 group_key 表達 | R5 | hevy.py lowering | 2026-07-23 修正：Hevy 欄位為 `superset_id`（原誤寫 `supersets_id`，合成資料冒煙測試未攔到）；已改用真實 raw_archive payload 迴歸驗證，wger routine 實測 12 supersets 正確分組 |
| FR-006 | 進程規則統一模型 + DSL ext 保留 | R7 | ProgressionRule + ext.liftosaur | ir-spec.md §3.2 |
| FR-007 | provenance refs + raw_archive | SSOT | db.py refs/raw_archive | 冒煙測試 2 |
| FR-008 | 版本策略：additive minor + lazy major migration | BG-002 | ir/migrate.py | 冒煙測試 5 |
| FR-020 | Hevy 定期拉取（排程 + 手動） | 任務 3 | services/sync.py + APScheduler | API 實測 /api/sync/run |
| FR-021 | 增量同步 + 刪除傳播 | FEA-041 | hevy.py _pull_workout_events | 程式碼審視（需真實金鑰整測） |
| FR-022 | docker-compose 部署 | 任務 3 | docker-compose.yml | compose config valid（含無 .env） |
| FR-023 | Web GUI（狀態/瀏覽/匯出/設定/映射） | 任務 3 | frontend/ | node --check + API 實測 |
| FR-024 | 前後端分離、JSON 邊界 | 任務 3 | nginx 反代 + CORS | architecture.md §1 |
| FR-025 | wger 匯出（session/weight + 動作解析） | 任務 3 | connectors/wger.py | 真實 wger 2.7 實測通過 (2026-07-23)：5 sessions / 226 logs |
| FR-027 | Exercise 解析 pipeline（manual/override/catalog/create + 失效重驗 + 排程自動 push） | FR-025, DEBT-001 | connectors/wger.py + data/wger-mapping.yaml + services/sync.py | 41 動作全解析（31 catalog + 10 create）；push 0 errors |
| FR-026 | 擴充新 app N+M | ADR-STR-001 | connectors/base.py + registry | architecture.md §3 |
| FR-028 | 全面增量匯出：session 更新/刪除標記、plan→routine、全 body-metric、exercise 回寫 | FR-025, R1, R9, ADR-STR-006/007 | db.py export_state + connectors/wger.py + connectors/hevy.py | 離線冒煙 24/24；真實 wger 實測 (2026-07-23)：2 routines 匯出、1 session 更新重建 32 logs、0 errors、preview 收斂全零 |
| ADR-STR-001~007, ADR-SEC-001 | 架構決策 | FR-* | docs/adr.md | — |
| RISK-001 | GUI 無認證 | ADR-SEC-001 | workflow-state.md | open, MEDIUM |
| DEBT-001~004 | 見 workflow-state.md | — | — | DEBT-004 已由 FR-028 緩解（更新重推自癒）；餘 active |
