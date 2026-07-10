---
name: fable-architect
description: 高階模型（Fable）只負責架構設計與功能規劃，產出可由 Sonnet 獨立執行的實作計畫，程式碼修改一律派發給 sonnet subagent 執行，最大化 Fable token 使用效率。適用於新功能、重構、跨多檔案修改等需要先規劃再實作的任務。
---

# Fable Architect：規劃／執行分工流程

## 目的

Fable 的 token 成本高，只應花在高價值工作上：理解需求、架構決策、撰寫計畫、審查結果。
所有機械性的探索與程式碼修改，分別交給 Explore subagent 與 Sonnet 執行者完成。

## 流程

### Phase 1 — 探索（省 token）

- **不要**自己大量 `Read` 檔案。需要了解程式碼結構時，派 `Explore` subagent 收集資訊，只拿回結論。
- 只有做關鍵架構決策必須親自確認的少數檔案，才直接 `Read`（盡量指定 offset/limit 只讀相關區段）。
- 需求模糊時，先用 `AskUserQuestion` 釐清，再開始規劃。

### Phase 2 — 規劃（Fable 的核心工作）

- 產出實作計畫，寫入 `.claude/plans/<任務代號>.md`（任務代號用簡短英文 slug）。
- 計畫必須 **self-contained**：執行者拿到單一步驟就能動手，不需要再問問題、
  不需要重新探索整個 codebase、不需要做任何架構決策。
- 每個步驟必須包含（見下方模板）：
  - 目標（一句話）
  - 涉及檔案的完整路徑，以及要改動的函式／區段
  - 具體修改內容 — 關鍵處直接給出程式碼片段、函式簽名或介面定義
  - 可執行的驗證指令（測試、import 檢查等）
  - 相依關係（依賴哪些步驟；沒有依賴的標記為可平行）

### Phase 3 — 派發執行（Sonnet subagent）

- 用 `Agent` tool 以 `subagent_type: "plan-executor"` 派發每個步驟
  （該 agent 定義已固定 `model: sonnet`，不需要另外指定 model）。
- prompt 中直接**貼上該步驟的全文**（含計畫檔路徑供參照），避免執行者重讀整份計畫。
- 無相依關係的步驟，在**同一則訊息中平行派發**多個 subagent。
- 有相依關係的步驟，等前置步驟完成並通過審查後再派發。

### Phase 4 — 審查與整合（Fable）

- 用 `git diff` 審查修改，**不要**重讀整個檔案。
- 執行計畫中的驗證指令（如 `python -m pytest tests/ -x -q`）。
- 發現問題時，用 `SendMessage` 讓原 subagent 修正（保留它已有的 context），
  不要重新 spawn；只有一兩行的小修才由 Fable 自己動手。
- 全部完成後：依 CLAUDE.md 的文件規範同步更新 README / docs，再 commit。

## 計畫模板

```markdown
# 計畫：<任務名稱>

## 背景與目標
<為什麼做、做完的樣子>

## 架構決策
<關鍵取捨與決定，執行者不得推翻>

## 步驟

### Step 1：<標題>（可平行 / 依賴 Step N）
- 目標：...
- 檔案：`path/to/file.py` 的 `function_name()`
- 修改內容：
  <具體描述；關鍵處給程式碼片段或簽名>
- 驗證：`<可執行指令>`

### Step 2：...

## 整體驗證
<所有步驟完成後的最終驗證指令>
```

## Token 效率守則

1. Fable 只做：需求理解、架構決策、計畫撰寫、diff 審查、整合。
2. 探索交給 `Explore`，程式碼修改交給 `plan-executor`。
3. 獨立步驟一律平行派發。
4. 給 subagent 的 prompt 必須自足，避免它為了補齊 context 反覆探索。
5. 修正用 `SendMessage` 續用原 subagent，不要重新 spawn 從零開始。
6. 審查看 diff，不重讀全檔。
