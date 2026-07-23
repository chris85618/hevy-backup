# Workflow State

- **Pipeline Position**: Phase 9 完成 — 真實資料端對端驗證通過：Hevy 拉取 → FitIR → wger push 全自動。5 sessions / 226 workoutlogs 已在 wger 實例上（archlinux.chrischen.org:9000），preview pending=0、unresolved=0
- **Last Updated**: 2026-07-23
- **Gate Status**: Stage 3-8 由使用者單一 prompt 預先授權通過；Phase 9 驗收標準（2026-07-23 規格）全數通過
- **環境注意**:
  - 使用者 Docker 為 legacy builder（無 BuildKit），Dockerfile 禁用 `COPY --chmod`，需以 `RUN chmod` 替代
  - `data/` 目錄為 root 擁有（docker 建立），host 端寫入需經 `docker compose exec backend`
  - wger 實例為 2.7.0a1：`/exercise/search/` 已移除（404）；exercise-translation 的 `description_source` 必填且 ≥40 字元；repetition_unit 3 = Seconds
  - wger 使用者 chris85618 已設為 superuser（exercise 建立需 trustworthy；此為單人自架實例）
- **Exercise 解析架構（2026-07-23 規格實作）**: `data/wger-mapping.yaml` 設定 resolver pipeline：manual(refs/GUI) → override(yaml) → catalog(exercise-translation+alias 本地比對，variants: paren_equipment/singularize/token_sort/spaceless) → create(自動建立，保底)。push 前批次重驗 refs、workoutlog 400 時失效重解重試。wger push 已掛入排程（Hevy 拉完即推）。audit（created_exercises/invalidated_refs）寫入 sync_runs.detail
- **Pending Escalations**:
  - RISK-001: Web GUI 無認證（內網定位，公開部署需 nginx basic auth）— 已於 adr.md ADR-SEC-001 揭露
  - DEBT-002 (P3): Liftosaur connector 未實作（IR 已預留 ext.liftosaur 與 refs 結構）
  - DEBT-003 (P3): sync_interval 變更需重啟後端才生效
  - DEBT-004 (P2): wger session push 非交易性 — workoutsession 建立後若 log 中途失敗，ref 未寫入，重跑會另建新 session，遺留部分匯出的孤兒 session（2026-07-23 已手動清除 2 個；長期需 push 前偵測同日未 ref session 或失敗時回滾刪除）
- **已解決**:
  - DEBT-001 ✅ (2026-07-23): exercise 自動解析真實驗證 — 41 個 pending 動作 31 個 catalog 命中、10 個 create 自動建立（含 McGill改良式捲腹/RKC棒式/側棒式）；`/exercise/search/` 依賴已移除
- **Session Summary (2026-07-23, 後段)**: 依討論定案規格實作 resolver pipeline（wger.py 重構 + wger-mapping.yaml + db.delete_ref + sync.run_export 排程整合 + PyYAML）。過程中修復三個 wger 2.7 相容性問題（search 404 → catalog 比對；description_source 40 字元；duration-only set 降階為 repetition_unit=Seconds）。5/5 sessions 匯出、0 errors；孤兒 exercise 11 個與孤兒 session 2 個已清理
