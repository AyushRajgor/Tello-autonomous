let ws;
let frameStreamInterval = null;
let videoStatusPoll = null;
const chatMessages = document.getElementById('chat-messages');
const chatInput = document.getElementById('chat-input');

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    const pill = document.getElementById('status-pill');
    pill.className = 'status-pill connected';
    document.getElementById('connection-status').textContent = 'Connected';
    checkVideoStatus();
    // Keep polling so stream auto-starts once drone/webcam comes online.
    if (!videoStatusPoll) videoStatusPoll = setInterval(checkVideoStatus, 2000);
  };
  ws.onclose = () => {
    const pill = document.getElementById('status-pill');
    pill.className = 'status-pill disconnected';
    document.getElementById('connection-status').textContent = 'Disconnected';
    stopFrameStream();
    if (videoStatusPoll) { clearInterval(videoStatusPoll); videoStatusPoll = null; }
    setTimeout(connect, 2000);
  };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'chat') {
      addMessage(msg.role, msg.text);
      // Show thinking indicator right after user message echo from server
      if (msg.role === 'user' && waitingForResponse) {
        showThinkingIndicator();
      }
    }
    else if (msg.type === 'state') updateTelemetry(msg.data);
    else if (msg.type === 'frame') updateFrame(msg.image_base64);
    else if (msg.type === 'trace') handleTrace(msg.data);
    else if (msg.type === 'tool') handleToolCall(msg);
    else if (msg.type === 'mode') handleModeBadge(msg.mode);
  };
}

async function checkVideoStatus() {
  try {
    const resp = await fetch('/video-status');
    const data = await resp.json();
    const badge = document.getElementById('webcam-badge');
    const label = document.getElementById('video-source-label');
    if (data.active) {
      badge.style.display = 'flex';
      if (label) label.textContent = data.source === 'drone' ? 'Drone Feed' : 'Webcam';
      startFrameStream();
    } else {
      badge.style.display = 'none';
      stopFrameStream();
    }
  } catch (e) {}
}

function startFrameStream() {
  if (frameStreamInterval) return;
  frameStreamInterval = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'get_frame' }));
    }
  }, 100);
}

function stopFrameStream() {
  if (frameStreamInterval) {
    clearInterval(frameStreamInterval);
    frameStreamInterval = null;
  }
}

function addMessage(role, text) {
  // Remove thinking indicator when assistant responds
  if (role === 'assistant') {
    waitingForResponse = false;
    const thinking = document.getElementById('thinking-indicator');
    if (thinking) thinking.remove();
  }
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.textContent = text;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function handleToolCall(msg) {
  // Inline compact indicator of tool calls arriving via Live API
  const div = document.createElement('div');
  div.className = `msg tool ${msg.status === 'ok' ? 'ok' : 'err'}`;
  const mark = msg.status === 'ok' ? '✓' : '✗';
  const argStr = Object.keys(msg.args || {}).length
    ? '(' + Object.entries(msg.args).map(([k, v]) => `${k}=${v}`).join(', ') + ')'
    : '()';
  div.textContent = `${mark} ${msg.name}${argStr}`;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function handleModeBadge(mode) {
  const badges = document.querySelector('.status-badges');
  if (!badges) return;
  let el = document.getElementById('mode-badge');
  if (!el) {
    el = document.createElement('div');
    el.id = 'mode-badge';
    el.className = 'badge mode-badge';
    badges.appendChild(el);
  }
  if (mode === 'live') {
    el.classList.add('live');
    el.classList.remove('legacy');
    el.innerHTML = '<div class="dot"></div><span>LIVE · gemini-3.1</span>';
  } else {
    el.classList.add('legacy');
    el.classList.remove('live');
    el.innerHTML = '<div class="dot"></div><span>LEGACY · gemini-3</span>';
  }
}

function updateTelemetry(state) {
  const p = state.position || {};
  const alt = p.z || 0;
  const heading = state.heading_deg || 0;
  const batt = state.battery_pct || 0;
  const dist = state.distance_from_home_cm || 0;
  const flying = state.is_flying;
  const ft = state.flight_duration_s || 0;

  // Telemetry strip
  document.getElementById('tel-pos').textContent = `(${p.x||0}, ${p.y||0}, ${alt})`;
  document.getElementById('tel-heading').textContent = `${heading}°`;
  document.getElementById('tel-dist').textContent = `${dist} cm`;
  document.getElementById('tel-alt').textContent = `${alt} cm`;
  document.getElementById('tel-time').textContent = `${ft}s`;

  const flyEl = document.getElementById('tel-flying');
  flyEl.textContent = flying ? 'Airborne' : 'Grounded';
  flyEl.className = `tel-val ${flying ? 'active' : 'inactive'}`;

  // Battery
  const battColor = batt <= 20 ? 'var(--red)' : batt <= 50 ? 'var(--yellow)' : 'var(--green)';
  document.getElementById('battery').textContent = `${batt}%`;
  document.getElementById('battery-fill').style.width = `${batt}%`;
  document.getElementById('battery-fill').style.background = battColor;
  document.getElementById('inst-battery').textContent = `${batt}%`;
  document.getElementById('inst-battery-bar').style.width = `${batt}%`;
  document.getElementById('inst-battery-bar').style.background = battColor;

  // Altitude
  const altPct = Math.min(alt / 200 * 100, 100);
  document.getElementById('inst-alt').textContent = `${alt} cm`;
  document.getElementById('inst-alt-bar').style.width = `${altPct}%`;

  // Heading
  document.getElementById('inst-heading').textContent = `${heading}°`;
}

function updateFrame(b64) {
  const img = document.getElementById('video-frame');
  img.src = 'data:image/jpeg;base64,' + b64;
  img.style.display = 'block';
  document.getElementById('video-placeholder').style.display = 'none';
}

let waitingForResponse = false;

function showThinkingIndicator() {
  // Remove any existing indicator first
  const existing = document.getElementById('thinking-indicator');
  if (existing) existing.remove();
  
  const div = document.createElement('div');
  div.className = 'thinking-indicator';
  div.id = 'thinking-indicator';
  div.innerHTML = 'Thinking<span class="thinking-dots"><span></span><span></span><span></span></span>';
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || !ws) return;
  waitingForResponse = true;
  ws.send(JSON.stringify({ type: 'command', text }));
  chatInput.value = '';
}

function quickCmd(text) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  waitingForResponse = true;
  ws.send(JSON.stringify({ type: 'command', text }));
}

chatInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMessage(); });

/* ── Microphone (Push-to-Talk) ── */
const TARGET_SAMPLE_RATE = 16000;
let micStream = null;
let micContext = null;
let micSource = null;
let micProcessor = null;
let micActive = false;

async function startMic(e) {
  if (e) e.preventDefault();
  if (micActive) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    addMessage('system', 'Microphone not available in this browser.');
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addMessage('system', 'Not connected. Cannot start mic.');
    return;
  }

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        noiseSuppression: true,
        echoCancellation: true,
        autoGainControl: true,
      },
    });
  } catch (err) {
    addMessage('system', `Mic permission denied: ${err.message}`);
    return;
  }

  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  micContext = new AudioCtx();
  micSource = micContext.createMediaStreamSource(micStream);

  // ScriptProcessor is deprecated but universally supported and simplest for raw PCM.
  // Buffer size 4096 at source rate -> ~85ms chunks at 48kHz, fine for low-latency streaming.
  const bufferSize = 4096;
  micProcessor = micContext.createScriptProcessor(bufferSize, 1, 1);
  micSource.connect(micProcessor);
  micProcessor.connect(micContext.destination);

  const sourceRate = micContext.sampleRate;
  micActive = true;
  updateMicButton(true);

  // Tell server to start a new user turn
  ws.send(JSON.stringify({ type: 'audio_start' }));

  micProcessor.onaudioprocess = (ev) => {
    if (!micActive || !ws || ws.readyState !== WebSocket.OPEN) return;
    const input = ev.inputBuffer.getChannelData(0);
    const pcm16 = floatTo16BitPCM(downsample(input, sourceRate, TARGET_SAMPLE_RATE));
    if (pcm16.byteLength > 0) {
      ws.send(pcm16);
    }
  };
}

function stopMic(e) {
  if (e) e.preventDefault();
  if (!micActive) return;
  micActive = false;
  updateMicButton(false);

  try { micProcessor && micProcessor.disconnect(); } catch (_) {}
  try { micSource && micSource.disconnect(); } catch (_) {}
  try { micContext && micContext.close(); } catch (_) {}
  if (micStream) {
    micStream.getTracks().forEach(t => t.stop());
  }
  micStream = null; micContext = null; micSource = null; micProcessor = null;

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'audio_end' }));
  }
}

function updateMicButton(recording) {
  const btn = document.getElementById('mic-btn');
  if (!btn) return;
  btn.classList.toggle('recording', !!recording);
  btn.title = recording ? 'Release to send' : 'Hold to talk';
}

function downsample(buffer, inRate, outRate) {
  if (outRate === inRate) return buffer;
  if (outRate > inRate) throw new Error('Upsampling not supported');
  const ratio = inRate / outRate;
  const outLen = Math.floor(buffer.length / ratio);
  const result = new Float32Array(outLen);
  let oi = 0, ii = 0;
  while (oi < outLen) {
    const next = Math.floor((oi + 1) * ratio);
    // simple box average over the window -> cheap low-pass
    let sum = 0, count = 0;
    for (let i = ii; i < next && i < buffer.length; i++) {
      sum += buffer[i];
      count++;
    }
    result[oi] = count > 0 ? sum / count : 0;
    oi++;
    ii = next;
  }
  return result;
}

function floatTo16BitPCM(float32) {
  const out = new ArrayBuffer(float32.length * 2);
  const view = new DataView(out);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return out;
}

// Prevent spacebar from triggering click on the mic button after pointer release
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && document.activeElement && document.activeElement.id === 'mic-btn') {
    e.preventDefault();
  }
});

/* ── Tabs & Traces Logic ── */
function switchTab(tabId) {
  document.getElementById('tab-btn-controls').classList.remove('active');
  document.getElementById('tab-btn-traces').classList.remove('active');
  document.getElementById(`tab-btn-${tabId}`).classList.add('active');
  
  document.getElementById('tab-controls').style.display = tabId === 'controls' ? 'flex' : 'none';
  document.getElementById('tab-traces').style.display = tabId === 'traces' ? 'flex' : 'none';
  
  if (tabId === 'traces') {
    loadTraces();
  }
}

let allTraces = [];
let sessionStart = Date.now();
let totalTraces = 0;
let totalCost = 0;
let llmCalls = 0;
let toolCalls = 0;

function handleTrace(trace) {
  // Check if we already have it
  const idx = allTraces.findIndex(t => t.trace_id === trace.trace_id);
  if (idx >= 0) {
    allTraces[idx] = trace;
  } else {
    allTraces.unshift(trace);
    totalTraces++;
  }
  updateSessionStats();
  renderTracesList();
}

async function loadTraces() {
  try {
    const res = await fetch('/api/traces');
    const data = await res.json();
    allTraces = data.traces || [];
    totalTraces = allTraces.length;
    updateSessionStats();
    renderTracesList();
  } catch (e) {
    console.error("Failed to load traces", e);
  }
}

function updateSessionStats() {
  let cost = 0;
  let latSum = 0;
  llmCalls = 0;
  toolCalls = 0;
  
  allTraces.forEach(t => {
    cost += t.total_cost_usd || 0;
    latSum += t.total_duration_ms || 0;
    (t.spans || []).forEach(s => {
      if (s.span_type === 'llm_call' || s.span_type === 'vision_call') llmCalls++;
      if (s.span_type === 'tool_call') toolCalls++;
    });
  });
  
  const avgLat = totalTraces > 0 ? (latSum / totalTraces / 1000).toFixed(1) : 0;
  
  const minutes = Math.floor((Date.now() - sessionStart) / 60000);
  const seconds = Math.floor(((Date.now() - sessionStart) % 60000) / 1000);
  
  document.getElementById('session-stats').innerHTML = 
    `Session: ${minutes}m ${seconds}s | Traces: ${totalTraces} | Total cost: $${cost.toFixed(4)}<br>` +
    `Avg latency: ${avgLat}s | LLM calls: ${llmCalls} | Tool calls: ${toolCalls}`;
}

// Update runtime every second
setInterval(() => {
  if (document.getElementById('tab-traces').style.display === 'flex') {
    updateSessionStats();
  }
}, 1000);

function renderTracesList() {
  const container = document.getElementById('traces-list-view');
  container.innerHTML = '';
  
  allTraces.forEach(t => {
    const dur = (t.total_duration_ms || 0) / 1000;
    const durClass = dur < 1 ? 'fast' : dur < 3 ? 'med' : 'slow';
    
    const row = document.createElement('div');
    row.className = 'trace-row';
    row.onclick = () => showTraceDetail(t.trace_id);
    
    row.innerHTML = `
      <div class="trace-row-header">
        <span class="trace-id">${t.trace_id}</span>
        <span class="trace-dur ${durClass}">${dur.toFixed(1)}s</span>
      </div>
      <div class="trace-msg">"${t.user_message}"</div>
      <div class="trace-meta">
        <span>${(t.spans || []).length} spans</span>
        <span>$${(t.total_cost_usd || 0).toFixed(4)}</span>
      </div>
    `;
    container.appendChild(row);
  });
}

function syntaxHighlight(json) {
  if (typeof json != 'string') {
       json = JSON.stringify(json, undefined, 2);
  }
  json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
      var cls = 'number';
      if (/^"/.test(match)) {
          if (/:$/.test(match)) {
              cls = 'key';
          } else {
              cls = 'string';
          }
      } else if (/true|false/.test(match)) {
          cls = 'boolean';
      } else if (/null/.test(match)) {
          cls = 'null';
      }
      return '<span class="json-' + cls + '">' + match + '</span>';
  });
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatJson(obj) {
  if (!obj) return '';
  try {
    const parsed = typeof obj === 'string' ? JSON.parse(obj) : obj;
    return syntaxHighlight(JSON.stringify(parsed, null, 2));
  } catch(e) {
    return syntaxHighlight(typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2));
  }
}

function showTraceDetail(trace_id) {
  const t = allTraces.find(x => x.trace_id === trace_id);
  if (!t) return;
  
  document.getElementById('traces-list-view').style.display = 'none';
  document.getElementById('session-stats').style.display = 'none';
  document.getElementById('traces-detail-view').style.display = 'flex';
  
  const content = document.getElementById('trace-detail-content');
  
  let html = `
    <div class="trace-detail-bar">
      <button class="tdb-back" onclick="closeTraceDetail()">← Back</button>
      <div class="tdb-divider"></div>
      <div class="tdb-id"><span class="tdb-muted">TRACE /</span> ${t.trace_id}</div>
      <div class="tdb-divider"></div>
      <div class="tdb-pills">
        <span class="tdb-pill tdb-dur">${((t.total_duration_ms || 0) / 1000).toFixed(2)}s</span>
        <span class="tdb-pill tdb-cost">$${(t.total_cost_usd || 0).toFixed(4)}</span>
        <span class="tdb-pill tdb-tokens">${t.total_tokens_in} / ${t.total_tokens_out}</span>
        <span class="tdb-pill tdb-batt">${(t.drone_state_snapshot?.battery_pct || 0)}%</span>
      </div>
    </div>
    <div class="trace-bar-msg">"${t.user_message}"</div>
    <div class="trace-split-layout">
      <div class="trace-tree-pane">
        <div class="trace-tree">
  `;
  
  // Group tool calls under the preceding LLM call for a clean tree structure
  const nodes = [];
  let currentGroup = null;

  (t.spans || []).forEach(s => {
    if (s.span_type === 'llm_call' || s.span_type === 'vision_call') {
      currentGroup = { parent: s, children: [] };
      nodes.push(currentGroup);
    } else if (s.span_type === 'tool_call' && currentGroup) {
      currentGroup.children.push(s);
    } else {
      // Standalone
      nodes.push({ parent: s, children: [] });
    }
  });

  nodes.forEach((node, nodeIdx) => {
    html += renderTreeHeaderNode(trace_id, node.parent, false);
    if (node.children.length > 0) {
      html += `<div class="tree-children">`;
      node.children.forEach((child, childIdx) => {
        html += renderTreeHeaderNode(trace_id, child, true);
      });
      html += `</div>`;
    }
  });

  html += `
        </div>
      </div>
      <div class="trace-data-pane" id="trace-data-pane">
        <div class="payload-section">
          <div class="payload-label">TRACE OVERVIEW</div>
          <div class="payload-text">Select a span on the left to view data.</div>
        </div>
      </div>
    </div>
  `;
  content.innerHTML = html;

  // auto-select first node if exists
  if (t.spans && t.spans.length > 0) {
    selectSpan(trace_id, t.spans[0].span_id);
  }
}

function renderTreeHeaderNode(trace_id, s, isChild) {
  let headerTitle = '';
  let headerMeta = '';
  const statusIcon = s.status === 'ok' ? '<svg viewBox="0 0 24 24" fill="none" class="icon-ok" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>' : '<svg viewBox="0 0 24 24" fill="none" class="icon-err" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
  
  const dur = ((s.duration_ms||0) / 1000).toFixed(2) + 's';
  const cost = s.cost_usd ? `$${(s.cost_usd).toFixed(4)}` : '';

  if (s.span_type === 'llm_call') {
    headerTitle = `<span class="tag tag-llm">LLM</span> ${s.model}`;
    headerMeta = `<span>${dur}</span> ${cost ? `<span>${cost}</span>` : ''}`;
  } else if (s.span_type === 'vision_call') {
    const mode = s.vision_mode === 'quick' ? '⚡ Quick' : '🧠 Deep';
    headerTitle = `<span class="tag tag-vision">VIS</span> ${s.vision_model}`;
    headerMeta = `<span>${mode}</span> <span>${dur}</span> ${cost ? `<span>${cost}</span>` : ''}`;
  } else if (s.span_type === 'tool_call') {
    headerTitle = `<span class="tag tag-tool">TOOL</span> ${s.tool_name}`;
    headerMeta = `<span>${dur}</span>`;
  } else {
    headerTitle = `<span class="tag tag-default">${s.span_type.toUpperCase()}</span> ${s.name || ''}`;
    headerMeta = `<span>${dur}</span>`;
  }

  return `
    <div class="tree-node ${isChild ? 'is-child' : ''}">
      <div class="node-header" id="node-header-${s.span_id}" onclick="selectSpan('${trace_id}', '${s.span_id}')">
        <div class="node-title">${headerTitle}</div>
        <div class="node-meta">${headerMeta} ${statusIcon}</div>
      </div>
    </div>
  `;
}

function selectSpan(trace_id, span_id) {
  document.querySelectorAll('.node-header').forEach(el => el.classList.remove('selected'));
  const el = document.getElementById(`node-header-${span_id}`);
  if (el) el.classList.add('selected');

  const t = allTraces.find(x => x.trace_id === trace_id);
  if (!t) return;
  const s = t.spans.find(x => x.span_id === span_id);
  if (!s) return;

  const dataPane = document.getElementById('trace-data-pane');
  dataPane.innerHTML = renderSpanPayload(s);
}

function renderSpanPayload(s) {
  let payloadContent = '';
  
    if (s.span_type === 'llm_call') {
      payloadContent += `
        <div class="payload-section">
          <div class="payload-label">TOKENS & PARAMS</div>
          <div class="pill-grid">
            <div class="content-pill tokens-in">
              <span class="pill-label">Prompt</span>
              <span class="pill-value">${s.tokens_in || 0}</span>
            </div>
            <div class="content-pill tokens-out">
              <span class="pill-label">Completion</span>
              <span class="pill-value">${s.tokens_out || 0}</span>
            </div>
            ${s.thinking_tokens ? `
            <div class="content-pill tokens-thinking">
              <span class="pill-label">Thinking</span>
              <span class="pill-value">${s.thinking_tokens}</span>
            </div>` : ''}
            ${s.model_parameters ? `<div class="pill-spacer"></div>` + Object.entries(s.model_parameters).map(([key, val], idx) => `
            <div class="content-pill param${idx === 0 ? ' param-first' : ''}">
              <span class="pill-label">${key.replace(/_/g, ' ')}</span>
              <span class="pill-value">${val}</span>
            </div>
            `).join('') : ''}
          </div>
        </div>
      `;
    if (s.thought_summary) {
      payloadContent += `
        <div class="payload-section thought-section">
          <div class="payload-label thought-label">THOUGHT SUMMARY</div>
          <div class="thought-body">${escapeHtml(s.thought_summary)}</div>
        </div>
      `;
    }
    if (s.input_messages) {
      payloadContent += `
        <div class="payload-section">
          <div class="payload-label">INPUT MESSAGES</div>
          <pre class="span-json">${formatJson(s.input_messages)}</pre>
        </div>
      `;
    }
    if (s.output_message) {
      payloadContent += `
        <div class="payload-section">
          <div class="payload-label">OUTPUT MESSAGE</div>
          <pre class="span-json">${formatJson(s.output_message)}</pre>
        </div>
      `;
    }
  } else if (s.span_type === 'vision_call') {
    payloadContent += `
      <div class="payload-section">
        <div class="payload-label">TOKENS & PARAMS</div>
        <div class="pill-grid">
          <div class="content-pill tokens-in">
            <span class="pill-label">Prompt</span>
            <span class="pill-value">${s.tokens_in || 0}</span>
          </div>
          <div class="content-pill tokens-out">
            <span class="pill-label">Completion</span>
            <span class="pill-value">${s.tokens_out || 0}</span>
          </div>
          ${s.thinking_tokens ? `
          <div class="content-pill tokens-thinking">
            <span class="pill-label">Thinking</span>
            <span class="pill-value">${s.thinking_tokens}</span>
          </div>` : ''}
          ${s.model_parameters ? `<div class="pill-spacer"></div>` + Object.entries(s.model_parameters).map(([key, val], idx) => `
          <div class="content-pill param${idx === 0 ? ' param-first' : ''}">
            <span class="pill-label">${key.replace(/_/g, ' ')}</span>
            <span class="pill-value">${val}</span>
          </div>
          `).join('') : ''}
        </div>
      </div>
    `;
    
    payloadContent += `
      <div class="payload-section">
        <div class="payload-label">PROMPT</div>
        <div class="payload-text">${s.vision_prompt}</div>
      </div>
    `;
    if (s.frame_thumbnail_b64) {
      payloadContent += `
        <div class="payload-section">
          <div class="payload-label">IMAGE INPUT</div>
          <img class="payload-image" src="data:image/jpeg;base64,${s.frame_thumbnail_b64}" />
        </div>
      `;
    }
    if (s.thought_summary) {
      payloadContent += `
        <div class="payload-section thought-section">
          <div class="payload-label thought-label">THOUGHT SUMMARY</div>
          <div class="thought-body">${escapeHtml(s.thought_summary)}</div>
        </div>
      `;
    }
    if (s.vision_response) {
      payloadContent += `
        <div class="payload-section">
          <div class="payload-label">RESPONSE</div>
          <pre class="span-json">${formatJson(s.vision_response)}</pre>
        </div>
      `;
    }
  } else if (s.span_type === 'tool_call') {
    payloadContent += `
      <div class="payload-section">
        <div class="payload-label">ARGUMENTS</div>
        <pre class="span-json">${formatJson(s.tool_args)}</pre>
      </div>
      <div class="payload-section">
        <div class="payload-label">RESULT</div>
        <pre class="span-json">${formatJson(s.tool_result)}</pre>
      </div>
    `;
  }

  if (s.error) {
    payloadContent += `
      <div class="payload-section error-section">
        <div class="payload-label error-label">ERROR</div>
        <div class="payload-text">${s.error}</div>
      </div>
    `;
  }
  return payloadContent;
}

function closeTraceDetail() {
  document.getElementById('traces-list-view').style.display = 'flex';
  document.getElementById('session-stats').style.display = 'block';
  document.getElementById('traces-detail-view').style.display = 'none';
}

connect();
