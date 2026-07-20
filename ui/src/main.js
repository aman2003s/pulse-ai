// Pulse overlay — thin client. Zero business logic: renders core events, forwards input.
const WS_URL = "ws://127.0.0.1:7550";

const pill = document.getElementById("pill");
const statusLine = document.getElementById("status-line");
const detailLine = document.getElementById("detail-line");
const input = document.getElementById("text-input");
const toast = document.getElementById("toast");
const settingsBtn = document.getElementById("settings-btn");
const settingsMenu = document.getElementById("settings-menu");
const modeSelect = document.getElementById("mode-select");
const wakeInput = document.getElementById("wakeword-input");
const a11yChk = document.getElementById("a11y-chk"); // "Superhero Mode": guided + narrate + typing echo, as one switch

const STATE_LABELS = {
  idle: "Say “Pulse” or type below",
  listening: "Listening…",
  thinking: "Thinking…",
  acting: "Working on it…",
  speaking: "Speaking…",
};

let ws = null;
let reconnectDelay = 1000;
let fadeTimer = null;

function setState(state) {
  pill.className = state;
  statusLine.textContent = STATE_LABELS[state] || state;
  clearTimeout(fadeTimer);
  pill.classList.remove("faded");
  if (state === "idle") {
    fadeTimer = setTimeout(() => pill.classList.add("faded"), 6000);
  }
}

function showToast(msg) {
  toast.textContent = msg;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 5000);
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(Object.assign({ v: 1 }, obj)));
  }
}

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    reconnectDelay = 1000;
    setState("idle");
    detailLine.textContent = "";
    send({ type: "list_devices" });
  };

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    switch (msg.type) {
      case "state":
        setState(msg.payload);
        break;
      case "transcript":
        detailLine.textContent = "“" + msg.payload + "”";
        break;
      case "action":
        detailLine.textContent = msg.tool + " " + JSON.stringify(msg.params || {});
        break;
      case "feedback":
        statusLine.textContent = msg.text;
        break;
      case "error":
        showToast(msg.message || "Something went wrong.");
        break;
      case "devices": {
        const sel = document.getElementById("mic-select");
        sel.innerHTML = '<option value="">Default</option>';
        (msg.inputs || []).forEach((d) => {
          const o = document.createElement("option");
          o.value = d.id;
          o.textContent = d.name;
          sel.appendChild(o);
        });
        break;
      }
      case "config":
        wakeInput.value = msg.wake_word || "pulse";
        modeSelect.value = msg.feedback_mode || "Standard";
        a11yChk.checked = msg.feedback_mode === "Guided" && !!msg.narrate && !!msg.typing_echo;
        break;
      case "training_progress": {
        const bar = document.getElementById("train-bar");
        if (msg.status === "started" || msg.status === "running") {
          bar.hidden = false;
          bar.textContent = msg.text;
        } else {
          // done or failed — show final message then fade out
          bar.textContent = msg.text;
          bar.dataset.status = msg.status;
          setTimeout(() => { bar.hidden = true; bar.dataset.status = ""; }, 6000);
        }
        break;
      }
    }
  };

  ws.onclose = () => {
    pill.className = "disconnected";
    statusLine.textContent = "Pulse core not running";
    detailLine.textContent = "Retrying…";
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 5000);
  };

  ws.onerror = () => ws.close();
}

// --- input handling ---
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && input.value.trim()) {
    send({ type: "text_command", text: input.value.trim() });
    input.value = "";
  } else if (e.key === "Escape") {
    send({ type: "cancel" });
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.activeElement !== input) {
    send({ type: "cancel" });
  }
});

document.getElementById("orb").addEventListener("click", () => {
  send({ type: "wake" });
});
function closeSettingsMenu() {
  settingsMenu.hidden = true;
  settingsBtn.setAttribute("aria-expanded", "false");
}
settingsBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  settingsMenu.hidden = !settingsMenu.hidden;
  settingsBtn.setAttribute("aria-expanded", String(!settingsMenu.hidden));
});
document.addEventListener("click", (e) => {
  if (!settingsMenu.hidden && !settingsMenu.contains(e.target) && e.target !== settingsBtn) {
    closeSettingsMenu();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsMenu.hidden) closeSettingsMenu();
});
modeSelect.addEventListener("change", () => {
  send({ type: "set_config", key: "feedback_mode", value: modeSelect.value });
});
document.getElementById("train-wake-btn").addEventListener("click", () => {
  send({ type: "train_wake_word", word: wakeInput.value.trim() });
});
a11yChk.addEventListener("change", (e) => {
  const on = e.target.checked;
  send({ type: "set_config", key: "accessibility_mode", value: on ? "on" : "off" });
  modeSelect.value = on ? "Guided" : "Standard";
});
document.getElementById("mic-select").addEventListener("change", (e) => {
  if (e.target.value !== "") send({ type: "set_config", key: "mic_device", value: e.target.value });
});

// --- position window bottom-center (Tauri only; no-op in a plain browser) ---
async function positionWindow() {
  try {
    const t = window.__TAURI__;
    if (!t || !t.window) return;
    const win = t.window.getCurrentWindow();
    const monitor = await t.window.currentMonitor();
    if (!monitor) return;
    const size = await win.outerSize();
    const x = Math.round(monitor.position.x + (monitor.size.width - size.width) / 2);
    const y = Math.round(monitor.position.y + monitor.size.height - size.height - 80);
    await win.setPosition(new t.window.PhysicalPosition(x, y));
  } catch (e) {
    console.warn("Window positioning skipped:", e);
  }
}

positionWindow();
connect();
