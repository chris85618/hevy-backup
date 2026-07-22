# 三款 App API / Data Model 分析與冗餘精粹

> 任務 2 前半產出物。結論（IR 規格本體）見 `ir-spec.md`。
> 一手資料：`docs/specs/hevy-openapi.json`（自 api.hevyapp.com/docs 抽取）、wger OpenAPI schema（16,216 行，可自任一 wger 實例的 `/api/v2/schema` 取得）、https://www.liftosaur.com/doc/api。

## 1. Hevy API (v1)

- **認證**：`api-key` header（UUID，Pro 帳號）。分頁：`page`/`pageSize`（max 10）。
- **增量同步**：`GET /v1/workouts/events?since=` 回傳 updated/deleted 事件流（新→舊），官方設計目的即是讓 client 維持本地快取 — 本專案拉取機制的基礎。

### 資源與模型

| 資源 | 端點 | 模型重點 |
|------|------|----------|
| Workout | GET/POST /v1/workouts, GET/PUT /{id}, /count, /events | id(uuid), title, routine_id, start/end_time, exercises[] |
| └ Exercise(埋入) | — | index, title, notes, exercise_template_id, supersets_id(nullable) |
| └ Set(埋入) | — | index, type(normal/warmup/dropset/failure), weight_kg, reps, distance_meters, duration_seconds, rpe, custom_metric — 全 nullable |
| Routine | GET/POST /v1/routines, GET/PUT /{id} | 同 Workout 結構 + rest_seconds, rep_range{start,end} |
| ExerciseTemplate | GET/POST /v1/exercise_templates | id, title, type(8 種計量型), primary/secondary_muscle_group(20 值 enum), equipment_category(9 值 enum), is_custom |
| RoutineFolder | GET/POST /v1/routine_folders | id(int), index, title |
| BodyMeasurement | GET/POST /v1/body_measurements, GET/PUT /{date} | date 為主鍵；17 個固定 nullable 欄位（weight_kg, fat_percent, 圍度 *_cm…）；PUT 全覆寫 |
| ExerciseHistory | GET /v1/exercise_history/{templateId} | 攤平的 set 紀錄 + workout 脈絡 |
| UserInfo | GET /v1/user/info | id, name, url |

**特徵**：文件式（workout 內嵌 exercises/sets）、單位固定公制（weight_kg/meters/seconds）、RPE 尺度、集合型 enum 少而穩定。

## 2. Liftosaur API (v1)

- **認證**：`Authorization: Bearer lftsk_*`（Premium）。分頁：cursor。
- **核心特徵**：課表不是結構化資料，而是 **Liftoscript DSL 原始碼**（`text` 欄位）；workout 歷史同樣以「Liftoscript Workouts」文字格式表達。結構化模型僅存在於周邊（gym/equipment/exercise-data/measurements）。

| 資源 | 模型重點 |
|------|----------|
| Program | id, name, text(Liftoscript), isCurrent — 進程邏輯（`progress: lp(5lb)`）內嵌於程式碼 |
| History | id, text（timestamp/program/dayName/week/duration/exercises{sets×reps×weight, warmup, target, rest}） |
| Playground / Program-stats | 課表模擬（progression 試算）與靜態分析（每肌群組數、strength/hypertrophy 分類） |
| Gym / Equipment | 槓重 bar{lb,kg}、plates[{weight,num}]、fixed[], multiplier, isFixed — 器材物理建模 |
| ExerciseData | key=`{exerciseId}_{equipmentId}`, rm1, rounding, muscleMultipliers, isUnilateral |
| Measurement | 15 固定 key（weight/bodyfat/圍度左右分開）；值為「數字+單位字串」("185lb")；允許同 key 混單位 |

**特徵**：文字優先（程式即課表）、單位顯式標注（lb/kg 混用）、RPE 尺度、器材建模全市場最深。

## 3. wger API (v2)

- **認證**：Token / JWT。分頁：limit/offset。130 端點，唯一「全域可寫」的完整平台 API。
- **增量同步**：deletion-log + PowerSync 端點。

### 訓練域模型（與本專案相關的核心）

```
Routine (start/end date, fit_in_week, is_template, is_public)
 └─ Day (order, name, is_rest, need_logs_to_advance, type: custom/amrap/hiit/tabata/edt/rft/afap)
     └─ Slot (order, comment)            ← 同一 Slot 多個 Entry = superset
         └─ SlotEntry (exercise, type: normal/warmup/dropset/myo/partial/forced/tut/iso/jump,
                        repetition_unit, weight_unit, rounding)
             └─ 進程規則（每參數一張表，值隨 iteration 演進）:
                RepetitionsConfig / MaxRepetitionsConfig / WeightConfig / MaxWeightConfig /
                SetsConfig(SetNrConfig) / RestConfig / MaxRestConfig / RiRConfig / MaxRiRConfig
                各含: iteration, value, operation(+/-/r), step(na/abs/percent), repeat, requirements(JSON)
```

### 紀錄域

- **WorkoutSession**：uuid, date, routine, day, notes, impression(1-3), time_start/time_end。
- **WorkoutLog**：uuid, session, exercise, iteration, slot_entry, **repetitions vs repetitions_target / weight vs weight_target / rir vs rir_target / rest vs rest_target**（實際與計畫成對）、repetitions_unit / weight_unit（可為時間/距離單位，藉 RepetitionUnit.multiplier 換算至秒/公尺）。

### 其他域

Exercise DB（exercise/translation/alias/image/video/muscle/equipment/category/license，多語系社群資料庫）、體測（weightentry 獨立於 measurement-category/measurement 自訂類別）、營養域（ingredient/meal/nutritionplan/nutritiondiary，Open Food Facts）、gamification（trophy）、user 域。

**特徵**：高度正規化關聯模型（與 Hevy 文件式相反）、RIR 尺度、單位以 unit id + multiplier 表達、target/actual 成對、營養與訓練同庫。

## 4. 冗餘分析與單一事實來源 (SSOT) 精粹

三款 app 對同一事實的不同表達，逐項判定 canonical 形式：

| # | 事實 | Hevy | Liftosaur | wger | SSOT 精粹決策 (IR) |
|---|------|------|-----------|------|--------------------|
| R1 | 「一次訓練」 | Workout(含巢狀) | History text | WorkoutSession + WorkoutLog(攤平) | `Session` 文件：session 級中繼資料 + 巢狀 exercises/sets（文件式易同步、可整體 hash）。wger 的 session/log 拆分是同一事實的正規化投影，由 exporter 生成 |
| R2 | 「一組」的計量 | weight_kg/reps/duration/distance/custom（隱含單位） | "185lb" 字串（顯式單位） | value + unit_id + multiplier | `Quantity {value, unit}`，**canonical 單位 kg/m/s**；來源單位保留於 provenance。custom_metric → `extra_metrics` |
| R3 | 主觀強度 | RPE (0-10) | RPE | RIR | `effort {scale: rpe\|rir, value}` — 保存原始尺度（轉換 RPE=10−RIR 有損，由讀取端按需推導，不落地雙寫） |
| R4 | 組型 | 4 值 enum | warmup 標記 | 9 值 enum | IR 取聯集 enum（wger 超集 + failure），附映射表；未知值降級 normal + ext 保留 |
| R5 | superset | supersets_id (nullable int) | 文字表達 | 同一 Slot 多 Entry | `group_key`（同 key = 同一 superset/slot）— 兩種表達可雙向無損互轉 |
| R6 | 目標 vs 實際 | Routine(目標) 與 Workout(實際) 分離；rep_range | target 內嵌於 history text | *_target 與實際同筆 log | 計畫存於 `Plan`，實際存於 `Session`；Session set 額外帶 `prescription`（快照目標，含 rep range）— target 不是獨立事實，是 Plan 在該次執行的快照 |
| R7 | 進程規則 | 無（Hevy Trainer 黑箱） | Liftoscript 程式碼 | 9 張 Config 表 (iteration/operation/step) | `Progression` 統一為規則清單 `{param, iteration, op, step, value, repeat, condition}`（wger 可無損映射；Liftoscript 以 `ext.liftosaur.script` 原文保留 — DSL 不可靜態化為規則時不強制翻譯） |
| R8 | 運動定義 | ExerciseTemplate(enum 肌群) | exercise key + exerciseData | Exercise DB(關聯肌肉/器材/多語) | `Exercise`：canonical 名 + 肌群(取 Hevy 20 值 enum 為 IR 標準詞彙) + 計量型 + `refs[]` 保存各 app 外部 id — 肌群細節差異放 ext |
| R9 | 身體測量 | 17 固定欄位 | 15 固定 key | 自訂 category + weightentry | `BodyMetric {metric_key, at, quantity}` 時間序列（wger 的自訂模式為最一般形式）；固定欄位/固定 key 都是 metric_key 詞彙表的子集，IR 定義標準 key 詞彙 + 映射表 |
| R10 | 課表容器 | RoutineFolder > Routine | Program (單層) | Routine > Day > Slot | `Plan > PlanDay > entries(group_key)`；folder 僅是 UI 分類 → Plan.tags |
| R11 | 刪除傳播 | /workouts/events deleted | 無 | deletion-log | IR 文件 `deleted_at` 軟刪除（tombstone），匯出端各自翻譯 |
| R12 | 器材 | 9 值 enum | gym/equipment/plates 物理建模 | Equipment 名錄 | IR 核心僅存 `equipment_category`（Hevy enum 為詞彙）；Liftosaur 物理細節屬單一 app 深度功能 → `ext.liftosaur.*`（YAGNI：不進核心） |

**精粹原則總結**：
1. 每個事實在 IR 只有一種表達（上表 canonical 欄）；app 特有且不可一般化者進 `ext.<vendor>` 命名空間，不汙染核心。
2. 來源系統的 id 一律不是事實本體，收斂於 `refs[]`（provenance），核心以 IR 自有 id 為準。
3. 可推導值（1RM 估算、RPE↔RIR 換算、總量統計）一律不落地，由讀取端計算 — 避免雙寫失同步。
