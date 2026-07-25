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

## ADR-STR-006: 全面增量匯出 — export_state 變更偵測 + 刪除標記策略
- **決策**（2026-07-23，使用者定案範圍）：wger push 從「僅新 session/weight」擴為全 IR 增量：session 新增/更新、plan（routine）、全部 body-metric、auto-created exercise 中繼資料回寫。
- **變更偵測**：新表 `export_state(system, ir_kind, ir_id, pushed_updated_at)`；dirty = `doc.updated_at != pushed_updated_at`。~~升級前已推送的文件首次掃描時「認養為 clean」，避免歷史全量重推~~（認養機制已由 ADR-STR-008 廢除：它無法區分「升級前推過」與「半途失敗」，會把 partial push 永久凍結；升級遷移已於 2026-07-23 完成，認養分支只剩陷阱面）。配套：`db.put_doc_if_changed`（內容不變不動 `updated_at`，否則 body-metric 每次拉取都誤判 dirty；反向不變量：內容變了但來源時間戳沒動 → 強制 bump 到 now，否則 lowering 邏輯修正永遠推不出去，2026-07-23 superset 修復實證）；`now_iso` 升為微秒精度（同秒雙寫需可區分）；Hevy 刪除事件補 bump `updated_at`。
- **Session 更新** = PATCH workoutsession + 刪舊 log（`?session=` 過濾 + 逐筆核對後才刪）+ 重建。順帶緩解 DEBT-004（重推可自癒部分匯出）。
- **刪除策略**（使用者決策：備註標記）：Hevy 刪除的訓練不刪 wger 資料，僅在 notes 加 `[deleted in Hevy] ` 前綴；冪等（已有前綴不重複加）。
- **Exercise 回寫**：僅回寫本橋自建者（以 `exercise_translation` ref 識別）；共享目錄項永不觸碰。限制：升級前 auto-create 的動作無 translation ref，不回寫。

## ADR-STR-007: Plan → wger 2.7 routine 降階（有損）
- **決策**：IR plan 匯為 `/routine/ + /day/ + /slot/ + /slot-entry/ + *-config`（iteration=1, operation=r, step=na，實測驗證）。更新 = PATCH routine 本體 + 刪除其 days 後重建（day 級聯 slots/entries/configs），拒絕 diff（Ockham）。
- **修訂**（2026-07-23，session↔routine 關聯修復）：原「DELETE routine 後重建」已廢棄 — wger `WorkoutSession.routine` 為 `on_delete=CASCADE`，刪 routine 會連帶刪除所有已關聯 session 與其 logs。routine row 必須存活以保持 id 穩定。`/day/` 無 routine filter（wger 2.7 filterset 未含），改為抓取使用者全部 days 後 client 端比對刪除。
- **Session 關聯**：session 匯出時帶 `routine`（來自 IR `plan_id` → routine ref；push 順序改為 plans 先於 sessions，fresh instance 全量匯出時 ref 才存在）；workoutlog 帶 session 實際連結的 routine（以 wger 回應為準）。wger 有 unique(date, user, routine) 約束：同 routine 同日第二個 session 於 400 時退回不關聯（`_send_session` fallback），不使匯出失敗。
- **修訂**（2026-07-23，template 語意對齊，FR-029）：概念修正 — Hevy routine 是「訓練時即時取用的敏捷範本」，語意對應 wger 的 **template**（`is_template=True` 的 routine），不是 wger 的行事曆型執行 routine。因此每個 plan 推送**兩棵** routine 樹：(1) template（ref kind `template`，`is_template=True`，凍結的範本，對應 wger UI 的 Templates 清單）；(2) 執行 routine（ref kind `routine`，`is_template=False`），等同使用者「用 template 建立計畫」，session 與 workoutlog 全部掛在執行 routine 上（延用既有 `routine` ref kind，故既有實例零遷移 — 已掛 session 的舊 routine 就地 PATCH 成執行 routine，template 新建補齊；`_plan_work` 對缺 template ref 的 plan 觸發一次性重推）。執行 routine 日期範圍 = 該 plan 最早 session 日 → 今日+70 天（受 wger `MAX_DURATION_DAYS=120` 限制，過長時 start 向後截斷；真正的日誌關聯是 FK 不是日期窗）。實測 (2026-07-23)：templates 6/7 建立（各 1 day/7 slots，superset 結構同執行 routine）、執行 routine 1/2 就地轉換（is_template=False、start 回溯至 07-18/07-19）、194 logs 與 4 sessions 關聯不動、0 errors、preview 收斂全零。
- **有損映射**：wger config 為 per-entry，Hevy target 為 per-set → 取第一個有 target 的 set 供值；sets-config = set 數。duration-only 降為 repetition_unit=3（秒），與 workoutlog 同法。superset：連續同 group_key entry 共享一個 slot。
- **常數**：routine name 截 25 字元、day name 截 20、notes 截 100；template 的 start=推送日、end=+70 天（wger 必填欄位，template 凍結後日期無實質作用）。

## ADR-STR-008: 收斂式匯出狀態機 — 最終一致性取代原子性
- **背景**（2026-07-25，使用者實驗發現）：純排程自動化（無人工監督首推）會產生不可逆 partial sync。三個根因：(1) `_status` 的「認養為 clean」分支把半推送文件（ref 已寫、export_state 缺）永久凍結；(2) `_push_session` 在 logs 全寫完後才寫 ref，logs 中途失敗留下 wger 孤兒 session，下輪重試 POST 撞 unique(date,user,routine) 400 → `_send_session` fallback 剝掉 routine → 產生永久脫鉤的重複件；(3) `hevy_last_sync` 一次性寫死且無強制全量入口，狀態污染後只能砍掉重來。
- **決策**（使用者定案方向：增量 + timestamp-based snapshot、pull/push 狀態解耦、最終一致性優於原子操作）：
  - **語意分離**：`refs` = 身分（wger 物件已存在，防重複建立）；`export_state` = 完成度（整份文件完整落地當下的 `updated_at` 快照）。ref 存在但 export_state 缺/落後 → 一律 "changed"，走冪等 update 路徑重推。認養分支移除。
  - **ref 前移**：`_push_session` 在 POST workoutsession 成功後、寫 logs 前就寫 ref。logs 失敗時下輪以同一 wger session 走 `_update_session`（PATCH + 刪 log 重建）自癒，不再產生孤兒或脫鉤重複件。
  - **不變量**：任何一次成功的 push run ⇒ wger 反映 pull 端最新快照（最終一致性）。失敗只能讓文件停留在 dirty，永不得轉為 clean。
  - **pull/push 解耦**：`hevy_last_sync`（producer 水位）與 `export_state`（consumer 游標）互不影響；push 失敗不阻擋 pull 前進，pull 照常推進水位。
  - **重建基線逃生口**：`POST /api/sync/run?full=true`（清水位重走全量拉取）、`POST /api/export/wger?force=true`（`db.clear_export_state` 全轉 dirty，經冪等 upsert + PATCH-404-recreate 重落地，wger 實例被重置也能自癒，免換 bridge）。
- **取捨**：拒絕交易性/原子匯出（wger API 無交易能力，模擬原子性需補償邏輯且仍有窗口）；接受「wger 短暫存在 half-pushed 文件」換取任意時點可重入收斂。升級成本：極舊 DB（refs 有、export_state 無）首輪會全量冪等重推一次 — 這正是修正後的正確行為。
- **驗證**：離線收斂測試 18/18（scratchpad test_convergence.py，FakeWger 模擬）：logs 中途失敗 → 下輪 update 自癒無重複件；plan 半推送 → 重推非認養；force → 冪等重落地；各劇本後均靜默收斂。

## ADR-SEC-001: 金鑰處理
- **決策**：金鑰來源 env 或 GUI 設定（存 DB settings 表，明文，檔案權限保護）；`.env`/`data/` 進 `.gitignore`；日誌不輸出金鑰。
- **風險承認**：GUI 無認證（RISK-001，內網個人工具定位；公開部署需 nginx basic auth，文件已註明）。
