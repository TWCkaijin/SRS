import type { Session } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError'; }
}

let csrf = '';
export function setCsrfToken(token: string) { csrf = token; }
export const API_BASE = '/api/v1';

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrf) headers.set('X-CSRF-Token', csrf);
  const response = await fetch(`${API_BASE}${path}`, { ...options, method, headers, credentials: 'include' });
  if (!response.ok) {
    let detail = `請求未完成（${response.status}）`;
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((x: { msg?: string }) => x.msg || '欄位格式不正確').join('；');
    } catch { /* A proxy may return a non-JSON error page. */ }
    if (response.status === 401) window.dispatchEvent(new Event('srs:unauthorized'));
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function post<T>(path: string, body: unknown = {}) { return api<T>(path, { method: 'POST', body: JSON.stringify(body) }); }
export function patch<T>(path: string, body: unknown) { return api<T>(path, { method: 'PATCH', body: JSON.stringify(body) }); }
export async function getSession() {
  const session = await api<Session>('/session');
  setCsrfToken(session.csrf_token);
  return session;
}
export async function list<T>(path: string) { return (await api<{ items: T[] }>(path)).items; }
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '發生未預期的錯誤，請稍後重試。'; }
