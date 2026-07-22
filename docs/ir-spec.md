# FitIR — 健身資料中介表示 (Intermediate Representation) 規格 v1.0

> 任務 2 後半產出物。設計依據見 `api-analysis.md` §4（冗餘精粹）。
> 追溯：BG-002 → FEA-060 → FR-001~FR-012 → ADR-STR-001, ADR-STR-004。

## 0. 設計哲學（借鑑 LLVM IR）

| LLVM 概念 | FitIR 對應 |
|-----------|-----------|
| Frontend (C/Rust → IR) | **Importer**：Hevy/Liftosaur/wger 專屬模型 *lower* 成 FitIR |
| Backend (IR → x86/ARM) | **Exporter**：FitIR *lower* 成目標 app 的寫入格式 |
| IR 是唯一交換語言，前後端 N+M 而非 N×M | 新增 app 只需寫一個 importer 或 exporter，不需知道其他 app 存在 |
| 型別系統小而封閉 | 核心 kind 僅 4 種：`exercise` / `plan` / `session` / `body-metric` |
| Metadata / intrinsics 不破壞核心語義 | `ext.<vendor>` 命名空間：app 特有資料無損攜帶，核心讀取端可完全忽略 |
| IR 版本演進不破壞舊 bitcode | SemVer + additive-only minor + 註冊式 migration（§5） |
| SSA：每個值單一定義點 | 每個事實單一表達；來源 id 收斂為 `refs[]` provenance；可推導值不落地 |

## 1. 文件封皮 (Envelope)

所有 FitIR 實體都是一份 JSON 文件，共享封皮：

```jsonc
{
  "fitir": "1.0",              // 規格版本 (SemVer major.minor)
  "kind": "session",           // exercise | plan | session | body-metric
  "id": "ses_a1b2c3d4",        // IR 自有 id（前綴 + 穩定雜湊/uuid），全域唯一
  "refs": [                     // provenance：來源系統對照（SSOT 之外的別名）
    {"system": "hevy", "id": "b459cba5-...", "kind": "workout"}
  ],
  "created_at": "2026-07-23T00:00:00Z",
  "updated_at": "2026-07-23T00:00:00Z",
  "deleted_at": null,          // 軟刪除 tombstone（R11）
  "ext": {                      // vendor 命名空間，核心語義之外的無損攜帶
    "hevy": {"custom_metric_name": "floors"}
  },
  ...kind 專屬本體
}
```

## 2. 基礎型別

```
Quantity     := {value: number, unit: "kg"|"m"|"s"|"count"|"cm"|"percent"}
                // canonical 單位固定：重量 kg、距離 m、時間 s、圍度 cm。
                // 來源若為 lb/in/min，importer 換算後將原值存 ext（需要時）。
Effort       := {scale: "rpe"|"rir", value: number}   // 保留原始尺度，不轉換 (R3)
SetTag       := "normal"|"warmup"|"dropset"|"failure"|"myo"|"partial"|"forced"|"tut"|"iso"|"jump"
                // 聯集詞彙 (R4)；未知值 → "normal" + 原值進 ext
MetricKind   := "weight_reps"|"reps"|"bodyweight_reps"|"assisted_reps"|"duration"|
                "weight_duration"|"distance_duration"|"weight_distance"
MuscleGroup  := Hevy 20 值 enum（abdominals…other）作為 IR 標準詞彙 (R8)
EquipmentCat := Hevy 9 值 enum（none…other） (R12)
```

## 3. 核心 Kind

### 3.1 `exercise` — 運動定義（字典實體）

```jsonc
{
  "kind": "exercise",
  "id": "exr_bench_press_barbell",   // slug 化名稱 → 跨來源合併鍵
  "name": "Bench Press (Barbell)",
  "aliases": ["臥推"],
  "metric_kind": "weight_reps",
  "primary_muscles": ["chest"],
  "secondary_muscles": ["triceps", "shoulders"],
  "equipment_category": "barbell",
  "is_custom": false,
  "refs": [
    {"system": "hevy", "id": "05293BCA", "kind": "exercise_template"},
    {"system": "wger", "id": "73", "kind": "exercise"},
    {"system": "liftosaur", "id": "benchPress_barbell", "kind": "exercise_key"}
  ]
}
```

合併規則：importer 以「正規化名稱 + equipment_category」為合併鍵；命中既有 exercise 時僅追加 `refs`，不新建 — 這是跨 app 對齊的樞紐。

### 3.2 `plan` — 課表（目標側，R6/R7/R10）

```jsonc
{
  "kind": "plan",
  "id": "pln_...",
  "name": "Upper Body",
  "description": "",
  "tags": ["folder:Push Pull"],          // Hevy folder → tag (R10)
  "days": [
    {
      "name": "Day A", "order": 0, "is_rest": false,
      "entries": [
        {
          "order": 0,
          "exercise_id": "exr_bench_press_barbell",
          "group_key": null,               // 相同非空值 = superset/slot (R5)
          "rest": {"value": 60, "unit": "s"},
          "notes": "",
          "sets": [
            {
              "order": 0, "tag": "normal",
              "target": {                    // 各欄位皆 optional
                "reps": {"min": 8, "max": 12},        // 單值時 min==max
                "weight": {"value": 100, "unit": "kg"},
                "duration": null, "distance": null,
                "effort": {"scale": "rpe", "value": 8}
              }
            }
          ],
          "progression": [                  // 統一進程規則 (R7)，可為空
            {"param": "weight",             // weight|reps|sets|rest|effort
             "iteration": 2,                // 第 N 次執行起生效
             "op": "add",                   // add|subtract|replace
             "step": "abs",                 // abs|percent
             "value": 2.5,
             "repeat": true,
             "condition": null}             // 保留：wger requirements JSON
          ]
        }
      ]
    }
  ]
}
```

Liftoscript 無法無損靜態化時：`progression` 留空，原始碼存 `ext.liftosaur.script`（R7）。

### 3.3 `session` — 訓練紀錄（實際側，R1/R2/R6）

```jsonc
{
  "kind": "session",
  "id": "ses_...",
  "title": "Morning Workout",
  "plan_id": "pln_..." ,                   // nullable
  "started_at": "2026-07-20T12:00:00Z",
  "ended_at":   "2026-07-20T13:00:00Z",
  "notes": "",
  "mood": null,                             // 1|2|3 (wger impression) or null
  "exercises": [
    {
      "order": 0,
      "exercise_id": "exr_bench_press_barbell",
      "group_key": null,
      "notes": "",
      "sets": [
        {
          "order": 0, "tag": "normal",
          "actual": {
            "reps": 10,
            "weight": {"value": 100, "unit": "kg"},
            "duration": null,               // Quantity(s)
            "distance": null,               // Quantity(m)
            "effort": {"scale": "rpe", "value": 9.5},
            "extra_metrics": {"floors": 50} // Hevy custom_metric 等 (R2)
          },
          "prescription": null              // 執行當下的目標快照，同 target 結構 (R6)
        }
      ]
    }
  ]
}
```

### 3.4 `body-metric` — 身體測量時間序列（R9）

```jsonc
{
  "kind": "body-metric",
  "id": "bm_2026-07-20_weight",
  "metric_key": "weight",       // 標準詞彙見 §4.3
  "at": "2026-07-20",
  "quantity": {"value": 80.5, "unit": "kg"},
  "notes": ""
}
```

每筆一份文件（最一般形式，wger 模式）；Hevy 的 17 欄位列、Liftosaur 的 15 key 各自展開/收攏。

## 4. 詞彙映射表（importer/exporter 的 lowering 規則）

### 4.1 SetTag (R4)

| FitIR | Hevy | wger SlotEntryType | Liftosaur |
|-------|------|--------------------|-----------|
| normal | normal | normal | (default) |
| warmup | warmup | warmup | warmup set |
| dropset | dropset | dropset | — |
| failure | failure | —(→normal) | — |
| myo/partial/forced/tut/iso/jump | —(→ext) | 同名 | — |

### 4.2 MetricKind ↔ Hevy exercise type：同名直映（`short_distance_weight`→`weight_distance`）。

### 4.3 body metric_key 詞彙

| FitIR key | Hevy 欄位 | Liftosaur key | wger |
|-----------|-----------|---------------|------|
| weight | weight_kg | weight | weightentry |
| body_fat | fat_percent | bodyfat | 自訂 category |
| lean_mass | lean_mass_kg | — | 自訂 category |
| neck / shoulders / chest / waist / abdomen / hips | neck_cm / shoulder_cm / chest_cm / waist / abdomen / hips | neck / shoulders / chest / waist / — / hips | 自訂 category |
| bicep_left 等左右圍度 | left_bicep_cm 等 | bicepLeft 等 | 自訂 category |

（wger 端 exporter 首次匯出時自動建立同名 measurement-category，之後以 refs 記住 category uuid。）

### 4.4 Effort (R3)：Hevy/Liftosaur 產生 `{scale:"rpe"}`；wger 產生 `{scale:"rir"}`。匯出到對方尺度時以 `RPE = 10 − RIR` 推導，並於匯出紀錄標注 derived。

## 5. 版本策略（相容性演進，ADR-STR-004）

1. **版號**：封皮 `fitir: "MAJOR.MINOR"`。
2. **Minor（1.0 → 1.1）**：只允許「新增 optional 欄位」與「enum 尾端加值」。讀取端規則：**忽略未知欄位、未知 enum 值降級到既定 fallback**（SetTag→normal 等）。舊文件無需遷移即為合法新版文件。
3. **Major（1.x → 2.0）**：允許破壞性變更，但必須同時提交 `migrate_1_to_2()` 純函式並註冊。儲存層**保留原版本文件不重寫**（如 LLVM bitcode），讀取路徑上按需鏈式遷移 1.0→1.1→…→2.0（lazy migration）。
4. **ext 契約**：核心永不讀取 `ext.*` 內容；vendor 欄位變動不構成 IR 版本變更。
5. **id 穩定性**：IR id 一旦簽發永不變更；合併實體時保留全部 refs 並以 tombstone + `replaced_by` 指向存續者。
6. 實作位置：`backend/app/ir/schema.py`（Pydantic 模型 = 規格的可執行形式）與 `backend/app/ir/migrate.py`（遷移註冊表）。

## 6. SSOT 執行規則

- 資料庫僅儲存 FitIR 文件 + refs 索引 + 原始 payload 封存（provenance，不作為讀取來源）。
- 任何 app 專屬格式（含 wger 的 session/log 拆分、Hevy 的巢狀 workout）只存在於 importer/exporter 的邊界，一律不落地。
- 可推導值（1RM、量統計、RPE↔RIR）由 API 層即時計算。
