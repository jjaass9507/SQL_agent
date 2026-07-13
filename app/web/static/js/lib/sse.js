// lib/sse.js — EventSource 封裝
//
// 斷線後自動重試最多 3 次（NFR-02），重試失敗後呼叫 onGiveUp，
// 由呼叫端顯示「請重新整理」提示。
// TODO(API): 後端 SSE 端點（/api/v1/sessions/{id}/messages、
// /api/v1/sessions/{id}/events、/api/v1/agent/chat）就緒後即可直接使用。

const MAX_RETRIES = 3;

/**
 * @param {string} url SSE 端點
 * @param {Object} handlers
 * @param {(eventName: string, data: any) => void} handlers.onEvent 收到具名 event 時呼叫
 * @param {() => void} [handlers.onGiveUp] 重試 3 次仍失敗時呼叫
 * @returns {{ close: () => void }}
 */
export function connectSSE(url, { onEvent, onGiveUp } = {}) {
  let source = null;
  let retries = 0;
  let closedByCaller = false;

  function open() {
    source = new EventSource(url);

    source.onopen = () => {
      retries = 0;
    };

    source.onmessage = (event) => {
      dispatch("message", event);
    };

    source.addEventListener("delta", (event) => dispatch("delta", event));
    source.addEventListener("turn_done", (event) => dispatch("turn_done", event));
    source.addEventListener("tool_call", (event) => dispatch("tool_call", event));
    source.addEventListener("tool_result", (event) => dispatch("tool_result", event));
    source.addEventListener("generation_status", (event) => dispatch("generation_status", event));

    source.onerror = () => {
      source.close();
      if (closedByCaller) return;

      retries += 1;
      if (retries > MAX_RETRIES) {
        if (onGiveUp) onGiveUp();
        return;
      }
      // 指數退避重試
      setTimeout(open, 500 * 2 ** (retries - 1));
    };
  }

  function dispatch(eventName, event) {
    if (!onEvent) return;
    let data = event.data;
    try {
      data = JSON.parse(event.data);
    } catch {
      // 非 JSON payload，原樣傳遞
    }
    onEvent(eventName, data);
  }

  open();

  return {
    close() {
      closedByCaller = true;
      if (source) source.close();
    },
  };
}
