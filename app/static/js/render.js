/**
 * Rendering helpers. Everything interpolated into innerHTML goes through
 * `esc()` because invoice content originates from untrusted PDFs.
 */

const ESCAPES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

export function icon(name, className = 'icon') {
  return `<svg class="${className}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

const STATUS = {
  COMPLETED: { label: 'Completed', cls: 'status--completed', dot: 'dot--ok' },
  PROCESSING: { label: 'Processing', cls: 'status--processing', dot: 'dot--info dot--pulse' },
  QUEUED: { label: 'Queued', cls: 'status--queued', dot: '' },
  FAILED: { label: 'Failed', cls: 'status--failed', dot: 'dot--danger' },
};

export function statusMeta(status) {
  return STATUS[status] || { label: status || 'Unknown', cls: 'status--queued', dot: '' };
}

export function statusBadge(status) {
  const meta = statusMeta(status);
  return `<span class="status-badge ${meta.cls}"><span class="dot ${meta.dot}"></span>${esc(meta.label)}</span>`;
}

const ZERO_DECIMAL = new Set(['JPY', 'KRW', 'VND', 'CLP', 'ISK', 'UGX']);

export function formatMoney(value, currency) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';

  const code = /^[A-Z]{3}$/.test(String(currency || '')) ? String(currency) : null;
  const fractionDigits = ZERO_DECIMAL.has(code) ? 0 : 2;

  const options = {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  };

  if (code) {
    options.style = 'currency';
    options.currency = code;
    options.currencyDisplay = 'narrowSymbol';
  }

  try {
    return new Intl.NumberFormat(code === 'INR' ? 'en-IN' : 'en-US', options).format(number);
  } catch {
    return number.toLocaleString(undefined, options);
  }
}

export function formatQty(value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return esc(value);
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(number);
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(value) {
  if (!value) return null;
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(String(value));
  const date = dateOnly ? new Date(`${value}T00:00:00`) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);

  return date.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

export function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function relativeTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';

  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 30) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.round(seconds / 86400)}d ago`;
  return formatDate(value);
}

/* -------------------------------------------------------------------------- */
/* Stats                                                                      */
/* -------------------------------------------------------------------------- */

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

export function renderStats(jobs) {
  const total = jobs.length;
  const completed = jobs.filter((job) => job.status === 'COMPLETED').length;
  const active = jobs.filter(
    (job) => job.status === 'QUEUED' || job.status === 'PROCESSING'
  ).length;
  const failed = jobs.filter((job) => job.status === 'FAILED').length;

  setText('stat-total', total);
  setText('stat-completed', completed);
  setText('stat-active', active);
  setText('stat-failed', failed);

  const hint = document.getElementById('stat-success-hint');
  if (hint) {
    hint.textContent = total
      ? `${Math.round((completed / total) * 100)}% success rate · ${completed} of ${total}`
      : 'No completed extractions yet';
  }

  setText('nav-job-count', total);
}

/* -------------------------------------------------------------------------- */
/* Jobs table                                                                 */
/* -------------------------------------------------------------------------- */

function documentCell(job) {
  const invoice = job.result?.invoice || {};
  const reference = [
    invoice.invoice_number ? `#${invoice.invoice_number}` : null,
    invoice.vendor_name || null,
  ]
    .filter(Boolean)
    .join(' · ');

  const subtitle = reference || `Job ${job.id.slice(0, 8)}`;

  return `
    <div class="doc-cell">
      <span class="doc-cell__icon">${icon('file', 'icon icon--sm')}</span>
      <span class="doc-cell__copy">
        <span class="doc-cell__name" title="${esc(job.filename)}">${esc(job.filename)}</span>
        <span class="doc-cell__sub" title="${esc(subtitle)}">${esc(subtitle)}</span>
      </span>
    </div>`;
}

function statusCell(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  const isActive = job.status === 'QUEUED' || job.status === 'PROCESSING';
  const meta = statusMeta(job.status);

  let progressBar = '';
  if (isActive) {
    progressBar = `
      <div class="row-progress" title="${esc(job.message || meta.label)}">
        <div class="progress"><span class="progress__bar" style="width:${progress}%"></span></div>
        <span class="row-progress__label">${job.status === 'QUEUED' ? 'queued' : `${progress}%`}</span>
      </div>`;
  } else if (job.status === 'FAILED') {
    progressBar = `<div class="doc-cell__sub" title="${esc(job.error?.message || job.message)}">${esc(
      job.error?.message || 'Extraction failed'
    ).slice(0, 52)}</div>`;
  }

  return `<div>${statusBadge(job.status)}${progressBar}</div>`;
}

function amountCell(job) {
  if (job.status !== 'COMPLETED' || !job.result?.invoice) {
    return '<td class="num amount-cell amount-cell--muted">—</td>';
  }
  const invoice = job.result.invoice;
  const value = formatMoney(invoice.total_amount, invoice.currency);
  const muted = invoice.total_amount === null || invoice.total_amount === undefined;
  return `<td class="num amount-cell${muted ? ' amount-cell--muted' : ''}">${esc(value)}</td>`;
}

function sourceCell(job) {
  if (!job.result) {
    return `
      <td>
        <div class="source-cell">
          <span class="source-cell__name">${job.status === 'FAILED' ? 'No result' : 'Pending'}</span>
          <span class="source-cell__sub">${esc(job.stage || '—')}</span>
        </div>
      </td>`;
  }

  const model = job.result.model_used || 'Gemini';
  return `
    <td>
      <div class="source-cell">
        <span class="source-cell__name">${esc(model)}</span>
        <span class="source-cell__sub">${esc(job.result.pdf_type || '—')} · ${esc(
          job.result.approach || ''
        )}</span>
      </div>
    </td>`;
}

function actionsCell(job) {
  const download =
    job.status === 'COMPLETED'
      ? `<a class="icon-btn" href="/api/v1/invoices/${encodeURIComponent(job.id)}/json"
             title="Download JSON" aria-label="Download JSON for ${esc(job.filename)}">
           ${icon('download')}
         </a>`
      : '';

  return `
    <td>
      <div class="row-actions">
        <button class="icon-btn" type="button" data-action="view" data-id="${esc(job.id)}"
                title="View details" aria-label="View details for ${esc(job.filename)}">
          ${icon('eye')}
        </button>
        ${download}
        <button class="icon-btn icon-btn--danger" type="button" data-action="delete"
                data-id="${esc(job.id)}" title="Delete"
                aria-label="Delete ${esc(job.filename)}">
          ${icon('trash')}
        </button>
      </div>
    </td>`;
}

export function renderJobs(visibleJobs, { total = 0, filtersActive = false } = {}) {
  const body = document.getElementById('jobs-body');
  const empty = document.getElementById('jobs-empty');
  const summary = document.getElementById('table-summary');

  if (!body || !empty) return;

  body.innerHTML = visibleJobs
    .map((job) => {
      const created = formatDateTime(job.created_at);
      return `
        <tr data-job-id="${esc(job.id)}">
          <td>${documentCell(job)}</td>
          <td>${statusCell(job)}</td>
          ${amountCell(job)}
          ${sourceCell(job)}
          <td class="time-cell" title="${esc(created)}">${esc(relativeTime(job.created_at))}</td>
          ${actionsCell(job)}
        </tr>`;
    })
    .join('');

  const isEmpty = visibleJobs.length === 0;
  empty.hidden = !isEmpty;

  if (isEmpty) {
    const title = document.getElementById('empty-title');
    const text = document.getElementById('empty-text');
    if (filtersActive) {
      if (title) title.textContent = 'No matching invoices';
      if (text) text.textContent = 'Try a different search term or clear the status filter.';
    } else {
      if (title) title.textContent = 'No invoices yet';
      if (text)
        text.textContent =
          'Upload your first PDF invoice and the structured result will appear here.';
    }
  }

  if (summary) {
    const noun = total === 1 ? 'document' : 'documents';
    summary.textContent = filtersActive
      ? `${visibleJobs.length} of ${total} ${noun}`
      : `${total} ${noun}`;
  }
}

/* -------------------------------------------------------------------------- */
/* Detail modal                                                               */
/* -------------------------------------------------------------------------- */

let currentJobId = null;

function field({ label, value, wide = false, span2 = false, accent = false, strong = false, muted = false }) {
  const classes = ['field'];
  if (wide) classes.push('field--wide');
  if (span2) classes.push('field--span2');
  if (accent) classes.push('field--accent');

  const valueClass = [
    'field__value',
    strong ? 'field__value--strong' : '',
    muted ? 'field__value--muted' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const display =
    value === null || value === undefined || value === ''
      ? '<span class="field__value field__value--muted">Not found</span>'
      : `<span class="${valueClass}">${esc(value)}</span>`;

  return `
    <div class="${classes.join(' ')}">
      <span class="field__label">${esc(label)}</span>
      ${display}
    </div>`;
}

function renderDetail(job) {
  const invoice = job.result?.invoice || null;
  const meta = statusMeta(job.status);

  const statusNode = document.getElementById('detail-status');
  if (statusNode) {
    statusNode.className = `status-badge ${meta.cls}`;
    statusNode.innerHTML = `<span class="dot ${meta.dot}"></span>${esc(meta.label)}`;
  }

  setText('detail-filename', job.filename);

  const metaText = [
    job.result?.pdf_type ? `${job.result.pdf_type} PDF` : null,
    job.result?.approach ? job.result.approach.replace(/\+/g, ' + ') : null,
    job.result ? `completed ${relativeTime(job.updated_at)}` : relativeTime(job.created_at),
  ]
    .filter(Boolean)
    .join(' · ');
  setText('detail-meta', metaText || 'Awaiting extraction');

  const modelChip = document.getElementById('detail-model');
  if (modelChip) {
    modelChip.textContent = job.result?.model_used || '—';
    modelChip.hidden = !job.result?.model_used;
  }

  const alert = document.getElementById('detail-alert');
  if (alert) {
    const showError = job.status === 'FAILED';
    alert.hidden = !showError;
    alert.textContent = showError
      ? job.error?.message || job.message || 'Extraction failed.'
      : '';
  }

  const fieldsNode = document.getElementById('detail-fields');
  if (fieldsNode) {
    if (!invoice) {
      fieldsNode.innerHTML = `
        <div class="field field--wide">
          <span class="field__label">Status</span>
          <span class="field__value field__value--muted">${esc(
            job.status === 'FAILED' ? 'Extraction failed — no invoice data.' : 'Extraction in progress…'
          )}</span>
        </div>`;
    } else {
      const money = (value) => (value === null || value === undefined ? null : formatMoney(value, invoice.currency));
      fieldsNode.innerHTML = [
        field({ label: 'Invoice number', value: invoice.invoice_number }),
        field({ label: 'Invoice date', value: formatDate(invoice.invoice_date) }),
        field({ label: 'Payment due', value: formatDate(invoice.payment_due_date) }),
        field({ label: 'Vendor', value: invoice.vendor_name }),
        field({ label: 'GST / Tax ID', value: invoice.gst_number }),
        field({ label: 'Currency', value: invoice.currency }),
        field({ label: 'Vendor address', value: invoice.vendor_address, wide: true }),
        field({ label: 'Customer', value: invoice.customer_name }),
        field({ label: 'Customer address', value: invoice.customer_address, span2: true }),
        field({ label: 'Subtotal', value: money(invoice.subtotal) }),
        field({ label: 'Tax', value: money(invoice.tax_amount) }),
        field({ label: 'Total', value: money(invoice.total_amount), accent: true, strong: true }),
      ].join('');
    }
  }

  const items = invoice?.line_items || [];
  const itemsBody = document.getElementById('line-items-body');
  const itemsEmpty = document.getElementById('line-items-empty');
  const itemsCount = document.getElementById('line-item-count');

  if (itemsCount) {
    itemsCount.textContent = `${items.length} ${items.length === 1 ? 'item' : 'items'}`;
  }

  if (itemsBody) {
    itemsBody.innerHTML = items
      .map(
        (item, index) => `
          <tr>
            <td class="num">${index + 1}</td>
            <td class="item-desc">${item.description ? esc(item.description) : '<span class="field__value--muted">—</span>'}</td>
            <td class="num">${esc(formatQty(item.quantity))}</td>
            <td class="num">${esc(formatMoney(item.unit_price, invoice?.currency))}</td>
            <td class="num amount-cell">${esc(formatMoney(item.amount, invoice?.currency))}</td>
          </tr>`
      )
      .join('');
  }

  if (itemsEmpty) itemsEmpty.hidden = items.length > 0;

  const warnings = job.result?.warnings || [];
  const warningsSection = document.getElementById('detail-warnings-section');
  const warningsList = document.getElementById('detail-warnings');
  if (warningsSection && warningsList) {
    warningsSection.hidden = warnings.length === 0;
    warningsList.innerHTML = warnings.map((warning) => `<li>${esc(warning)}</li>`).join('');
  }

  const timingsNode = document.getElementById('detail-timings');
  if (timingsNode) {
    if (!job.result) {
      timingsNode.innerHTML = '<span class="timing-chip">Waiting for the first result…</span>';
    } else {
      const { timings = {}, tokens = {}, raw_text_chars: rawChars = 0 } = job.result;
      const labels = [
        ['analysis', 'PDF analysis'],
        ['text_extraction', 'Text layer'],
        ['ocr', 'OCR'],
        ['vision', 'Vision'],
        ['llm', 'Gemini'],
        ['total', 'Total'],
      ];
      const chips = labels
        .filter(([key]) => typeof timings[key] === 'number')
        .map(
          ([key, label]) =>
            `<span class="timing-chip">${label} <strong>${Number(timings[key]).toFixed(2)}s</strong></span>`
        );

      if (rawChars) {
        chips.push(
          `<span class="timing-chip">Source text <strong>${Number(rawChars).toLocaleString()} chars</strong></span>`
        );
      }
      if (tokens.total) {
        chips.push(
          `<span class="timing-chip">Tokens <strong>${Number(tokens.total).toLocaleString()}</strong></span>`
        );
      }
      timingsNode.innerHTML = chips.join('') || '<span class="timing-chip">No timing data</span>';
    }
  }

  const jsonNode = document.getElementById('detail-json');
  if (jsonNode) {
    jsonNode.textContent = job.result
      ? JSON.stringify(job.result, null, 2)
      : JSON.stringify(
          {
            job_id: job.id,
            filename: job.filename,
            status: job.status,
            stage: job.stage,
            progress: job.progress,
            message: job.message,
          },
          null,
          2
        );
  }

  const download = document.getElementById('detail-download');
  if (download) {
    download.href = `/api/v1/invoices/${encodeURIComponent(job.id)}/json`;
    download.hidden = job.status !== 'COMPLETED';
  }

  const pdfLink = document.getElementById('detail-open-pdf');
  if (pdfLink) {
    pdfLink.href = `/api/v1/invoices/${encodeURIComponent(job.id)}/document`;
  }
}

export function openDetail(job) {
  currentJobId = job.id;
  renderDetail(job);

  const modal = document.getElementById('detail-modal');
  if (!modal) return;
  modal.setAttribute('aria-hidden', 'false');
  document.body.classList.add('no-scroll');

  const panel = modal.querySelector('.modal__body');
  if (panel) panel.scrollTop = 0;

  const closeButton = modal.querySelector('[data-close-modal]');
  if (closeButton) closeButton.focus({ preventScroll: true });
}

export function closeDetail() {
  const modal = document.getElementById('detail-modal');
  if (!modal) return;
  modal.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('no-scroll');
  currentJobId = null;
}

export function syncDetail(job) {
  if (!currentJobId || !job || job.id !== currentJobId) return;
  renderDetail(job);
}

export function isDetailOpen() {
  const modal = document.getElementById('detail-modal');
  return Boolean(modal && modal.getAttribute('aria-hidden') === 'false');
}

/* -------------------------------------------------------------------------- */
/* Toasts                                                                     */
/* -------------------------------------------------------------------------- */

const TOAST_ICONS = {
  success: 'check-circle',
  error: 'alert',
  info: 'sparkles',
};

export function toast({ type = 'info', title, message = '', timeout = 4600 }) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;

  const node = document.createElement('div');
  node.className = `toast toast--${type}`;
  node.innerHTML = `
    <span class="toast__icon">${icon(TOAST_ICONS[type] || 'sparkles', 'icon icon--sm')}</span>
    <div class="toast__copy">
      <p class="toast__title">${esc(title)}</p>
      ${message ? `<p class="toast__message">${esc(message)}</p>` : ''}
    </div>
    <button class="toast__close" type="button" aria-label="Dismiss notification">
      ${icon('x', 'icon icon--xs')}
    </button>`;

  const remove = () => {
    if (!node.isConnected) return;
    node.classList.add('is-leaving');
    window.setTimeout(() => node.remove(), 220);
  };

  node.querySelector('.toast__close')?.addEventListener('click', remove);
  stack.appendChild(node);

  if (timeout) window.setTimeout(remove, timeout);
}
