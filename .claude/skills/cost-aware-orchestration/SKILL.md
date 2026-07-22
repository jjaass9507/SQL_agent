---
name: cost-aware-orchestration
description: Use when deciding how to handle a new task — whether to answer/edit directly, delegate to a subagent (and which model tier), whether to enter plan mode, whether to resume an existing subagent or spawn a new one, or when the user asks how to reduce token consumption.
---

# 成本意識工作分工

給主模型（統籌者）的行為指引：收到任務時，先依此判斷處理方式，而不是預設派代理或進 plan mode。

## 1. 任務分級決策表

| 任務型態 | 處理方式 |
|---|---|
| 提問 / 諮詢 / 一兩行修改 | 主模型直接回答或修改，不派代理、不進 plan mode |
| 預期改動 < 50 行 | 主模型直接做，改完驗證即 commit |
| 預期改動 ≥ 50 行或涉及多檔案的實作 | 規格寫入檔案 → 派 Sonnet 子代理實作 → 統籌者驗收 |
| 機械性批次任務（跑測試、格式化、大量重複性修改） | 派 Haiku 子代理 |
| 架構級大改動（需使用者核准方向後才動工） | 才使用 plan mode |

判斷順序：先估計改動規模與是否機械性重複，再決定要不要派代理；不要因為任務「聽起來重要」就升級處理方式。plan mode 只在需要使用者對方向做決策時使用，不要用它取代規格文件。

## 2. 派代理的固定流程（spec-in-file 模式）

派子代理實作前：

1. 統籌者先把規格寫成檔案，內容至少包含：
   - 介面定義（函式簽章、API、資料結構）
   - 行為保持清單（哪些現有行為不能變）
   - 不可變動清單（哪些檔案/邏輯禁止觸碰）
   - 回報格式（要子代理最後如何總結）
2. 派代理的 prompt 只放任務摘要 + 規格檔路徑，不要把規格內容重複貼進 prompt。
3. 子代理**不**負責 commit / push。
4. 統籌者逐項驗收：讀 diff、跑編譯/測試，確認符合規格後才自行 commit。

不要跳過驗收直接信任子代理的完成回報；子代理的總結只反映它「打算做的事」，不代表實際結果。

## 3. 續用 vs 新開代理

- 後續任務與某個子代理先前的工作直接相關、且該代理的 transcript 還短 → 用 SendMessage 續用同一個代理，帶著既有上下文。
- 該代理 transcript 已累積多輪（續用需要重播大量歷史、成本已偏高）或新任務與舊工作無關 → 開新代理，並附上規格檔路徑，不依賴舊 transcript。

判斷準則是「重播成本 vs 上下文價值」，不是單純看任務是否同一主題。

## 4. 對話與 session 習慣

- 把相關需求合併成一則訊息處理，目標是一次規格、一次驗收、一次 commit，避免來回瑣碎訊息推高 token 消耗。
- 話題完全切換時開新 session；同一主題的後續工作留在原 session，以利用 prompt cache。
- 日常小維護、小修改：直接在 Sonnet 5 當主模型的 session 處理即可。
- 大型重構、跨檔案架構調整、需要多階段規劃與驗收的工作：才用 Fable 5 主模型統籌 + Sonnet/Haiku 子代理執行的分工模式。

## 使用者詢問「如何省 token」時

直接引用上述四節重點作答：分級處理、規格寫檔避免重複貼內容、續用代理需評估 transcript 長度、合併訊息並善用 session/prompt cache。不要另外發明新規則，以此 skill 內容為準。
