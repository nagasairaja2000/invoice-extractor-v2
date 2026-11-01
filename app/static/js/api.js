/**
 * Thin API client. All network errors are normalised into Error objects
 * with a readable message, an optional code and an optional status.
 */

const ACCEPT_JSON = { Accept: 'application/json' };

function buildError(payload, status) {
  const info = payload && typeof payload === 'object' ? payload.error : null;
  const error = new Error(
    (info && info.message) ||
      (status === 413
        ? 'That file is too large.'
        : status === 415
          ? 'Only PDF files are supported.'
          : `Request failed (HTTP ${status}).`)
  );
  error.code = info?.code || 'REQUEST_FAILED';
  error.details = info?.details;
  error.status = status;
  return error;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { ...(options.body ? {} : ACCEPT_JSON), ...(options.headers || {}) },
    });
  } catch {
    const error = new Error('Cannot reach the server. Check that it is running and try again.');
    error.code = 'NETWORK_ERROR';
    throw error;
  }

  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json().catch(() => null)
    : null;

  if (!response.ok) {
    throw buildError(payload, response.status);
  }

  return payload;
}

export function listJobs({ limit = 200 } = {}) {
  return request(`/api/v1/invoices?limit=${limit}`, { cache: 'no-store' });
}

export function getJob(jobId) {
  return request(`/api/v1/invoices/${encodeURIComponent(jobId)}`);
}

export function deleteJob(jobId) {
  return request(`/api/v1/invoices/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
}

export function getHealth() {
  return request('/api/v1/health');
}

/**
 * Upload with progress reporting (fetch has no upload progress events).
 * @param {File[]} files
 * @param {{ onProgress?: (percent: number) => void }} options
 */
export function uploadFiles(files, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    if (!files.length) {
      reject(new Error('Choose at least one PDF file.'));
      return;
    }

    const form = new FormData();
    files.forEach((file) => form.append('files', file, file.name));

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/v1/invoices');
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.responseType = 'json';
    xhr.timeout = 120000;

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.max(1, Math.min(99, Math.round((event.loaded / event.total) * 100))));
      }
    });

    xhr.addEventListener('load', () => {
      const payload = xhr.response;
      if (xhr.status >= 200 && xhr.status < 300) {
        if (onProgress) onProgress(100);
        resolve(payload);
        return;
      }
      reject(buildError(payload, xhr.status));
    });

    xhr.addEventListener('error', () => {
      reject(new Error('The upload was interrupted. Please try again.'));
    });

    xhr.addEventListener('timeout', () => {
      reject(new Error('The upload timed out. Please try again.'));
    });

    xhr.send(form);
  });
}
