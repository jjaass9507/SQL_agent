// lib/sse.js — SSE 客戶端：GET 用原生 EventSource、POST 用 fetch + ReadableStream 解析
//
// 斷線後自動重試最多 3 次（NFR-02），重試失敗後呼叫 onGiveUp，
// 由呼叫端顯示「請重新整理」提示。

const MAX_RETRIES = 3;
const KNOWN_EVENTS = ["delta", "turn_done", "tool_call", "tool_result", "generation_status"];

/**
 * @param {string} url SSE 端點（GET）
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

    for (const name of KNOWN_EVENTS) {
      source.addEventListener(name, (event) => dispatch(name, event));
    }

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

/**
 * 解析緩衝區內已完整收到的事件（以空白行 "\n\n" 分隔的 text/event-stream 區塊），
 * 回傳 [已解析事件陣列, 剩餘未完成的緩衝區]。
 * @param {string} buffer
 * @returns {[{event: string, data: string}[], string]}
 */
function parseEvents(buffer) {
  const events = [];
  const parts = buffer.split("\n\n");
  const remainder = parts.pop() ?? "";

  for (const part of parts) {
    if (!part.trim()) continue;
    let eventName = "message";
    const dataLines = [];
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }
    events.push({ event: eventName, data: dataLines.join("\n") });
  }
  return [events, remainder];
}

/**
 * POST + `Accept: text/event-stream`，以 fetch + ReadableStream 解析 SSE 回應。
 * 原生 EventSource 只支援 GET，本函式提供等價的 onEvent/onGiveUp 介面與
 * 3 次重試 + 指數退避語意。已收到 turn_done（回合已正常結束）後若串流才中斷，
 * 視為正常結束、不重試（避免重送整個請求造成訊息重複送出）。
 *
 * @param {string} url SSE 端點（POST）
 * @param {Object} options
 * @param {object} options.body 送出的 JSON body
 * @param {(eventName: string, data: any) => void} options.onEvent 收到具名 event 時呼叫
 * @param {() => void} [options.onGiveUp] 重試 3 次仍失敗時呼叫
 * @returns {{ close: () => void }}
 */
export function postSSE(url, { body, onEvent, onGiveUp } = {}) {
  let retries = 0;
  let closedByCaller = false;
  let controller = null;
  let sawTurnDone = false;

  async function run() {
    controller = new AbortController();
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`API ${response.status}`);
      }

      retries = 0;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const [events, remainder] = parseEvents(buffer);
        buffer = remainder;
        for (const { event, data } of events) {
          if (event === "turn_done") sawTurnDone = true;
          dispatchEvent(event, data);
        }
      }
      // 串流正常結束
    } catch (err) {
      if (closedByCaller || err.name === "AbortError" || sawTurnDone) return;
      retries += 1;
      if (retries > MAX_RETRIES) {
        if (onGiveUp) onGiveUp();
        return;
      }
      setTimeout(run, 500 * 2 ** (retries - 1));
    }
  }

  function dispatchEvent(eventName, rawData) {
    if (!onEvent) return;
    let data = rawData;
    try {
      data = JSON.parse(rawData);
    } catch {
      // 非 JSON payload，原樣傳遞
    }
    onEvent(eventName, data);
  }

  run();

  return {
    close() {
      closedByCaller = true;
      if (controller) controller.abort();
    },
  };
}
