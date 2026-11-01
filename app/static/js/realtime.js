/**
 * Per-job WebSocket subscriptions with a bounded reconnect strategy.
 * The dashboard also polls, so a dropped socket degrades gracefully.
 */

const handles = new Map();

export function watchJob(jobId, onEvent) {
  if (handles.has(jobId)) return handles.get(jobId);

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${window.location.host}/ws/jobs/${encodeURIComponent(jobId)}`;

  let socket = null;
  let attempts = 0;
  let terminal = false;
  let closedByUs = false;
  let retryTimer = null;

  const cleanup = () => {
    if (retryTimer) {
      window.clearTimeout(retryTimer);
      retryTimer = null;
    }
    handles.delete(jobId);
  };

  const connect = () => {
    try {
      socket = new WebSocket(url);
    } catch {
      cleanup();
      return;
    }

    socket.addEventListener('open', () => {
      attempts = 0;
    });

    socket.addEventListener('message', (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }

      if (!payload || payload.type === 'ping') return;

      if (payload.type === 'completed' || payload.type === 'failed') {
        terminal = true;
      }

      try {
        onEvent(payload);
      } catch (error) {
        console.error('WebSocket handler failed', error);
      }
    });

    socket.addEventListener('close', () => {
      socket = null;
      if (terminal || closedByUs) {
        cleanup();
        return;
      }
      if (attempts >= 3) {
        cleanup();
        return;
      }
      attempts += 1;
      retryTimer = window.setTimeout(connect, 700 * attempts);
    });

    socket.addEventListener('error', () => {
      // `close` always follows; reconnection is handled there.
    });
  };

  const handle = {
    jobId,
    close() {
      closedByUs = true;
      cleanup();
      if (socket) {
        try {
          socket.close();
        } catch {
          /* already closing */
        }
        socket = null;
      }
    },
  };

  handles.set(jobId, handle);
  connect();
  return handle;
}

export function closeAll() {
  for (const handle of [...handles.values()]) handle.close();
  handles.clear();
}
