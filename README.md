# hevy-bridge

以 **FitIR**（LLVM IR 式的健身資料中介表示）為中樞的資料橋接服務：定期從 Hevy 拉取訓練資料，
以單一事實來源儲存，並可經 Web GUI 匯出至 wger 等其他健身 app。

```
Hevy ──pull──► [importer] ──► FitIR store (SQLite) ──► [exporter] ──push──► wger
                                    │
                                    └──► Web GUI (瀏覽 / 手動同步 / 匯出 / IR bundle 下載)
```

## 快速開始

```bash
cp .env.example .env        # 填入 HEVY_API_KEY（Hevy Pro → Settings → Developer）
docker compose up -d --build
open http://localhost:8080  # Web GUI；REST API 在 http://localhost:8000/docs
```

金鑰也可留空，啟動後在 GUI 的 Settings 分頁設定。排程同步間隔預設 60 分鐘；
首次同步全量拉取，之後走 Hevy `/v1/workouts/events` 增量（含刪除傳播）。

## 文件

| 文件 | 內容 |
|------|------|
| [docs/feature-landscape.md](docs/feature-landscape.md) | 任務 1：健身 app 差異化功能窮舉盤點 |
| [docs/api-analysis.md](docs/api-analysis.md) | 任務 2：Hevy / Liftosaur / wger API 與 data model 分析、冗餘精粹 (SSOT) |
| [docs/ir-spec.md](docs/ir-spec.md) | **FitIR 規格 v1.0**（封皮、四種 kind、詞彙映射、版本策略） |
| [docs/architecture.md](docs/architecture.md) | 系統架構、資料流、擴充新 app 的流程 |
| [docs/adr.md](docs/adr.md) | 架構決策紀錄 |
| [docs/specs/hevy-openapi.json](docs/specs/hevy-openapi.json) | Hevy OpenAPI spec 封存（一手資料） |

## 架構重點

- **前後端分離**：frontend 為零業務邏輯靜態 SPA（nginx 反代 `/api/`）；backend 為 FastAPI，
  自帶 OpenAPI 文件（`/docs`）。兩者僅以 JSON 溝通，可獨立替換。
- **IR 中樞**：新增 app 只需在 `backend/app/connectors/` 加一個 importer/exporter 模組並註冊
  （見 `connectors/base.py` protocol），互不相知（N+M 而非 N×M）。
- **版本相容**：FitIR 文件以原版本存檔、讀取時 lazy migration（`app/ir/migrate.py`）；
  minor 版只允許加 optional 欄位，讀取端忽略未知欄位。
- **SSOT**：資料庫只存 FitIR 文件 + 來源對照（refs）+ 原始 payload 封存（provenance）；
  可推導值（1RM、RPE↔RIR）一律即時計算不落地。

## 開發

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
DB_PATH=/tmp/fitir.db .venv/bin/uvicorn app.main:app --reload
```

安全備註：GUI 無認證，定位為個人內網工具；公開部署請在 nginx 層加 basic auth（見 docs/adr.md ADR-SEC-001）。
