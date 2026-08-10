// pages/index.js — 首頁：session 列表（狀態篩選）、建立設計/審查 session、DDL 匯入
import { ENDPOINTS, api } from "../lib/api.js";
import { confirmDialog } from "../lib/confirm-dialog.js";
import { showToast } from "../lib/toast.js";

// phase → 篩選群組（篩選按鈕的 data-status）
const PHASE_GROUP = {
  collecting: "in_progress",
  generating: "in_progress",
  reviewing: "in_progress",
  confirming: "confirming",
  done: "done",
  review_done: "done",
};

// phase → 狀態 pill 樣式後綴與顯示文字
const PHASE_PILL = {
  collecting: ["in_progress", "需求收集中"],
  confirming: ["confirming", "待確認"],
  generating: ["in_progress", "產出中"],
  done: ["done", "已完成"],
  reviewing: ["in_progress", "審查中"],
  review_done: ["done", "審查完成"],
};

// phase → 一句白話說明「現在卡在誰身上」。狀態 pill 講的是系統狀態
// （「待確認」），這裡講的是使用者要做什麼——提需求的人看 pill 看不出下一步。
const PHASE_HINT = {
  collecting: "AI 正在跟你確認需求細節",
  confirming: "等你確認欄位，確認後才會產出文件",
  generating: "AI 正在產出文件，稍等一下",
  done: "文件已經做好了，可以看了",
  reviewing: "AI 正在審查這個資料庫",
  review_done: "審查報告已經好了",
};

// phase → 開啟 session 時導向的頁面
function sessionUrl(session) {
  const routes = {
    collecting: `/chat/${session.id}`,
    confirming: `/confirm/${session.id}`,
    generating: `/docs/${session.id}`,
    done: `/docs/${session.id}`,
    reviewing: `/review/${session.id}`,
    review_done: `/review/${session.id}`,
  };
  return routes[session.phase] || `/chat/${session.id}`;
}

let allSessions = [];
let currentFilter = "all";
let searchTerm = "";
let tagFilter = "";  // 選中的標籤；空字串代表不依標籤篩選

function renderSessions() {
  const list = document.querySelector('[data-target="session-list"]');
  if (!list) return;

  const needle = searchTerm.trim().toLowerCase();
  const visible = allSessions.filter(
    (s) =>
      (currentFilter === "all" || PHASE_GROUP[s.phase] === currentFilter) &&
      (!tagFilter || (s.tags || []).includes(tagFilter)) &&
      // 搜尋也比對標籤：打「PM-陳」就能找到那個 PM 的所有案子
      (!needle ||
        (s.title || "").toLowerCase().includes(needle) ||
        (s.tags || []).some((t) => t.toLowerCase().includes(needle)))
  );

  list.textContent = "";
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "form-hint";
    empty.dataset.target = "session-list-empty";
    empty.textContent = allSessions.length
      ? "沒有符合條件的紀錄。"
      : "還沒有任何紀錄。點上方「開始新的資料表設計」，用聊天描述你要的資料就可以開始。";
    list.appendChild(empty);
    return;
  }

  for (const session of visible) {
    const card = document.createElement("div");
    card.className = "card card-clickable page-index-session-row";
    card.dataset.action = "open-session";
    card.dataset.target = session.id;

    const meta = document.createElement("div");
    meta.className = "page-index-session-meta";
    const title = document.createElement("strong");
    title.textContent = session.title;
    const hint = document.createElement("span");
    hint.className = "form-hint";
    const modeLabel = session.mode === "review" ? "審查模式" : "設計模式";
    hint.textContent = `${modeLabel}・${new Date(session.created_at).toLocaleString()}`;
    meta.appendChild(title);
    meta.appendChild(hint);

    const phaseHint = PHASE_HINT[session.phase];
    if (phaseHint) {
      const next = document.createElement("span");
      next.className = "page-index-session-next";
      next.textContent = phaseHint;
      meta.appendChild(next);
    }

    const [pillClass, pillText] = PHASE_PILL[session.phase] || ["in_progress", session.phase];
    const pill = document.createElement("span");
    pill.className = `status-pill status-pill-${pillClass}`;
    pill.textContent = pillText;

    // 標籤直接顯示在卡片上——不然還是得一個個點開才知道是哪個案子的
    if ((session.tags || []).length) {
      const tags = document.createElement("span");
      tags.className = "session-tags";
      for (const tag of session.tags) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "session-tag";
        chip.textContent = tag;
        chip.dataset.action = "filter-by-tag";
        chip.dataset.tag = tag;
        tags.appendChild(chip);
      }
      meta.appendChild(tags);
    }

    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = session.pinned ? "btn btn-ghost btn-sm is-pinned" : "btn btn-ghost btn-sm";
    pin.textContent = session.pinned ? "📌" : "📍";
    pin.title = session.pinned ? "取消釘選" : "釘選到最上面";
    pin.dataset.action = "toggle-pin";
    pin.dataset.target = session.id;
    pin.dataset.pinned = String(Boolean(session.pinned));

    const label = document.createElement("button");
    label.type = "button";
    label.className = "btn btn-ghost btn-sm";
    label.textContent = "標籤";
    label.dataset.action = "edit-tags";
    label.dataset.target = session.id;
    label.dataset.tags = (session.tags || []).join(", ");

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-ghost btn-sm";
    remove.textContent = "刪除";
    remove.dataset.action = "delete-session";
    remove.dataset.target = session.id;
    remove.dataset.title = session.title;

    card.appendChild(meta);
    card.appendChild(pill);
    card.appendChild(pin);
    card.appendChild(label);
    card.appendChild(remove);
    list.appendChild(card);
  }
}

async function loadSessions() {
  try {
    allSessions = await api.get(ENDPOINTS.sessions());
    renderSessions();
  } catch {
    // apiFetch 已 toast，列表維持空狀態
  }
}

// 顯示指定的建立表單卡片（review / ddl-import），其餘隱藏
function toggleForm(name) {
  for (const form of document.querySelectorAll("[data-target$='-form-card']")) {
    form.hidden = form.dataset.target !== `${name}-form-card`;
  }
}

async function saveLabels(sessionId, payload) {
  try {
    await api.put(ENDPOINTS.sessionLabels(sessionId), payload);
    await loadSessions();
  } catch {
    // apiFetch 已 toast
  }
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;

  if (target.dataset.action === "toggle-pin") {
    event.stopPropagation();
    await saveLabels(target.dataset.target, { pinned: target.dataset.pinned !== "true" });
    return;
  }

  if (target.dataset.action === "edit-tags") {
    event.stopPropagation();
    const current = target.dataset.tags || "";
    const next = window.prompt("標籤（用逗號分隔，例如：PM-陳, 急件）", current);
    if (next === null) return;
    await saveLabels(target.dataset.target, {
      tags: next.split(",").map((t) => t.trim()).filter(Boolean),
    });
    return;
  }

  if (target.dataset.action === "filter-by-tag") {
    event.stopPropagation();
    // 再點一次同一個標籤就取消篩選
    tagFilter = tagFilter === target.dataset.tag ? "" : target.dataset.tag;
    renderSessions();
    const banner = document.querySelector('[data-target="tag-filter-banner"]');
    if (banner) {
      banner.hidden = !tagFilter;
      banner.textContent = tagFilter ? `只顯示標籤「${tagFilter}」的紀錄（再點一次取消）` : "";
    }
    return;
  }
  const action = target.dataset.action;

  if (action === "create-session") {
    const mode = target.dataset.mode;
    if (mode === "design") {
      toggleForm("design");
    } else if (mode === "review") {
      toggleForm("review");
    } else if (mode === "ddl-import") {
      toggleForm("ddl-import");
    }
  }

  if (action === "filter-sessions") {
    currentFilter = target.dataset.status;
    document.querySelectorAll('[data-action="filter-sessions"]').forEach((btn) => {
      btn.classList.toggle("is-active", btn === target);
    });
    renderSessions();
  }

  if (action === "delete-session") {
    event.stopPropagation();  // 卡片本身是 open-session，不要順便打開
    const ok = await confirmDialog({
      title: "要刪除這筆紀錄嗎？",
      lead: `「${target.dataset.title}」以及它的對話、版本紀錄與已產出的文件都會一起刪除。`,
      facts: [
        { label: "可以復原嗎", value: "不行，刪掉就沒了。需要保留的文件請先下載。", tone: "warn" },
        { label: "會影響資料庫嗎", value: "不會。這裡刪的只是平台上的設計紀錄。" },
      ],
      confirmText: "刪除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(ENDPOINTS.session(target.dataset.target));
      allSessions = allSessions.filter((s) => s.id !== target.dataset.target);
      renderSessions();
      showToast("已刪除", "success");
    } catch {
      // apiFetch 已 toast
    }
    return;
  }

  if (action === "open-session") {
    const session = allSessions.find((s) => s.id === target.dataset.target);
    if (session) window.location.href = sessionUrl(session);
  }

  if (action === "cancel-form") {
    toggleForm("");
  }
});

document.addEventListener("input", (event) => {
  if (!event.target.matches('[data-target="session-search"]')) return;
  searchTerm = event.target.value;
  renderSessions();
});

document.addEventListener("submit", async (event) => {
  const form = event.target;

  if (form.matches('[data-action="submit-design"]')) {
    event.preventDefault();
    const title = form.querySelector('[data-target="design-title"]').value.trim();
    const dbUrl = form.querySelector('[data-target="design-db-url"]').value.trim();
    try {
      const session = await api.post(ENDPOINTS.sessions(), {
        mode: "design",
        ...(title ? { title } : {}),
      });
      // 匯入現有結構是加分項，失敗不該讓使用者連對話都進不去
      if (dbUrl) {
        try {
          const imported = await api.post(ENDPOINTS.sessionImportDb(session.id), {
            db_url: dbUrl,
          });
          showToast(`已讀取現有資料庫的 ${imported.table_count} 張資料表`, "success");
        } catch {
          showToast("讀取現有資料庫失敗，將以全新設計繼續（可稍後再匯入）", "warning");
        }
      }
      window.location.href = `/chat/${session.id}`;
    } catch {
      // apiFetch 已 toast
    }
    return;
  }

  if (form.matches('[data-action="submit-review-import"]')) {
    event.preventDefault();
    const dbUrl = form.querySelector('[data-target="review-db-url"]').value.trim();
    const title = form.querySelector('[data-target="review-title"]').value.trim();
    if (!dbUrl) {
      showToast("請填入 PostgreSQL 連線字串", "warning");
      return;
    }
    try {
      const session = await api.post(ENDPOINTS.sessions(), {
        mode: "review",
        db_url: dbUrl,
        title: title || "DB 審查",
      });
      window.location.href = `/review/${session.id}`;
    } catch {
      // apiFetch 已 toast
    }
  }

  if (form.matches('[data-action="submit-ddl-import"]')) {
    event.preventDefault();
    const ddl = form.querySelector('[data-target="ddl-import-text"]').value.trim();
    const title = form.querySelector('[data-target="ddl-import-title"]').value.trim();
    if (!ddl) {
      showToast("請貼上 CREATE TABLE 語句", "warning");
      return;
    }
    try {
      const result = await api.post(ENDPOINTS.ddlImport(), { ddl, title: title || null });
      showToast(`已匯入 ${result.table_count} 張資料表`, "success");
      window.location.href = `/confirm/${result.id}`;
    } catch {
      // apiFetch 已 toast
    }
  }
});

loadSessions();
