# Claude Code Skills

專案層級的 Claude Code skills。放在 `.claude/skills/<name>/SKILL.md`，開啟本 repo 的
Claude Code session（含 web session）會自動載入，用 `/<skill-name>` 呼叫。

## ponytail

「懶惰資深工程師」模式：強制最短、最簡單、可動的解法（YAGNI → stdlib → 平台原生
→ 一行 → 最小實作）。與本專案 `CLAUDE.md` 的 §2 Simplicity First / §3 Surgical
Changes 方向一致，ponytail 提供的是可隨時開關的強化版與幾個一次性報告指令。

| Skill | 用途 |
|-------|------|
| `/ponytail [lite\|full\|ultra]` | 開啟懶惰模式（預設 `full`），持續到 session 結束或說「stop ponytail」 |
| `/ponytail-review` | 針對 diff 找過度設計，逐行列出可刪除的部分 |
| `/ponytail-audit` | 全 repo 過度設計盤點，依可刪除量排序 |
| `/ponytail-debt` | 收集程式碼中的 `ponytail:` 註記，彙整成技術債帳本 |
| `/ponytail-gain` | 顯示 ponytail 的 benchmark 成效卡（上游數據，非本 repo 實測） |
| `/ponytail-help` | 指令速查卡 |

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
