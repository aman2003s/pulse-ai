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
const STATE_CLASSES = ["idle", "listening", "thinking", "acting", "speaking", "disconnected"];

let ws = null;
let reconnectDelay = 500;   // start fast — backend usually ready in 3-8s
let reconnectAttempts = 0;  // track how many times we've tried
const FAST_RETRY_LIMIT = 20; // try every 500ms for up to 10s before slowing down
let fadeTimer = null;
let trainingDoneTimer = null;

function setState(state) {
  // Only swap the state class — a plain `className =` overwrite would also wipe
  // the "training" glow class applied independently during wake-word training.
  pill.classList.remove(...STATE_CLASSES);
  void pill.offsetWidth; // force reflow so #orb and #pill animation clocks reset in lockstep
  pill.classList.add(state);
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
    reconnectDelay = 500;    // reset for next time
    reconnectAttempts = 0;  // reset attempt counter
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
        // Sole cue: glowing border on the existing pill + the text printed into
        // detail-line (an element that's always there anyway) — no separate element,
        // so no extra height is ever reserved for something that isn't visible.
        clearTimeout(trainingDoneTimer);
        if (msg.status === "started" || msg.status === "running") {
          pill.classList.add("training");
          pill.classList.remove("training-done", "training-failed");
          detailLine.textContent = msg.text;
        } else {
          // done or failed — solid color hold, then fade back to normal
          pill.classList.remove("training");
          pill.classList.add(msg.status === "failed" ? "training-failed" : "training-done");
          detailLine.textContent = msg.text;
          trainingDoneTimer = setTimeout(() => {
            pill.classList.remove("training-done", "training-failed");
          }, 6000);
        }
        break;
      }
    }
  };

  ws.onclose = () => {
    pill.className = "disconnected";
    if (reconnectAttempts < FAST_RETRY_LIMIT) {
      // Still in startup window — show a gentle "starting" message, not an error
      statusLine.textContent = "Starting up…";
      detailLine.textContent = `Connecting to Pulse core…`;
      reconnectDelay = 500;
    } else {
      statusLine.textContent = "Pulse core not running";
      detailLine.textContent = "Start run.bat to launch Pulse";
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    }
    reconnectAttempts++;
    setTimeout(connect, reconnectDelay);
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
document.getElementById("close-btn").addEventListener("click", () => {
  // Closes just this overlay window — Pulse core (and wake-word listening) keeps
  // running in the background; run.bat brings the window back without restarting it.
  const t = window.__TAURI__;
  if (t && t.window) t.window.getCurrentWindow().close();
});
function closeSettingsMenu() {
  if (settingsMenu.hidden) return;
  // Hide BEFORE shrinking — the window staying briefly oversized is invisible
  // (transparent), but showing the popup before the window has grown to fit it
  // is what caused the visible flicker (clipped for a frame, then snaps).
  settingsMenu.hidden = true;
  settingsBtn.setAttribute("aria-expanded", "false");
  syncWindowHeight(false);
}
settingsBtn.addEventListener("click", async (e) => {
  e.stopPropagation();
  if (settingsMenu.hidden) {
    await syncWindowHeight(true);  // grow to fit FIRST, then reveal — no clipped frame
    settingsMenu.hidden = false;
    settingsBtn.setAttribute("aria-expanded", "true");
  } else {
    closeSettingsMenu();
  }
});
document.addEventListener("click", (e) => {
  if (!settingsMenu.hidden && !settingsMenu.contains(e.target) && e.target !== settingsBtn) {
    closeSettingsMenu();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsMenu.hidden) closeSettingsMenu();
});
const expandBtn = document.getElementById("expand-btn");
const controlsPanel = document.getElementById("controls-panel");

expandBtn.addEventListener("click", async () => {
  const isCollapsed = controlsPanel.classList.contains("collapsed");
  if (isCollapsed) {
    // Expanding: Resize window first to allocate height, then reveal panel — zero flicker
    await syncWindowHeight(settingsMenu.hidden ? false : true, false);
    controlsPanel.classList.remove("collapsed");
    expandBtn.classList.remove("collapsed");
    expandBtn.setAttribute("aria-expanded", "true");
    await syncWindowHeight();
  } else {
    // Collapsing: Animate panel close first, then sync window height smoothly
    controlsPanel.classList.add("collapsed");
    expandBtn.classList.add("collapsed");
    expandBtn.setAttribute("aria-expanded", "false");
    setTimeout(() => {
      syncWindowHeight();
    }, 260);
  }
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

// Measure the settings popup's natural height ONCE, synchronously, before first paint
let settingsMenuHeight = 0;
(function measureSettingsMenu() {
  settingsMenu.hidden = false;
  settingsMenuHeight = settingsMenu.getBoundingClientRect().height;
  settingsMenu.hidden = true;
})();

// --- size the OS window to exactly the rendered content, then bottom-center it ---
async function syncWindowHeight(menuOpen, forceCollapsed) {
  try {
    const t = window.__TAURI__;
    if (!t || !t.window) return;
    const win = t.window.getCurrentWindow();

    const isCollapsed = forceCollapsed !== undefined ? forceCollapsed : controlsPanel.classList.contains("collapsed");
    const visibleRows = isCollapsed ? [pill] : [pill, controlsPanel];
    const rects = visibleRows.map((el) => el.getBoundingClientRect());
    let top = Math.min(...rects.map((r) => r.top));
    const bottom = Math.max(...rects.map((r) => r.bottom));
    if (menuOpen) top -= settingsMenuHeight + 8; // popup height + its own gap above the pill
    const pad = parseFloat(getComputedStyle(document.body).paddingTop) || 0;
    const SAFETY = 16;
    const neededHeight = Math.ceil(bottom - top + pad * 2) + SAFETY;

    await win.setSize(new t.window.LogicalSize(480, neededHeight));

    const monitor = await t.window.currentMonitor();
    if (!monitor) return;
    const size = await win.outerSize();
    const x = Math.round(monitor.position.x + (monitor.size.width - size.width) / 2);
    const y = Math.round(monitor.position.y + monitor.size.height - size.height - 80);
    await win.setPosition(new t.window.PhysicalPosition(x, y));
  } catch (e) {
    console.warn("Window height sync skipped:", e);
  }
}

syncWindowHeight(false);
connect();
