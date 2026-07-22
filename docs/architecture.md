# hevy-bridge 系統架構

> 任務 3 設計文件。追溯：FR-020~FR-034 → ADR-STR-001/002/003/005。

## 1. 資料流（前後端分離、進出清晰）

```
                    ┌────────────────────────────── backend (FastAPI) ──────────────────────────────┐
  Hevy API ──pull──►│ connectors/hevy.py   ─lower─► ir/schema.py (FitIR) ─► store (SQLite/JSON docs) │
  (排程/手動)        │  (importer/frontend)                                   │                        │
                    │                                                        ▼                        │
  wger API ◄──push──│ connectors/wger.py  ◄─lower── services/export.py ◄── REST API (/api/*)         │
                    └────────────────────────────────────────────────────────▲───────────────────────┘
                                                                             │ JSON over HTTP
                                                              frontend (nginx 靜態 SPA, /api 反向代理)
```

- **進 (import)**：只有 connectors 目錄的 importer 能寫入 store，且只寫 FitIR 文件。
- **出 (export)**：exporter 只讀 FitIR，翻譯成目標 app 格式後推送；匯出狀態（成敗、外部 id）記回 refs。
- **前端**：純靜態 SPA，只透過 `/api/*` JSON 溝通，不含任何業務邏輯 — 前後端可獨立替換。

## 2. 元件

| 元件 | 位置 | 職責 |
|------|------|------|
| IR 核心 | `backend/app/ir/` | Pydantic 版 FitIR 規格、版本遷移註冊表。**不依賴任何 connector** |
| Hevy importer | `backend/app/connectors/hevy.py` | API client、初次全量拉取（workouts/routines/templates/body_measurements 分頁）、之後 `workouts/events?since=` 增量、lower 至 FitIR |
| wger exporter | `backend/app/connectors/wger.py` | exercise 名稱自動解析（/exercise/search）+ 手動映射、session→workoutsession+workoutlog、body-metric→weightentry/measurement |
| Store | `backend/app/db.py`, `models.py` | SQLite：`documents`(kind, id, fitir_version, body JSON)、`refs`(system, external_id ↔ ir_id)、`raw_archive`（原始 payload 封存）、`sync_runs`、`settings` |
| 同步服務 | `backend/app/services/sync.py` | APScheduler 週期執行（`SYNC_INTERVAL_MINUTES`）+ 手動觸發；冪等（以 refs+updated_at 判重） |
| REST API | `backend/app/api/routes.py` | status/sessions/plans/exercises/body-metrics/sync/export/settings/mappings |
| Web GUI | `frontend/` | 同步狀態、資料瀏覽、IR bundle 下載、wger 匯出（含未解析動作映射的手動補齊） |

## 3. 擴充新 app 的流程（IR 的 N+M 承諾）

1. 新增 `connectors/<app>.py`，實作 `Importer`（`pull() -> list[FitIRDoc]`）或 `Exporter`（`push(docs) -> ExportReport`）protocol（`connectors/base.py`）。
2. 在 `connectors/__init__.py` 註冊。API 與 GUI 的 connector 清單即自動出現。
3. 不觸碰 IR 核心與其他 connector。app 特有資料放 `ext.<vendor>`。

## 4. 部署

`docker-compose up`：
- `backend`：python:3.12-slim，uvicorn :8000，volume `./data` 持久化 SQLite。
- `frontend`：nginx:alpine，:8080 服務 SPA 並反代 `/api/` → backend:8000。
- 設定：`.env`（`HEVY_API_KEY` 必填；`WGER_BASE_URL`/`WGER_API_KEY`/`SYNC_INTERVAL_MINUTES` 選填，皆可後續在 GUI 設定頁覆寫，儲存於 DB settings 表）。

## 5. 安全備註（ADR-SEC-001）

- API 金鑰經由 env 或 GUI 寫入 DB，不進 git；`.env` 已列入 `.gitignore`。
- GUI 無認證 — 定位為個人自架內網工具；如需公開部署，於 nginx 層加 basic auth（文件註明，不預作，YAGNI）。
