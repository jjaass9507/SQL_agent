# Claude Code Skills 使用說明

專案層級的 Claude Code skills。放在 `.claude/skills/<name>/SKILL.md`，開啟本 repo 的
Claude Code session（含 web session）會自動載入。不需安裝、不需網路、不需 Node.js —
檔案本身就是全部。

## 目前有哪些

| Skill | 觸發方式 | 做什麼 |
|-------|----------|--------|
| `cost-aware-orchestration` | 自動 | 決定任務要主模型直接做、派子代理、還是進 plan mode |
| `styleseed` | 自動 | 前端 UI／設計系統的資深設計判斷 |
| `ponytail` | `/ponytail [lite\|full\|ultra]` | 懶惰資深工程師模式，強制最短可動解法 |
| `i-have-adhd` | `/i-have-adhd` | 把回應排版成 ADHD 讀者可直接行動的形式 |
| `ponytail-review` | `/ponytail-review` | 針對目前 diff 找過度設計 |
| `ponytail-audit` | `/ponytail-audit` | 全 repo 過度設計盤點 |
| `ponytail-debt` | `/ponytail-debt` | 收集 `ponytail:` 註記成技術債帳本 |
| `ponytail-gain` | `/ponytail-gain` | ponytail 的 benchmark 成效卡 |
| `ponytail-help` | `/ponytail-help` | ponytail 指令速查卡 |

三種型態：

- **自動觸發** — `cost-aware-orchestration`、`styleseed`。符合條件時模型自己載入，不用打指令。
- **持續模式** — `/ponytail`、`/i-have-adhd`。打一次持續到 session 結束，要手動關。
- **一次性報告** — 四個 `ponytail-*`。跑完出報告就結束，只讀不寫，不會動到任何檔案。

### 開與關（只有持續模式需要）

```
/ponytail            # 開啟（預設 full 強度）
/ponytail ultra      # 直接切強度，不用先關
stop ponytail        # 關閉

/i-have-adhd         # 開啟
stop adhd mode       # 關閉

normal mode          # 一次關掉兩個
```

Session 重開後兩者都是關閉狀態。

---

## cost-aware-orchestration

本專案自製，給主模型的分工指引：收到任務時先判斷處理方式，而不是預設派代理或進
plan mode。核心是一張分級表 — 提問和一兩行修改主模型直接做，< 50 行直接做，
≥ 50 行或跨檔案實作才寫規格派 Sonnet 子代理，機械性批次任務派 Haiku，只有需要
使用者對方向做決策時才用 plan mode。

自動觸發，也可以直接問「這個任務該怎麼分工」。

## styleseed

前端 UI 的設計判斷引擎，解決「技術上正確但一看就是 AI 做的」介面。內含 74 條視覺
規則、一致性法則、動效語彙、UX 文案準則，以及分領域／分頁型的 playbook。

`SKILL.md` 只是入口，實際內容在旁邊的目錄，依任務需要載入：

- `references/` — 方法論、設計語言、規則集、頁型手冊，以及 `commands/` 底下 20 份
  step-by-step playbook（`ss-page`、`ss-review`、`ss-score`、`ss-motion`、`ss-a11y` 等）
- `assets/` — 可直接取用的 React 元件、CSS、design token、5 組動效種子、7 套品牌皮膚
  （arc / linear / notion / raycast / stripe / toss / vercel）

自動觸發。本專案 Phase 8 的「Pro Space Gray」深色設計以
`docs/design/stitch_design_pro_space_gray.md` 為權威規格，styleseed 是輔助判斷用，
兩者衝突時以該規格為準。

授權：MIT，移植自 bitjaru/styleseed，全文見 `styleseed/LICENSE`。

---

## ponytail

「懶惰資深工程師」模式：強制最短、最簡單、可動的解法。核心是一道階梯，停在第一個
成立的階：

1. 這東西需要存在嗎？（YAGNI）
2. 這個 codebase 裡已經有了嗎？
3. 標準函式庫做得到嗎？
4. 平台原生功能覆蓋得了嗎？（DB constraint 優於 app code、CSS 優於 JS）
5. 已安裝的套件解得掉嗎？
6. 能不能一行？
7. 到這裡才寫：能動的最小實作。

### 三種強度

| 強度 | 行為 |
|------|------|
| `lite` | 照你要的做，另外用一行點出更懶的替代方案，你自己選 |
| `full` | 階梯全程執行，最短 diff、最短說明。**預設** |
| `ultra` | YAGNI 極端派，先刪再加，直接質疑需求本身該不該存在 |

範例（「幫 API 回應加個快取」）：

- `lite`：加好了。順帶一提 `functools.lru_cache` 一行就能覆蓋這個情境。
- `full`：`@lru_cache(maxsize=1000)` 掛在 fetch function 上。略過自訂快取類別，等 lru_cache 明顯不夠用再加。
- `ultra`：在 profiler 說話之前不要快取。真的要時就 `@lru_cache`。手刻 TTL 快取類別是個附帶命中率的 bug 農場。

### 幾個不會被簡化掉的東西

信任邊界的輸入驗證、防止資料遺失的錯誤處理、安全措施、無障礙基本要求、以及你明確
要求的東西。另外非 trivial 的邏輯（分支、迴圈、parser、金流／安全路徑）它會留一個
最小的可執行檢查，不會裸奔。

### `ponytail:` 註記

ponytail 刻意走捷徑時會留下註記，寫明「天花板」和「什麼時候該升級」：

```python
# ponytail: 全域鎖，吞吐量成為瓶頸時改成 per-account 鎖
```

`/ponytail-debt` 就是把這些註記掃出來變成帳本，避免「之後再說」變成「永遠不做」。

### 一次性報告指令

- **`/ponytail-review`** — 看目前的 diff，逐行列出可刪的部分，格式是
  `L42: yagni: 只有一個實作的 factory。直接 inline。`，結尾給 `net: -N lines possible.`
- **`/ponytail-audit`** — 同樣的事情但掃全 repo，依可刪除量由大到小排序。
- **`/ponytail-debt`** — 掃 `ponytail:` 註記，沒寫升級條件的會標 `no-trigger`（最容易爛掉的那種）。
- **`/ponytail-gain`** — 上游 benchmark 中位數，**不是**本 repo 的實測數字。

`/ponytail-review` 和 `/ponytail-audit` 的範圍限定在「過度設計」，正確性 bug、
安全漏洞、效能問題明確不在範圍內 — 那些要走一般的 review。

### 來源與授權

- 上游：https://github.com/DietrichGebert/ponytail
- 版本：v4.8.4（commit `16f2980`）
- 授權：MIT，全文見 `PONYTAIL-LICENSE`

### 與上游的差異

僅 vendor `skills/` 目錄，未引入上游的 `hooks/`（SessionStart / UserPromptSubmit
的 Node.js hooks）。因此**不會**每個 session 自動啟用，需手動 `/ponytail`。
對應地，`ponytail-help` 的 Configure / Update 段落與 `ponytail-gain` 的資料來源
連結已改寫成 vendored 版本的說明，其餘檔案與上游一致。

### 更新方式

```bash
git clone --depth 1 https://github.com/DietrichGebert/ponytail /tmp/ponytail
cp -r /tmp/ponytail/skills/. .claude/skills/
# 重新套用上述兩處差異，並更新本檔的 commit 記錄
```

## i-have-adhd

把輸出排版成 ADHD 讀者可以直接行動的形式。十條規則，重點是：

- **第一行就是可執行的動作**，不是鋪陳、不是計畫。指令、路徑、程式碼片段擺最前面。
- **多步驟一律編號**，一步一個有邊界的動作。
- **每回合重述進度**（「5 步中的第 3 步完成：schema 已更新。下一步：回填新欄位。」），
  因為上一則訊息的狀態記不住。
- **抑制岔題**，先把手上的做完，第二件事另外問。
- **具體時間估計**（「如果測試已經涵蓋大概 15 分鐘，沒有的話要一個下午」），不是「要花點時間」。
- **明確列出現在能動的東西**，不要把成果埋在總結裡。
- **錯誤用平舖直敘**，不要「糟糕」「似乎有點問題」，直接講原因和修法。
- **清單上限 5 項**，超過就切成「現在做／之後做」。
- **沒有開場白、沒有事後總結、沒有結尾客套**。

### 會自動破例的情況

要求「解釋一下」「帶我走過一遍」時會完整說明；有破壞性操作（`rm -rf`、force push、
schema migration、drop table）時會先確認；連續三回合都「還是壞的」時會停下來講出
可能錯誤的假設；請求真的有歧義時會問一個問題；以及規則本身會把答案砍掉時（例如
問「我有哪些選項」，選項就是答案）以任務為準。

### 只能手動啟用

skill 標了 `disable-model-invocation: true`，所以**只有**明確打 `/i-have-adhd`
才會啟用，不會被自動觸發。

### 來源與授權

- 上游：https://github.com/ayghri/i-have-adhd
- 版本：v0.1.0（commit `07684c4`）
- 授權：MIT，全文見 `I-HAVE-ADHD-LICENSE`

### 與上游的差異

`SKILL.md` 與上游逐字一致。未引入上游的 `hooks/`（SessionStart 的 always-on
旗標腳本），也未複製 `skills/i-have-adhd/agents/`（Gemini / OpenAI 平台專用設定，
Claude Code 用不到）。

### 更新方式

```bash
git clone --depth 1 https://github.com/ayghri/i-have-adhd /tmp/i-have-adhd
cp /tmp/i-have-adhd/skills/i-have-adhd/SKILL.md .claude/skills/i-have-adhd/
# 更新本檔的 commit 記錄
```

---

## 搭配使用

四個 skill 管的層面不同，可以同時生效：

| Skill | 管什麼 |
|-------|--------|
| `cost-aware-orchestration` | 這件事該由誰做 |
| `ponytail` | 寫出什麼程式碼 |
| `styleseed` | 介面長什麼樣 |
| `i-have-adhd` | 回應怎麼排版 |

在本專案的典型組合：

| 情境 | 建議 |
|------|------|
| 依 `docs/v2_rebuild_plan.md` 實作某個 Phase | 直接開工，`cost-aware-orchestration` 會自己判斷要不要派子代理 |
| 新增 service / API 端點 | `/ponytail` 再描述需求，避免長出不必要的抽象層 |
| 調 `app/web/` 的頁面或元件 | styleseed 自動生效，但以 `docs/design/stitch_design_pro_space_gray.md` 為準 |
| 送 PR 前自我檢查 | `/ponytail-review` |
| 覺得某個模組膨脹了 | `/ponytail-audit`，看排序最前面的幾項 |
| 想知道之前留了哪些捷徑 | `/ponytail-debt` |
| 要一步步跟著做的操作指引 | `/i-have-adhd` |

## 與 `CLAUDE.md` 的關係

專案根目錄的 `CLAUDE.md` 是一直生效的基準規範，skills 是可開關的疊加層。
兩處已知的張力：

- **`CLAUDE.md` 的「簡單優先」「不做規格外功能」與 ponytail 方向一致**，
  ponytail 開啟時較強勢（`full` 以上會主動質疑需求該不該存在）。v2 的實作要照
  `docs/v2_rebuild_plan.md` 的階段規格走，ponytail 質疑的是規格外的東西，
  不是規格本身。
- **`CLAUDE.md` 要求先講清楚假設與取捨，i-have-adhd 要求砍掉前言**。
  實際使用時若覺得該講的被砍掉了，明確說「解釋一下」即可觸發 i-have-adhd
  的破例條款。
