# 健身規劃/紀錄 App 差異化功能盤點 (FEA Registry)

> 任務 1 產出物。調查日期：2026-07-23。
> 方法：市場比較文章 + 三款目標 app（Hevy / Liftosaur / wger）官方 API 文件實際分析。
> 追溯：BG-001（跨 app 資料可攜）、BG-002（單一事實來源的訓練資料中樞）。

## 1. 紀錄體驗 (Logging UX)

| ID | 功能 | 代表 app | 差異化要點 |
|----|------|----------|-----------|
| FEA-001 | 快速登錄（上次數值預填、一鍵完成組） | Strong, Hevy | Strong 定位「健身房的數位筆記本」，登錄速度是核心賣點 |
| FEA-002 | 組間休息計時器（自動啟動、通知） | Hevy, Strong, Liftosaur | wger 以 RestConfig 規則型態存在但 UX 較弱 |
| FEA-003 | 槓片計算機 (plate calculator) | Boostcamp, Liftosaur | Liftosaur 進一步以 gym/equipment 模型記錄每間健身房的槓鈴與槓片庫存 |
| FEA-004 | 特殊組型：superset / dropset / warmup / failure / myo-reps / partial / iso | Hevy(4 種), wger(9 種) | wger 的 SlotEntryTypeEnum 是三款中最細的分類 |
| FEA-005 | 離線紀錄與同步 | Hevy, Liftosaur | 自架方案 (wger) 依賴連線，行動端以 PowerSync 補強 |
| FEA-006 | 手錶 app (Apple Watch / WearOS) | Hevy, Strong | Android 端 Hevy 對等性較佳 |
| FEA-007 | 多型態運動計量（重量×次數 / 僅次數 / 時間 / 距離 / 樓層等自訂計量） | Hevy | Hevy set 有 8 種 exercise type 與 custom_metric 欄位 |

## 2. 課表與編程 (Programming)

| ID | 功能 | 代表 app | 差異化要點 |
|----|------|----------|-----------|
| FEA-010 | 課表模板與資料夾管理 | Hevy (routine_folders), Strong | |
| FEA-011 | 社群課表市集（nSuns, GZCLP, 5/3/1, PPL…） | Boostcamp (11,000+), Liftosaur (內建 GZCL 全系列) | Boostcamp 免費層即含全部課表庫 |
| FEA-012 | **可程式化進程 DSL** (Liftoscript) | Liftosaur（獨有） | 以文字語言描述課表與進程邏輯（`progress: lp(5lb)`），可版本控制、可用 playground API 模擬進程 |
| FEA-013 | 規則式自動進程（週期迭代 + 條件） | wger | (Max)Repetitions/Weight/RiR/Rest/SetNr Config 以 iteration+operation+step+requirements 描述逐週進程 |
| FEA-014 | AI 自適應課表（依表現/恢復調整） | Fitbod, Hevy Trainer, Alpha Progression | Alpha Progression 以每肌群週訓練量目標為核心；Fitbod 分析每一組紀錄 |
| FEA-015 | rep range 目標 (8-12) 與 target/actual 對照 | Hevy (rep_range), wger (repetitions_target vs repetitions) | wger 每筆 log 同時保存目標與實際 |
| FEA-016 | 週期化結構（week/day、rest day、fit_in_week） | Liftosaur, wger | wger Day 有 is_rest、need_logs_to_advance；DayTypeEnum 支援 AMRAP/HIIT/Tabata/EDT/RFT 等課型 |
| FEA-017 | 課表模擬與統計（時長估計、每肌群組數、strength/hypertrophy 分類） | Liftosaur (/program-stats, /playground) | 獨有的「執行前靜態分析」能力 |

## 3. 分析與洞察 (Analytics)

| ID | 功能 | 代表 app | 差異化要點 |
|----|------|----------|-----------|
| FEA-020 | 1RM 估算與 PR 追蹤 | 全部 | Liftosaur 將 rm1 作為 exercise-data 可寫欄位，進程腳本可引用 |
| FEA-021 | 每肌群訓練量統計 | Alpha Progression, Liftosaur | |
| FEA-022 | RPE / RIR 紀錄 | Hevy (RPE), wger (RiR), Boostcamp (兩者) | 兩種主觀強度尺度並存，整合時需正規化（RPE ≈ 10 − RIR） |
| FEA-023 | 恢復感知調整（HRV/睡眠 → 當日課表） | 市場空缺 | 2026 比較文指出無主流 app 完整實現 |
| FEA-024 | 運動歷史查詢（單一動作跨 workout） | Hevy (/exercise_history) | |

## 4. 社群 (Social)

| ID | 功能 | 代表 app | 差異化要點 |
|----|------|----------|-----------|
| FEA-030 | 社群動態、追蹤、按讚 | Hevy（最強差異化） | 「健身版 Strava」定位 |
| FEA-031 | 公開課表分享 | wger (is_public template, public-templates API), Hevy | |
| FEA-032 | 遊戲化獎盃/成就 | wger (trophy / user-trophy / trophy progress API) | 自架方案中少見的完整成就系統 |

## 5. 生態與資料可攜 (Ecosystem & Portability)

| ID | 功能 | 代表 app | 差異化要點 |
|----|------|----------|-----------|
| FEA-040 | 公開 REST API | wger（全功能 130 端點）> Liftosaur（需付費訂閱）> Hevy（Pro 限定、唯讀為主） | 本專案的整合基礎 |
| FEA-041 | 增量同步機制 | Hevy (/workouts/events since=)、wger (deletion-log, PowerSync) | 兩者皆為「client cache 保持最新」設計 |
| FEA-042 | Apple Health / Google Fit / Strava 整合 | Hevy, Strong | |
| FEA-043 | CSV / JSON 匯出 | Strong, Hevy, wger | 格式互不相容 → BG-001 的根因 |
| FEA-044 | 自架 + FLOSS + Docker 部署 | wger（獨有） | 資料主權；AGPL；Open Food Facts 食品資料庫 |

## 6. 週邊域 (Adjacent Domains)

| ID | 功能 | 代表 app | 差異化要點 |
|----|------|----------|-----------|
| FEA-050 | 營養追蹤（食品資料庫、餐點計畫、營養日記） | wger (ingredient/meal/nutritiondiary/nutritionplan) | 訓練+營養一體是 wger 對比純訓練 app 的最大差異 |
| FEA-051 | 身體圍度測量 | Hevy (17 固定欄位), Liftosaur (15 固定 key), wger (自訂 category 無上限) | 三種建模策略：固定欄位 vs 固定 key vs 自訂類別 |
| FEA-052 | 體重時間序列 | 全部（wger 獨立 weightentry） | |
| FEA-053 | 進步照片 gallery | wger | |
| FEA-054 | 器材/健身房建模（槓重、槓片、固定重量、單邊倍率） | Liftosaur（獨有深度） | gym → equipment → plates/bar/multiplier |
| FEA-055 | 多語系運動資料庫（別名、翻譯、影片、授權） | wger (exercise-translation/alias/video/license) | 社群維護的開放運動資料庫 |

## 7. 市場空缺（本專案機會）

| ID | 空缺 | 說明 |
|----|------|------|
| FEA-060 | 跨 app 資料中樞 | 無任何 app 提供「以中介模型雙向流動資料」；各家匯出格式互不相容 → 本專案核心 (BG-001, BG-002) |
| FEA-061 | 恢復感知編程 | FEA-023 空缺，IR 若涵蓋 effort/body-metric 時間序列即為未來擴充點 |

## 資料來源

- [Hevy vs Strong vs Fitbod vs Jefit (SensAI, 2026)](https://www.sensai.fit/blog/hevy-vs-strong-vs-fitbod-vs-jefit)
- [Best Workout Tracker Apps For 2026 (Fitbod)](https://fitbod.me/blog/best-workout-tracker-apps-for-2026/)
- [Best Workout Tracker Apps 2026 (Workout Lab)](https://workoutlab.app/blog/workout-lab-vs-strong-hevy-fitbod-comparison/)
- [wger GitHub](https://github.com/wger-project/wger) / [wger 自架指南 (XDA)](https://www.xda-developers.com/wger-guide/)
- [Liftosaur 官方概覽](https://www.liftosaur.com/blog/posts/liftosaur-overview/) / [Liftoscript 文件](https://www.liftosaur.com/doc/liftoscript)
- [Best Apps for Hypertrophy (Boostcamp)](https://www.boostcamp.app/best/hypertrophy)
- [Alpha Progression Review (hotelgyms)](https://www.hotelgyms.com/blog/alpha-progression-the-gym-logger-app-from-germany)
- 一手資料：`docs/specs/hevy-openapi.json`、wger `/api/v2/schema` OpenAPI、https://www.liftosaur.com/doc/api
