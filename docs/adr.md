# Architecture Decision Records

## ADR-STR-001: IR 中樞式 (hub-and-spoke) 整合架構
- **決策**：以 FitIR 為唯一交換語言，importer/exporter 各自對接單一 app（N+M）；拒絕 app 對 app 直翻（N×M）。
- **理由**：使用者明示需求（LLVM IR 類比、易於擴增進出不同 app）；三款 app 模型差異大（文件式/DSL/正規化），直翻組合爆炸。
- **後果**：所有語義衝突必須在 IR 設計期解決（見 api-analysis.md §4）。

## ADR-STR-002: JSON 文件儲存 + refs 索引，而非正規化關聯 schema
- **決策**：SQLite 中以 `documents(kind, id, fitir_version, body)` 儲存 IR 文件，輔以 `refs` 索引表；不把 IR 展開成多張關聯表。
- **理由**：IR 要能迭代改版不破壞相容（lazy migration 需保留原文件）；文件式與 Hevy 增量事件天然對齊；查詢需求（列表/單筆/匯出）不需要關聯查詢。
- **取捨**：放棄 SQL 級的欄位查詢；以應用層過濾補足（資料量為個人訓練紀錄等級，可行）。

## ADR-STR-003: Python 3.12 + FastAPI + Pydantic + APScheduler + SQLite
- **理由**：Pydantic 模型即 IR 規格的可執行形式（validation = 規格檢查）；FastAPI 自動 OpenAPI 文件呼應本專案「API 優先」精神；SQLite 單檔案 volume 即可持久化，無需額外 DB 容器（Ockham）。
- **替代方案**：Postgres（多餘）、Node/TS（Pydantic 的 schema 表達力勝出）。

## ADR-STR-004: IR 版本策略 — SemVer + additive-only minor + 註冊式 lazy migration
- **決策**：見 ir-spec.md §5。儲存層永不重寫舊版文件；讀取路徑鏈式遷移。
- **理由**：使用者明示「data model/IR 要能迭代改版而不破壞相容性」；LLVM bitcode 前例。

## ADR-STR-005: 前後端分離 — 靜態 SPA + nginx 反代，前端零業務邏輯
- **決策**：frontend 僅消費 `/api/*` JSON；無 build 工具鏈（vanilla JS）。
- **理由**：使用者明示前後端分離、資料匯出入清晰；vanilla 免去 node 建置層（YAGNI）；替換前端不影響資料流。

## ADR-SEC-001: 金鑰處理
- **決策**：金鑰來源 env 或 GUI 設定（存 DB settings 表，明文，檔案權限保護）；`.env`/`data/` 進 `.gitignore`；日誌不輸出金鑰。
- **風險承認**：GUI 無認證（RISK-001，內網個人工具定位；公開部署需 nginx basic auth，文件已註明）。
