const sessionId = 'session_' + Math.random().toString(36).substring(2, 10);
let pendingImages = [];
let pendingFiles = [];
let allCountriesData = {};

document.addEventListener('DOMContentLoaded', () => {
  setupNavigation();
  setupChatHandlers();
  setupDragAndDrop();
  setupClipboardPaste();
  loadCountryPortals();
});

function setupNavigation() {
  const railBtns = document.querySelectorAll('nav.rail button[data-group]');
  const sideGroups = document.querySelectorAll('.side-g');

  railBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetGroup = btn.getAttribute('data-group');
      railBtns.forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
      sideGroups.forEach(g => {
        if (g.getAttribute('data-group') === targetGroup) {
          g.removeAttribute('hidden');
        } else {
          g.setAttribute('hidden', '');
        }
      });
    });
  });

  const portalSearchInput = document.getElementById('portalSearchInput');
  if (portalSearchInput) {
    portalSearchInput.addEventListener('input', (e) => {
      filterCountryList(e.target.value);
    });
  }

  const topSearchInput = document.getElementById('topSearchInput');
  if (topSearchInput) {
    topSearchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const val = topSearchInput.value.trim();
        if (val) {
          askAboutCountry(val);
        }
      }
    });
  }
}

async function loadCountryPortals() {
  try {
    const res = await fetch('/api/countries');
    if (res.ok) {
      allCountriesData = await res.json();
      renderCountrySidebar(allCountriesData);
      populateCountryDropdown(allCountriesData);
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
  });
}

function renderCountrySidebar(data) {
  const container = document.getElementById('countryListContainer');
  if (!container) return;
  container.innerHTML = '';
  const names = Object.keys(data).sort();
  names.forEach(name => {
    const item = document.createElement('div');
    item.className = 'side-country-item';
    const code = data[name].code || '';
    item.innerHTML = `<span>${name}</span><span class="c-code">${code}</span>`;
    item.addEventListener('click', () => {
      setCountryContext(name);
      askAboutCountry(name);
    });
    container.appendChild(item);
  });
}

function setCountryContext(countryName) {
  const select = document.getElementById('countrySelect');
  if (select && allCountriesData[countryName]) {
    select.value = countryName;
    localStorage.setItem('letterlens_country', countryName);
  }
}

function filterCountryList(query) {
  const container = document.getElementById('countryListContainer');
  if (!container) return;
  const q = query.trim().toLowerCase();
  const items = container.querySelectorAll('.side-country-item');
  items.forEach(el => {
    const text = el.textContent.toLowerCase();
    el.style.display = text.includes(q) ? 'flex' : 'none';
  });
}

function askAboutCountry(countryName) {
  const input = document.getElementById('userInput');
  input.value = `What are the verified official government legal portals, gazettes, and tenancy registries for ${countryName}?`;
  input.focus();
}

function setupChatHandlers() {
  const form = document.getElementById('chatForm');
  const input = document.getElementById('userInput');
  const fileInput = document.getElementById('fileInput');

  form.addEventListener('submit', handleSubmit);

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event('submit'));
    }
  });

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
          pendingFiles.push({ name: 'lease_amendment_notice.md', content: text });
          renderPreviews();
          input.value = 'Explain this notice in simple English and cite official legal portals with footnotes.';
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
          pendingImages.push({ name: 'sample_letter_scan.png', dataUrl: dataUrl });
          renderPreviews();
          input.value = 'Explain this scanned letter in simple English and cite official country legal portals in footnotes.';
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

  const sideWhatCanYouDo = document.getElementById('sideWhatCanYouDo');
  if (sideWhatCanYouDo) {
    sideWhatCanYouDo.addEventListener('click', () => {
      input.value = 'What can you do?';
      form.dispatchEvent(new Event('submit'));
    });
  }
}

function setupDragAndDrop() {
  const zone = document.getElementById('dropZone');

  ['dragenter', 'dragover'].forEach(name => {
    window.addEventListener(name, (e) => {
      e.preventDefault();
      zone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach(name => {
    window.addEventListener(name, (e) => {
      e.preventDefault();
      zone.classList.remove('dragover');
    });
  });

  window.addEventListener('drop', (e) => {
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
    pendingImages.push({ name: file.name || 'pasted_image.png', dataUrl: e.target.result });
    renderPreviews();
  };
  reader.readAsDataURL(file);
}

function readFileAsText(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    pendingFiles.push({ name: file.name, content: e.target.result });
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
    chip.innerHTML = `<span>[Image] ${escapeHtml(img.name)}</span><button type="button" title="Remove">&times;</button>`;
    chip.querySelector('button').addEventListener('click', () => {
      pendingImages.splice(idx, 1);
      renderPreviews();
    });
    bar.appendChild(chip);
  });
  pendingFiles.forEach((f, idx) => {
    const chip = document.createElement('div');
    chip.className = 'upload-chip';
    chip.innerHTML = `<span>[Doc] ${escapeHtml(f.name)}</span><button type="button" title="Remove">&times;</button>`;
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
      chip.textContent = '[Image] ' + im.name;
      attachments.appendChild(chip);
    });
    files.forEach(f => {
      const chip = document.createElement('span');
      chip.className = 'upload-chip';
      chip.textContent = '[File] ' + f.name;
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
  return textDiv;
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
  addMessage('u', text || '(Analyzed attachments)', currentImages, currentFiles);
  const bubble = addMessage('a', 'LetterLens Agent is analyzing input with Gemma 4 and official legal portals...');
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
            bubble.innerHTML = parseMarkdown(accumulated);
          } else if (data.error) {
            accumulated += `\n\nError: ${data.error}`;
            bubble.innerHTML = parseMarkdown(accumulated);
          }
        } catch (parseErr) {
          console.error(parseErr);
        }
      }
    }
  } catch (err) {
    bubble.innerHTML = `<span style="color:#ff6b6b">Communication error: ${escapeHtml(err.message)}</span>`;
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
