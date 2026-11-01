/**
 * Dashboard controller: upload flow, filters, live job updates and the
 * invoice detail dialog.
 */

import * as api from './api.js';
import * as store from './store.js';
import * as ui from './render.js';
import { watchJob } from './realtime.js';

const $ = (id) => document.getElementById(id);

const config = readConfig();
const filters = { search: '', status: 'ALL' };
const streams = new Map();

let staged = [];
let uploading = false;
let openJobId = null;
let networkWarningShown = false;

function readConfig() {
  const node = $('app-config');
  if (!node) return {};
  try {
    return JSON.parse(node.textContent || '{}');
  } catch {
    return {};
  }
}

/* -------------------------------------------------------------------------- */
/* Bootstrap                                                                  */
/* -------------------------------------------------------------------------- */

function init() {
  bindSidebar();
  bindUpload();
  bindFilters();
  bindTableActions();
  bindModal();
  bindNavigation();

  store.subscribe(() => {
    render();
  });

  render();
  loadInitialData();

  window.setInterval(pollJobs, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollJobs();
  });

  if (config.geminiConfigured === false) {
    ui.toast({
      type: 'error',
      title: 'Gemini API key missing',
      message: 'Add GEMINI_API_KEY to your .env file, then restart the server.',
      timeout: 9000,
    });
  }

  if (config.tesseractAvailable === false) {
    ui.toast({
      type: 'info',
      title: 'Tesseract not detected',
      message: 'Digital PDFs still work; scanned PDFs will use the Gemini Vision fallback.',
      timeout: 9000,
    });
  }
}

async function loadInitialData() {
  try {
    await store.refresh({ force: true });
  } catch (error) {
    showNetworkError(error);
  }
}

async function pollJobs() {
  if (document.hidden) return;
  try {
    await store.refresh();
    networkWarningShown = false;
  } catch (error) {
    showNetworkError(error);
  }
}

function showNetworkError(error) {
  if (networkWarningShown) return;
  networkWarningShown = true;
  ui.toast({
    type: 'error',
    title: 'Connection problem',
    message: error?.message || 'Could not reach the server.',
    timeout: 7000,
  });
}

/* -------------------------------------------------------------------------- */
/* Render                                                                     */
/* -------------------------------------------------------------------------- */

function filterJobs(jobs) {
  const needle = filters.search.trim().toLowerCase();

  return jobs.filter((job) => {
    if (filters.status !== 'ALL' && job.status !== filters.status) return false;

    if (needle) {
      const invoice = job.result?.invoice || {};
      const haystack = [
        job.filename,
        job.id,
        invoice.invoice_number,
        invoice.vendor_name,
        invoice.customer_name,
        invoice.gst_number,
      ];
      return haystack.some((value) => value && String(value).toLowerCase().includes(needle));
    }

    return true;
  });
}

function render() {
  const { jobs } = store.getState();
  const visible = filterJobs(jobs);
  const filtersActive = filters.status !== 'ALL' || filters.search.trim() !== '';

  ui.renderStats(jobs);
  ui.renderJobs(visible, { total: jobs.length, filtersActive });

  syncStreams(jobs);

  if (openJobId) {
    const job = jobs.find((item) => item.id === openJobId);
    if (job) {
      ui.syncDetail(job);
    } else {
      closeModal();
    }
  }
}

/* -------------------------------------------------------------------------- */
/* Live updates                                                               */
/* -------------------------------------------------------------------------- */

function syncStreams(jobs) {
  const activeIds = new Set(
    jobs
      .filter((job) => job.status === 'QUEUED' || job.status === 'PROCESSING')
      .map((job) => job.id)
  );

  for (const [jobId, handle] of streams) {
    if (!activeIds.has(jobId)) {
      handle.close();
      streams.delete(jobId);
    }
  }

  for (const jobId of activeIds) {
    if (streams.has(jobId)) continue;
    const handle = watchJob(jobId, (event) => handleJobEvent(jobId, event));
    streams.set(jobId, handle);
  }
}

function handleJobEvent(jobId, event) {
  if (event?.data?.job) {
    store.upsert(event.data.job);
  } else if (event?.type === 'progress') {
    store.patch(jobId, {
      status: event.status,
      stage: event.stage,
      progress: event.progress,
      message: event.message,
      updated_at: new Date().toISOString(),
    });
  }

  if (event.type === 'completed') {
    const job = store.find(jobId);
    const invoice = job?.result?.invoice;
    const reference = invoice?.invoice_number ? `Invoice #${invoice.invoice_number}` : job?.filename;
    ui.toast({
      type: 'success',
      title: 'Extraction complete',
      message: `${reference || 'Invoice'} is ready to review.`,
    });
    closeStream(jobId);
  }

  if (event.type === 'failed') {
    const job = store.find(jobId);
    ui.toast({
      type: 'error',
      title: 'Extraction failed',
      message: job?.error?.message || event.message || job?.filename || 'Please try again.',
    });
    closeStream(jobId);
  }
}

function closeStream(jobId) {
  const handle = streams.get(jobId);
  if (handle) {
    handle.close();
    streams.delete(jobId);
  }
}

/* -------------------------------------------------------------------------- */
/* Upload                                                                     */
/* -------------------------------------------------------------------------- */

function bindUpload() {
  const zone = $('dropzone');
  const input = $('file-input');
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());
  zone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      input.click();
    }
  });

  input.addEventListener('change', () => {
    addFiles(input.files);
    input.value = '';
  });

  ['dragenter', 'dragover'].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      event.stopPropagation();
      zone.classList.add('is-dragover');
    });
  });

  ['dragleave', 'dragend'].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove('is-dragover');
    });
  });

  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    event.stopPropagation();
    zone.classList.remove('is-dragover');
    addFiles(event.dataTransfer?.files);
  });

  // Prevent the browser from opening a PDF when dropped outside the zone.
  ['dragover', 'drop'].forEach((name) => {
    document.addEventListener(name, (event) => {
      if (!zone.contains(event.target)) event.preventDefault();
    });
  });

  $('upload-btn')?.addEventListener('click', startUpload);
  $('clear-files')?.addEventListener('click', () => {
    staged = [];
    renderFiles();
  });

  $('file-list')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-remove]');
    if (!button) return;
    staged.splice(Number(button.dataset.remove), 1);
    renderFiles();
  });

  renderFiles();
}

function addFiles(fileList) {
  const incoming = Array.from(fileList || []);
  const errors = [];
  const maxFiles = Number(config.maxFiles) || 10;
  const maxBytes = (Number(config.maxFileSizeMb) || 15) * 1024 * 1024;

  for (const file of incoming) {
    const looksPdf = /\.pdf$/i.test(file.name) || file.type === 'application/pdf';
    if (!looksPdf) {
      errors.push(`${file.name} is not a PDF.`);
      continue;
    }
    if (file.size > maxBytes) {
      errors.push(`${file.name} exceeds the ${config.maxFileSizeMb} MB limit.`);
      continue;
    }
    if (staged.length >= maxFiles) {
      errors.push(`You can upload up to ${maxFiles} files per batch.`);
      break;
    }
    if (staged.some((item) => item.name === file.name && item.size === file.size)) {
      continue;
    }
    staged.push(file);
  }

  if (errors.length) {
    ui.toast({
      type: 'error',
      title: 'Some files were skipped',
      message: errors.slice(0, 3).join(' '),
    });
  }

  renderFiles();
}

function renderFiles() {
  const list = $('file-list');
  if (!list) return;

  list.innerHTML = staged
    .map(
      (file, index) => `
        <div class="file-row">
          <span class="file-row__icon">${ui.icon('file', 'icon icon--sm')}</span>
          <span class="file-row__copy">
            <span class="file-row__name" title="${ui.esc(file.name)}">${ui.esc(file.name)}</span>
            <span class="file-row__size">${ui.esc(ui.formatBytes(file.size))}</span>
          </span>
          <button class="file-row__remove" type="button" data-remove="${index}"
                  aria-label="Remove ${ui.esc(file.name)}">
            ${ui.icon('x', 'icon icon--xs')}
          </button>
        </div>`
    )
    .join('');

  list.hidden = staged.length === 0;

  const clearButton = $('clear-files');
  if (clearButton) clearButton.hidden = staged.length === 0 || uploading;

  const button = $('upload-btn');
  const label = $('upload-btn-label');
  if (button) button.disabled = staged.length === 0 || uploading;
  if (label) {
    label.textContent =
      staged.length > 1 ? `Extract ${staged.length} invoices` : 'Extract invoice';
  }
}

async function startUpload() {
  if (uploading || staged.length === 0) return;

  uploading = true;
  renderFiles();
  setProgress(0, 'Uploading invoices…');
  $('upload-progress').hidden = false;

  try {
    const payload = await api.uploadFiles(staged, {
      onProgress: (percent) => setProgress(percent, percent >= 100 ? 'Upload complete' : 'Uploading invoices…'),
    });

    const accepted = payload?.jobs?.length ?? 0;
    staged = [];
    renderFiles();
    setProgress(100, 'Upload complete');

    ui.toast({
      type: 'success',
      title: accepted === 1 ? 'Invoice queued' : `${accepted} invoices queued`,
      message: 'Extraction is running — results will appear automatically.',
    });

    await store.refresh({ force: true });
  } catch (error) {
    setProgress(0, 'Upload failed');
    ui.toast({
      type: 'error',
      title: 'Upload failed',
      message: error?.message || 'Please try again.',
      timeout: 7000,
    });
  } finally {
    uploading = false;
    renderFiles();
    window.setTimeout(() => {
      if (!uploading) $('upload-progress').hidden = true;
    }, 900);
  }
}

function setProgress(percent, label) {
  const bar = $('upload-progress-bar');
  const value = $('upload-progress-value');
  const text = $('upload-progress-label');
  const bounded = Math.max(0, Math.min(100, Math.round(percent)));
  if (bar) bar.style.width = `${bounded}%`;
  if (value) value.textContent = `${bounded}%`;
  if (text) text.textContent = label;
}

/* -------------------------------------------------------------------------- */
/* Filters / table actions                                                    */
/* -------------------------------------------------------------------------- */

function bindFilters() {
  const search = $('job-search');
  let timer = null;

  search?.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      filters.search = search.value;
      render();
    }, 140);
  });

  $('status-filter')?.addEventListener('change', (event) => {
    filters.status = event.target.value;
    render();
  });

  $('refresh-jobs')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const svg = button.querySelector('svg');
    svg?.classList.add('spin');
    try {
      await store.refresh({ force: true });
      ui.toast({ type: 'info', title: 'History refreshed' });
    } catch (error) {
      showNetworkError(error);
    } finally {
      window.setTimeout(() => svg?.classList.remove('spin'), 400);
    }
  });
}

function bindTableActions() {
  $('jobs-body')?.addEventListener('click', (event) => {
    const view = event.target.closest('[data-action="view"]');
    if (view) {
      const job = store.find(view.dataset.id);
      if (job) openModal(job);
      return;
    }

    const remove = event.target.closest('[data-action="delete"]');
    if (remove) deleteJob(remove.dataset.id);
  });
}

async function deleteJob(jobId) {
  const job = store.find(jobId);
  if (!job) return;

  const confirmed = window.confirm(
    `Delete the extraction for "${job.filename}"?\n\nThis removes the stored JSON and the uploaded PDF.`
  );
  if (!confirmed) return;

  try {
    await api.deleteJob(jobId);
    store.remove(jobId);
    closeStream(jobId);
    if (openJobId === jobId) closeModal();
    ui.toast({ type: 'info', title: 'Job deleted', message: job.filename });
  } catch (error) {
    ui.toast({
      type: 'error',
      title: 'Could not delete the job',
      message: error?.message || 'Please try again.',
    });
  }
}

/* -------------------------------------------------------------------------- */
/* Detail modal                                                               */
/* -------------------------------------------------------------------------- */

function bindModal() {
  const modal = $('detail-modal');
  if (!modal) return;

  modal.querySelectorAll('[data-close-modal]').forEach((node) => {
    node.addEventListener('click', closeModal);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeModal();
      closeSidebar();
    }
  });
}

function openModal(job) {
  openJobId = job.id;
  ui.openDetail(job);
}

function closeModal() {
  openJobId = null;
  ui.closeDetail();
}

/* -------------------------------------------------------------------------- */
/* Navigation                                                                 */
/* -------------------------------------------------------------------------- */

function bindNavigation() {
  document.querySelectorAll('[data-scroll-to]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = document.getElementById(button.dataset.scrollTo);
      if (!target) return;
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      if (target.id === 'upload') {
        window.setTimeout(() => $('dropzone')?.focus({ preventScroll: true }), 420);
      }
    });
  });

  document.querySelectorAll('.sidebar__nav a[data-nav]').forEach((link) => {
    link.addEventListener('click', () => {
      document
        .querySelectorAll('.sidebar__nav a[data-nav]')
        .forEach((item) => item.classList.toggle('is-active', item === link));
    });
  });
}

function bindSidebar() {
  const sidebar = $('sidebar');
  const scrim = $('sidebar-scrim');
  if (!sidebar || !scrim) return;

  $('sidebar-open')?.addEventListener('click', openSidebar);
  $('sidebar-close')?.addEventListener('click', closeSidebar);
  scrim.addEventListener('click', closeSidebar);

  sidebar.querySelectorAll('.sidebar__nav a').forEach((link) => {
    link.addEventListener('click', () => {
      if (window.matchMedia('(max-width: 1024px)').matches) closeSidebar();
    });
  });
}

function openSidebar() {
  const sidebar = $('sidebar');
  const scrim = $('sidebar-scrim');
  if (!sidebar || !scrim) return;
  sidebar.classList.add('is-open');
  scrim.hidden = false;
  document.body.classList.add('no-scroll');
}

function closeSidebar() {
  const sidebar = $('sidebar');
  const scrim = $('sidebar-scrim');
  if (!sidebar || !scrim) return;
  if (!sidebar.classList.contains('is-open')) return;
  sidebar.classList.remove('is-open');
  scrim.hidden = true;
  if (!ui.isDetailOpen()) document.body.classList.remove('no-scroll');
}

init();
