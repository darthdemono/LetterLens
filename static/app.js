const sessionId = 'session_' + Math.random().toString(36).substring(2, 10);
let pendingImages = [];
let pendingFiles = [];
let allCountriesData = {};
let dragCounter = 0;

document.addEventListener('DOMContentLoaded', () => {
  setupChatHandlers();
  setupDragAndDrop();
  setupClipboardPaste();
  setupAutoResizeTextarea();
  setupNewDocAndClear();
  loadCountryPortals();
});

function setupAutoResizeTextarea() {
  const textarea = document.getElementById('userInput');
  if (!textarea) return;

  function resize() {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 180) + 'px';
  }

  textarea.addEventListener('input', resize);
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      document.getElementById('chatForm').dispatchEvent(new Event('submit'));
    }
  });
}

function setupNewDocAndClear() {
  const btnNewDoc = document.getElementById('btnNewDoc');
  const btnClearChat = document.getElementById('btnClearChat');

  function resetChat() {
    pendingImages = [];
    pendingFiles = [];
    renderPreviews();

    const log = document.getElementById('log');
    if (log) {
      log.innerHTML = `
        <div class="msg-wrap agent">
          <div class="msg-sender">LetterLens Agent</div>
          <div class="msg-bubble">
            <p><strong>LetterLens Agent is ready.</strong> Provide an official letter, notice, bill, or legal document as a <strong>picture</strong>, <strong>Markdown file (.md)</strong>, or <strong>plain text</strong>.</p>
            <p>The agent consults official country legal portals as its primary source of truth and explains documents in clear A1–B2 plain English with verified hyperlinks in footnotes.</p>
          </div>
        </div>
      `;
    }

    const input = document.getElementById('userInput');
    if (input) {
      input.value = '';
      input.style.height = 'auto';
      input.focus();
    }
  }

  if (btnNewDoc) btnNewDoc.addEventListener('click', resetChat);
  if (btnClearChat) btnClearChat.addEventListener('click', resetChat);
}

async function loadCountryPortals() {
  try {
    const res = await fetch('/api/countries');
    if (res.ok) {
      allCountriesData = await res.json();
      populateCountryDropdown(allCountriesData);
      updateJurisdictionBadge();
    }
  } catch (err) {
    console.error('Failed to load countries', err);
  }
}

function populateCountryDropdown(data) {
  const select = document.getElementById('countrySelect');
  if (!select) return;
  const current = localStorage.getItem('letterlens_country') || '';
  const names = Object.keys(data).sort();
  names.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = `${name} (${data[name].code || ''})`;
    select.appendChild(opt);
  });
  if (current && data[current]) {
    select.value = current;
  }
  select.addEventListener('change', () => {
    localStorage.setItem('letterlens_country', select.value);
    updateJurisdictionBadge();
  });
}

function updateJurisdictionBadge() {
  const select = document.getElementById('countrySelect');
  const label = document.getElementById('jurisdictionLabel');
  if (!label) return;

  const val = select ? select.value : '';
  if (val && allCountriesData[val]) {
    const code = allCountriesData[val].code || '';
    const portals = (allCountriesData[val].portals || []).length;
    label.innerHTML = `Active Jurisdiction: <strong>${escapeHtml(val)} (${code})</strong> &bull; ${portals} Official Portals Injected into Context`;
  } else {
    label.innerHTML = `Active Jurisdiction: <strong>Auto-detect from document</strong> (select country above to fix jurisdiction)`;
  }
}

function setupChatHandlers() {
  const form = document.getElementById('chatForm');
  const input = document.getElementById('userInput');
  const fileInput = document.getElementById('fileInput');

  form.addEventListener('submit', handleSubmit);

  document.getElementById('attachBtn').addEventListener('click', () => fileInput.click());

  fileInput.addEventListener('change', (e) => {
    handleFilesList(e.target.files);
    fileInput.value = '';
  });

  const btnSampleNotice = document.getElementById('btnSampleNotice');
  if (btnSampleNotice) {
    btnSampleNotice.addEventListener('click', async () => {
      try {
        const res = await fetch('/sample/notice.md');
        if (res.ok) {
          const text = await res.text();
          pendingFiles.push({ name: 'lease_amendment_notice.md', content: text, size: text.length });
          renderPreviews();
          input.value = 'Explain this notice in simple English and cite official legal portals with footnotes.';
          input.style.height = 'auto';
          input.focus();
        }
      } catch (err) {
        console.error(err);
      }
    });
  }

  const btnSampleScan = document.getElementById('btnSampleScan');
  if (btnSampleScan) {
    btnSampleScan.addEventListener('click', async () => {
      try {
        const res = await fetch('/sample/scan.png');
        if (res.ok) {
          const dataUrl = await res.text();
          pendingImages.push({ name: 'sample_letter_scan.png', dataUrl: dataUrl, size: Math.round(dataUrl.length * 0.75) });
          renderPreviews();
          input.value = 'Explain this scanned letter in simple English and cite official country legal portals in footnotes.';
          input.style.height = 'auto';
          input.focus();
        }
      } catch (err) {
        console.error(err);
      }
    });
  }

  const btnWhatCanYouDo = document.getElementById('btnWhatCanYouDo');
  if (btnWhatCanYouDo) {
    btnWhatCanYouDo.addEventListener('click', () => {
      input.value = 'What can you do? Explain your capabilities and how you analyze legal documents.';
      form.dispatchEvent(new Event('submit'));
    });
  }
}

function setupDragAndDrop() {
  const overlay = document.getElementById('dropOverlay');
  const zone = document.getElementById('dropZone');

  window.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragCounter++;
    if (overlay) overlay.removeAttribute('hidden');
    if (zone) zone.classList.add('dragover');
  });

  window.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      if (overlay) overlay.setAttribute('hidden', '');
      if (zone) zone.classList.remove('dragover');
    }
  });

  window.addEventListener('dragover', (e) => {
    e.preventDefault();
  });

  window.addEventListener('drop', (e) => {
    e.preventDefault();
    dragCounter = 0;
    if (overlay) overlay.setAttribute('hidden', '');
    if (zone) zone.classList.remove('dragover');

    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFilesList(e.dataTransfer.files);
    }
  });
}

function setupClipboardPaste() {
  window.addEventListener('paste', (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf('image') !== -1) {
        const file = items[i].getAsFile();
        if (file) {
          readFileAsImage(file);
        }
      }
    }
  });
}

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function handleFilesList(files) {
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const name = file.name.toLowerCase();
    if (name.endsWith('.png') || name.endsWith('.jpg') || name.endsWith('.jpeg') || name.endsWith('.webp')) {
      readFileAsImage(file);
    } else {
      readFileAsText(file);
    }
  }
}

function readFileAsImage(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    pendingImages.push({
      name: file.name || 'pasted_image.png',
      dataUrl: e.target.result,
      size: file.size || Math.round(e.target.result.length * 0.75)
    });
    renderPreviews();
  };
  reader.readAsDataURL(file);
}

function readFileAsText(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    pendingFiles.push({
      name: file.name,
      content: e.target.result,
      size: file.size || e.target.result.length
    });
    renderPreviews();
  };
  reader.readAsText(file);
}

function renderPreviews() {
  const bar = document.getElementById('previewBar');
  bar.innerHTML = '';

  pendingImages.forEach((img, idx) => {
    const chip = document.createElement('div');
    chip.className = 'upload-chip';
    chip.innerHTML = `
      <img class="thumb" src="${img.dataUrl}" alt="Preview">
      <span class="chip-name">${escapeHtml(img.name)}</span>
      <span class="chip-size">${formatBytes(img.size)}</span>
      <button type="button" title="Remove">&times;</button>
    `;
    chip.querySelector('button').addEventListener('click', () => {
      pendingImages.splice(idx, 1);
      renderPreviews();
    });
    bar.appendChild(chip);
  });

  pendingFiles.forEach((f, idx) => {
    const chip = document.createElement('div');
    chip.className = 'upload-chip';
    chip.innerHTML = `
      <span class="file-badge">DOC</span>
      <span class="chip-name">${escapeHtml(f.name)}</span>
      <span class="chip-size">${formatBytes(f.size)}</span>
      <button type="button" title="Remove">&times;</button>
    `;
    chip.querySelector('button').addEventListener('click', () => {
      pendingFiles.splice(idx, 1);
      renderPreviews();
    });
    bar.appendChild(chip);
  });
}

function addMessage(role, rawContent, imgs = [], files = []) {
  const log = document.getElementById('log');
  const wrap = document.createElement('div');
  wrap.className = `msg-wrap ${role === 'u' ? 'user' : 'agent'}`;

  const sender = document.createElement('div');
  sender.className = 'msg-sender';
  sender.textContent = role === 'u' ? 'You' : 'LetterLens Agent';
  wrap.appendChild(sender);

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';

  if (imgs.length > 0 || files.length > 0) {
    const attachments = document.createElement('div');
    attachments.style.display = 'flex';
    attachments.style.gap = '8px';
    attachments.style.marginBottom = '8px';
    attachments.style.flexWrap = 'wrap';

    imgs.forEach(im => {
      const chip = document.createElement('span');
      chip.className = 'upload-chip';
      chip.innerHTML = `<span class="file-badge">IMG</span> <span class="chip-name">${escapeHtml(im.name)}</span>`;
      attachments.appendChild(chip);
    });
    files.forEach(f => {
      const chip = document.createElement('span');
      chip.className = 'upload-chip';
      chip.innerHTML = `<span class="file-badge">DOC</span> <span class="chip-name">${escapeHtml(f.name)}</span>`;
      attachments.appendChild(chip);
    });
    bubble.appendChild(attachments);
  }

  const textDiv = document.createElement('div');
  textDiv.className = 'msg-content';
  textDiv.innerHTML = role === 'u' ? escapeHtml(rawContent).replace(/\n/g, '<br>') : parseMarkdown(rawContent);
  bubble.appendChild(textDiv);

  wrap.appendChild(bubble);
  log.appendChild(wrap);
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  return { bubble, textDiv, wrap };
}

function attachBubbleActions(bubble, rawMarkdown) {
  if (bubble.querySelector('.bubble-actions')) return;

  const actions = document.createElement('div');
  actions.className = 'bubble-actions';

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'action-chip-btn copy-btn';
  copyBtn.innerHTML = `
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
    </svg>
    <span>Copy Summary</span>
  `;

  copyBtn.addEventListener('click', async () => {
    try {
      const cleanText = bubble.innerText.replace(/Copy Summary|Print \/ PDF/g, '').trim();
      await navigator.clipboard.writeText(cleanText);
      copyBtn.classList.add('copied');
      copyBtn.querySelector('span').textContent = 'Copied!';
      setTimeout(() => {
        copyBtn.classList.remove('copied');
        copyBtn.querySelector('span').textContent = 'Copy Summary';
      }, 2000);
    } catch (err) {
      console.error('Failed to copy', err);
    }
  });

  const printBtn = document.createElement('button');
  printBtn.type = 'button';
  printBtn.className = 'action-chip-btn print-btn';
  printBtn.innerHTML = `
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="6 9 6 2 18 2 18 9"></polyline>
      <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
      <rect x="6" y="14" width="12" height="8"></rect>
    </svg>
    <span>Print / PDF</span>
  `;
  printBtn.addEventListener('click', () => window.print());

  actions.appendChild(copyBtn);
  actions.appendChild(printBtn);
  bubble.appendChild(actions);
}

async function handleSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('userInput');
  const sendBtn = document.getElementById('sendBtn');
  const text = input.value.trim();

  if (!text && pendingImages.length === 0 && pendingFiles.length === 0) {
    return;
  }

  const currentImages = [...pendingImages];
  const currentFiles = [...pendingFiles];
  pendingImages = [];
  pendingFiles = [];
  renderPreviews();

  input.value = '';
  input.style.height = 'auto';

  addMessage('u', text || '(Analyzed attachments)', currentImages, currentFiles);
  const { bubble, textDiv } = addMessage('a', 'LetterLens Agent is analyzing input with Gemma 4 and official legal portals...');
  sendBtn.disabled = true;

  try {
    const countrySelect = document.getElementById('countrySelect');
    const payload = {
      sid: sessionId,
      message: text,
      images: currentImages.map(im => im.dataUrl),
      files: currentFiles,
      country: countrySelect ? countrySelect.value : ''
    };

    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      throw new Error(`HTTP error ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let accumulated = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const data = JSON.parse(line);
          if (data.t) {
            accumulated += data.t;
            textDiv.innerHTML = parseMarkdown(accumulated) + '<span class="streaming-cursor"></span>';
          } else if (data.error) {
            accumulated += `\n\nError: ${data.error}`;
            textDiv.innerHTML = parseMarkdown(accumulated);
          }
        } catch (parseErr) {
          console.error(parseErr);
        }
      }
    }

    textDiv.innerHTML = parseMarkdown(accumulated);
    attachBubbleActions(bubble, accumulated);
  } catch (err) {
    textDiv.innerHTML = `<span style="color:#ff6b6b">Communication error: ${escapeHtml(err.message)}</span>`;
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

function parseMarkdown(md) {
  if (!md) return '';
  const lines = md.split('\n');
  const out = [];
  let inList = false;
  let inOrdered = false;
  let inTable = false;
  let tableHeader = true;
  const footnoteDefs = [];

  function flushList() {
    if (inList) {
      out.push('</ul>');
      inList = false;
    }
    if (inOrdered) {
      out.push('</ol>');
      inOrdered = false;
    }
  }

  function flushTable() {
    if (inTable) {
      out.push('</tbody></table>');
      inTable = false;
      tableHeader = true;
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      flushList();
      flushTable();
      continue;
    }

    const fnMatch = trimmed.match(/^\[\^(\d+)\]:\s*(.*)$/);
    if (fnMatch) {
      flushList();
      flushTable();
      footnoteDefs.push({ id: fnMatch[1], text: fnMatch[2].trim() });
      continue;
    }

    const fnNumMatch = trimmed.match(/^(\d+)\.\s+(https?:\/\/[^\s]+)(.*)$/i);
    if (fnNumMatch) {
      flushList();
      flushTable();
      footnoteDefs.push({
        id: fnNumMatch[1],
        url: fnNumMatch[2],
        text: (fnNumMatch[2] + ' ' + (fnNumMatch[3] || '')).trim()
      });
      continue;
    }

    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      flushList();
      const cells = trimmed.slice(1, -1).split('|').map(c => c.trim());
      const isSep = cells.every(c => /^:?-+:?$/.test(c));

      if (isSep) {
        tableHeader = false;
        continue;
      }

      if (!inTable) {
        out.push('<table><thead><tr>');
        cells.forEach(c => out.push(`<th>${inlineFormat(c)}</th>`));
        out.push('</tr></thead><tbody>');
        inTable = true;
        tableHeader = false;
      } else {
        out.push('<tr>');
        cells.forEach(c => out.push(`<td>${inlineFormat(c)}</td>`));
        out.push('</tr>');
      }
      continue;
    } else {
      flushTable();
    }

    if (trimmed.startsWith('### ')) {
      flushList();
      out.push(`<h3>${inlineFormat(trimmed.slice(4))}</h3>`);
      continue;
    }
    if (trimmed.startsWith('## ')) {
      flushList();
      out.push(`<h2>${inlineFormat(trimmed.slice(3))}</h2>`);
      continue;
    }
    if (trimmed.startsWith('# ')) {
      flushList();
      out.push(`<h2>${inlineFormat(trimmed.slice(2))}</h2>`);
      continue;
    }

    if (trimmed.startsWith('* ') || trimmed.startsWith('- ')) {
      if (inOrdered) flushList();
      if (!inList) {
        out.push('<ul>');
        inList = true;
      }
      out.push(`<li>${inlineFormat(trimmed.slice(2))}</li>`);
      continue;
    }

    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (numMatch && !fnNumMatch) {
      if (inList) flushList();
      if (!inOrdered) {
        out.push('<ol>');
        inOrdered = true;
      }
      out.push(`<li>${inlineFormat(numMatch[2])}</li>`);
      continue;
    }

    flushList();

    if (trimmed.startsWith('Disclaimer:')) {
      out.push(`<div class="disclaimer-box">${inlineFormat(trimmed)}</div>`);
      continue;
    }

    out.push(`<p>${inlineFormat(trimmed)}</p>`);
  }

  flushList();
  flushTable();

  if (footnoteDefs.length > 0) {
    let fnHtml = '<div class="footnotes-section"><div class="footnotes-title">Footnotes &amp; Official Legal Sources</div><ol class="footnotes-list">';
    footnoteDefs.forEach((fn, idx) => {
      let url = fn.url;
      let desc = '';
      if (!url) {
        const urlMatch = fn.text.match(/(https?:\/\/[^\s\)\],]+)/i);
        if (urlMatch) {
          url = urlMatch[1];
          desc = fn.text.replace(url, '').replace(/^[-—:\s]+/, '').trim();
        } else {
          url = fn.text.trim();
        }
      } else {
        desc = fn.text.replace(url, '').replace(/^[-—:\s]+/, '').trim();
      }

      if (url.startsWith('http://') || url.startsWith('https://')) {
        const descSuffix = desc ? ` &mdash; ${escapeHtml(desc)}` : '';
        fnHtml += `<li value="${idx + 1}"><a href="${url}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>${descSuffix}</li>`;
      } else {
        fnHtml += `<li value="${idx + 1}">${escapeHtml(fn.text)}</li>`;
      }
    });
    fnHtml += '</ol></div>';
    out.push(fnHtml);
  }

  return out.join('');
}

function inlineFormat(txt) {
  let s = escapeHtml(txt);
  s = s.replace(/\[\^(\d+)\]/g, '<sup><a href="#fn-$1">[$1]</a></sup>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s\)<>"]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  return s;
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
