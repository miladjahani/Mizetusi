/* =============================================================================
   NEXUS · api — one HTTP client for every view.

   Requests carry the session token as a header when the browser refused the
   cookie, time out instead of hanging forever on a dead mobile connection, and
   a 401 is reported once (never as a toast storm from every polling view).
   ========================================================================== */

export class ApiError extends Error {
  constructor(message, { status = 0, detail = '' } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.unauthorized = status === 401;
  }
}

export class ApiClient {
  constructor(session, { timeout = 20000 } = {}) {
    this.session = session;
    this.timeout = timeout;
  }

  async request(url, { method = 'GET', body, headers = {}, timeout } = {}) {
    const controller = new AbortController();
    const limit = timeout || this.timeout;
    const timer = setTimeout(() => controller.abort(), limit);
    const init = {
      method,
      credentials: 'same-origin',
      signal: controller.signal,
      headers: { ...this.session.headers(), ...headers },
    };
    if (body !== undefined && body !== null && method !== 'GET') {
      init.headers['Content-Type'] = 'application/json';
      init.body = typeof body === 'string' ? body : JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(url, init);
    } catch (error) {
      clearTimeout(timer);
      if (error?.name === 'AbortError') throw new ApiError('پاسخی از سرور نیامد (تایم‌اوت)', { status: 0 });
      throw new ApiError('ارتباط با سرور برقرار نشد', { status: 0 });
    } finally {
      clearTimeout(timer);
    }

    const text = await response.text();
    let payload = null;
    if (text) {
      try { payload = JSON.parse(text); } catch (error) { payload = text; }
    }

    if (response.status === 401) {
      // One report per expiry: the shell shows a re-login overlay and stops
      // polling, so nothing else needs to react to this.
      this.session.markExpired('unauthorized');
      throw new ApiError('نشست شما منقضی شد', { status: 401 });
    }

    if (!response.ok) {
      const detail = payload && typeof payload === 'object' ? (payload.detail || payload.message) : payload;
      const message = typeof detail === 'string' && detail ? detail : `خطای سرور (${response.status})`;
      throw new ApiError(message, { status: response.status, detail: message });
    }

    return payload;
  }

  get(url, options) { return this.request(url, { ...options, method: 'GET' }); }

  post(url, body, options) { return this.request(url, { ...options, method: 'POST', body: body ?? {} }); }

  put(url, body, options) { return this.request(url, { ...options, method: 'PUT', body: body ?? {} }); }

  delete(url, options) { return this.request(url, { ...options, method: 'DELETE' }); }

  /** Multipart-free download helper (JSON backup, worker source…). */
  async download(url, filename) {
    const data = await this.get(url);
    const blob = new Blob([typeof data === 'string' ? data : JSON.stringify(data, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
    return true;
  }
}
