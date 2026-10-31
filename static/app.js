(function () {
  'use strict';

  const R = window.LLRender;
  const $ = (id) => document.getElementById(id);
  const MAX_ATTACH = 5;
  const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
  const TEXT_EXT = /\.(md|markdown|txt|rst|csv|log|json|ya?ml|html)$/i;
  const IMAGE_EXT = /\.(png|jpe?g|webp|gif)$/i;

  const state = { sid: newSid(), images: [], files: [], busy: false, controller: null, countries: {} };

  function newSid() {
    const bytes = new Uint8Array(12);
    crypto.getRandomValues(bytes);
    return 's' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* storage blocked */ } },
  };

  let toastTimer = 0;
  function toast(message) {
    const el = $('toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
  }

  function formatBytes(n) {
    if (n < 1024) return n + ' B';
    if (n < 1048576) return Math.round(n / 1024) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function readAs(file, method) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader[method](file);
    });
  }

  async function shrinkImage(file) {
    if (file.type === 'image/gif' || file.size < 600 * 1024) return readAs(file, 'readAsDataURL');
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1800 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
    return readAs(blob, 'readAsDataURL');
  }

  async function addFiles(list) {
    for (const file of Array.from(list)) {
      if (state.images.length + state.files.length >= MAX_ATTACH) {
        toast('You can attach up to ' + MAX_ATTACH + ' files at once.');
        break;
      }
      try {
        if (IMAGE_TYPES.includes(file.type) || IMAGE_EXT.test(file.name)) {
          const dataUrl = await shrinkImage(file);
          state.images.push({ name: file.name || 'pasted-image.png', dataUrl, size: file.size });
        } else if (TEXT_EXT.test(file.name)) {
          const content = await readAs(file, 'readAsText');
          state.files.push({ name: file.name, content, size: file.size });
        } else {
          toast(file.name + ' is not supported. Use a photo (PNG, JPG, WebP) or a .md or .txt file.');
        }
      } catch (err) {
        toast('Could not read ' + (file.name || 'that file') + '. Try a JPG or PNG.');
      }
    }
    renderChips();
  }

  function chip(label, size, thumb, onRemove) {
    const el = document.createElement('div');
    el.className = 'chip';
    if (thumb) {
      const img = document.createElement('img');
      img.src = thumb;
      img.alt = '';
      el.appendChild(img);
    }
    const name = document.createElement('span');
    name.className = 'chip-name';
    name.textContent = label;
    const meta = document.createElement('span');
    meta.className = 'chip-size';
    meta.textContent = formatBytes(size);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.setAttribute('aria-label', 'Remove ' + label);
    remove.textContent = '×';
    remove.addEventListener('click', onRemove);
    el.append(name, meta, remove);
    return el;
  }

  function renderChips() {
    const bar = $('previews');
    bar.replaceChildren();
    state.images.forEach((img, i) => bar.appendChild(chip(img.name, img.size, img.dataUrl, () => {
      state.images.splice(i, 1);
      renderChips();
    })));
    state.files.forEach((f, i) => bar.appendChild(chip(f.name, f.size, '', () => {
      state.files.splice(i, 1);
      renderChips();
    })));
    bar.hidden = !(state.images.length + state.files.length);
  }

  function addMessage(role, text, attachments) {
    $('app').classList.add('has-chat');
    const wrap = document.createElement('article');
    wrap.className = 'msg ' + role;
    const who = document.createElement('div');
    who.className = 'msg-who';
    who.textContent = role === 'user' ? 'You' : 'LetterLens';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    if (attachments && attachments.length) {
      const row = document.createElement('div');
      row.className = 'attached';
      attachments.forEach((name) => {
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.textContent = name;
        row.appendChild(tag);
      });
      bubble.appendChild(row);
    }
    const body = document.createElement('div');
    body.className = 'body';
    if (role === 'user') body.textContent = text;
    bubble.appendChild(body);
    wrap.append(who, bubble);
    $('log').appendChild(wrap);
    wrap.scrollIntoView({ block: 'end', behavior: 'smooth' });
    return { wrap, bubble, body };
  }

  function addActions(msg, raw, took) {
    const bar = document.createElement('div');
    bar.className = 'actions';
    const button = (label, onClick) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.addEventListener('click', onClick);
      bar.appendChild(b);
      return b;
    };
    const copy = button('Copy text', async () => {
      try {
        await navigator.clipboard.writeText(raw);
        copy.textContent = 'Copied';
        setTimeout(() => { copy.textContent = 'Copy text'; }, 1800);
      } catch (e) {
        toast('Copy was blocked by the browser.');
      }
    });
    const glance = R.parseGlance(raw);
    const due = (glance['due date'] || '').match(/\d{4}-\d{2}-\d{2}/);
    if (due) {
      button('Add deadline to calendar', () => {
        const ics = R.icsFor('Deadline: ' + (glance.document || 'legal document'), due[0],
          'From LetterLens. Check the original document before you act.');
        const url = URL.createObjectURL(new Blob([ics], { type: 'text/calendar' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = 'letterlens-deadline.ics';
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
    }
    button('Print', () => {
      msg.wrap.classList.add('print-this');
      window.print();
    });
    if (took) {
      const note = document.createElement('span');
      note.className = 'took';
      note.textContent = took;
      bar.appendChild(note);
    }
    msg.bubble.appendChild(bar);
  }

  function setBusy(busy) {
    state.busy = busy;
    $('sendBtn').hidden = busy;
    $('stopBtn').hidden = !busy;
  }

  function parseLine(line) {
    try { return JSON.parse(line); } catch (e) { return null; }
  }

  async function readEvents(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    const flush = (lines) => {
      lines.filter((l) => l.trim()).map(parseLine).filter(Boolean).forEach(onEvent);
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      flush(lines);
    }
    flush([buffer]);
  }

  async function send(override) {
    if (state.busy) return;
    const input = $('userInput');
    const text = override != null ? override : input.value.trim();
    const images = state.images.slice();
    const files = state.files.slice();
    if (!text && !images.length && !files.length) return;

    state.images = [];
    state.files = [];
    renderChips();
    input.value = '';
    autosize();
    addMessage('user', text || 'Please explain this document.', [...images, ...files].map((a) => a.name));
    const msg = addMessage('agent', '');
    msg.body.innerHTML = '<span class="dots" role="status" aria-label="Reading your document"><i></i><i></i><i></i></span>';
    setBusy(true);
    state.controller = new AbortController();

    let raw = '';
    let frame = 0;
    let firstAt = null;
    let finished = false;
    const startedAt = performance.now();
    const timer = document.createElement('p');
    timer.className = 'elapsed';
    timer.textContent = '0.0 s';
    msg.bubble.appendChild(timer);
    const tick = setInterval(() => { timer.textContent = R.duration(performance.now() - startedAt); }, 100);
    const warnings = [];
    const draw = () => {
      frame = 0;
      msg.body.innerHTML = R.render(raw) + (state.busy ? '<span class="caret"></span>' : '');
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(draw); };
    const notice = (kind, message) => {
      const el = document.createElement('p');
      el.className = 'notice notice-' + kind;
      el.textContent = message;
      msg.bubble.appendChild(el);
    };

    try {
      const response = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: state.controller.signal,
        body: JSON.stringify({
          sid: state.sid,
          message: text,
          images: images.map((i) => i.dataUrl),
          files: files.map((f) => ({ name: f.name, content: f.content })),
          country: $('countrySelect').value,
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || 'Request failed (' + response.status + ')');
      }
      await readEvents(response, (ev) => {
        if (ev.t) {
          if (firstAt === null) firstAt = performance.now() - startedAt;
          raw += ev.t;
          schedule();
        }
        else if (ev.reset) { raw = ''; schedule(); }
        else if (ev.status) { $('sr').textContent = ev.status; }
        else if (ev.warn) { warnings.push(ev.warn); }
        else if (ev.error) { throw new Error(ev.error); }
        else if (ev.done) { finished = true; }
      });
      if (!finished && !raw) throw new Error('The connection closed before an answer arrived. Check that the server is still running.');
    } catch (err) {
      if (err.name === 'AbortError') notice('info', 'Stopped.');
      else notice('error', err.message);
    } finally {
      const total = performance.now() - startedAt;
      clearInterval(tick);
      timer.remove();
      setBusy(false);
      cancelAnimationFrame(frame);
      msg.body.innerHTML = R.render(raw);
      warnings.forEach((w) => notice('warn', w));
      if (raw) addActions(msg, raw, R.tookText(total, firstAt));
      $('sr').textContent = 'Answer ready.';
      input.focus();
    }
  }

  function autosize() {
    const input = $('userInput');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  }

  async function reset() {
    if (state.controller) state.controller.abort();
    const old = state.sid;
    state.sid = newSid();
    state.images = [];
    state.files = [];
    renderChips();
    $('log').replaceChildren();
    $('app').classList.remove('has-chat');
    $('userInput').value = '';
    autosize();
    fetch('/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid: old }),
    }).catch(() => {});
  }

  async function useSample(kind) {
    try {
      if (kind === 'scan') {
        const blob = await (await fetch('/sample/scan.png')).blob();
        state.images.push({ name: 'sample-scan.png', dataUrl: await readAs(blob, 'readAsDataURL'), size: blob.size });
      } else {
        const path = kind === 'notice' ? '/sample/notice.md' : '/sample/rent_hu.txt';
        const content = await (await fetch(path)).text();
        state.files.push({ name: kind === 'notice' ? 'lease-notice.md' : 'rent-letter-hu.txt', content, size: content.length });
      }
      renderChips();
      send('');
    } catch (e) {
      toast('Could not load the sample.');
    }
  }

  async function loadStatus() {
    try {
      const status = await (await fetch('/api/status')).json();
      const el = $('modeChip');
      const offline = status.mode === 'offline';
      el.textContent = offline ? 'Offline: local model' : 'Cloud: ' + status.model;
      el.className = 'mode ' + (offline ? 'mode-offline' : 'mode-cloud');
      el.title = offline
        ? 'Your document stays on this computer.'
        : 'Your document is sent to the model provider to be read.';
      if (!offline && !status.keys) {
        el.textContent = 'No API key';
        el.className = 'mode mode-warn';
        el.title = 'Add GEMINI_1_KEY to .env, or start with --offline.';
      }
    } catch (e) { /* status is optional */ }
  }

  function updateCountryNote() {
    const name = $('countrySelect').value;
    const info = state.countries[name];
    $('countryNote').textContent = info
      ? (info.portals || []).length + ' official links (starter list)'
      : 'Country not set';
  }

  async function loadCountries() {
    try {
      state.countries = await (await fetch('/api/countries')).json();
    } catch (e) {
      return;
    }
    const select = $('countrySelect');
    Object.keys(state.countries).sort().forEach((name) => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    });
    const saved = store.get('letterlens_country');
    select.value = saved !== null && (saved === '' || state.countries[saved]) ? saved : 'Hungary';
    updateCountryNote();
    select.addEventListener('change', () => {
      store.set('letterlens_country', select.value);
      updateCountryNote();
    });
  }

  function setupDrop() {
    let depth = 0;
    const overlay = $('dropOverlay');
    window.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; overlay.hidden = false; });
    window.addEventListener('dragleave', (e) => {
      e.preventDefault();
      depth = Math.max(0, depth - 1);
      if (!depth) overlay.hidden = true;
    });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => {
      e.preventDefault();
      depth = 0;
      overlay.hidden = true;
      if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });
    window.addEventListener('paste', (e) => {
      const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
      if (files.length) addFiles(files);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('chatForm').addEventListener('submit', (e) => { e.preventDefault(); send(); });
    $('userInput').addEventListener('input', autosize);
    $('userInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    $('attachBtn').addEventListener('click', () => $('fileInput').click());
    $('pickBtn').addEventListener('click', () => $('fileInput').click());
    $('fileInput').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
    $('stopBtn').addEventListener('click', () => state.controller && state.controller.abort());
    $('btnNew').addEventListener('click', reset);
    document.querySelectorAll('[data-sample]').forEach((b) =>
      b.addEventListener('click', () => useSample(b.dataset.sample)));
    window.addEventListener('afterprint', () =>
      document.querySelectorAll('.print-this').forEach((el) => el.classList.remove('print-this')));
    setupDrop();
    loadStatus();
    loadCountries();
    renderChips();
  });
})();
