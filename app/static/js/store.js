/**
 * Minimal observable store for jobs. Only notifies subscribers when
 * something meaningful changed, so a background poll does not cause the
 * table to re-render (and lose hover/focus) every few seconds.
 */

import { listJobs } from './api.js';

const state = {
  jobs: [],
  loaded: false,
  lastError: null,
};

const listeners = new Set();
let lastSignature = '';

export function getState() {
  return state;
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit() {
  for (const listener of listeners) {
    try {
      listener(state);
    } catch (error) {
      console.error('Store listener failed', error);
    }
  }
}

function signatureOf(jobs) {
  return JSON.stringify(
    jobs.map((job) => [
      job.id,
      job.status,
      job.progress,
      job.stage,
      job.message,
      job.updated_at,
    ])
  );
}

function sortJobs(jobs) {
  return [...jobs].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );
}

export function setJobs(jobs, { force = false } = {}) {
  const next = sortJobs(Array.isArray(jobs) ? jobs : []);
  const signature = signatureOf(next);
  const changed = force || signature !== lastSignature || !state.loaded;

  state.jobs = next;
  state.loaded = true;
  state.lastError = null;
  lastSignature = signature;

  if (changed) emit();
}

export async function refresh({ force = false } = {}) {
  try {
    const payload = await listJobs();
    setJobs(payload?.jobs ?? [], { force });
    return state.jobs;
  } catch (error) {
    state.lastError = error;
    throw error;
  }
}

export function upsert(job) {
  if (!job || !job.id) return;
  const existing = state.jobs.findIndex((item) => item.id === job.id);
  const next = [...state.jobs];

  if (existing >= 0) {
    next[existing] = { ...next[existing], ...job };
  } else {
    next.push(job);
  }

  setJobs(next, { force: true });
}

export function patch(jobId, changes) {
  const index = state.jobs.findIndex((job) => job.id === jobId);
  if (index < 0) return;

  const next = [...state.jobs];
  next[index] = { ...next[index], ...changes };
  setJobs(next, { force: true });
}

export function remove(jobId) {
  setJobs(
    state.jobs.filter((job) => job.id !== jobId),
    { force: true }
  );
}

export function find(jobId) {
  return state.jobs.find((job) => job.id === jobId) || null;
}
