import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, getSession, post, setCsrfToken } from './api';

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(''); });
describe('API session boundary', () => {
  it('uses the server session CSRF token on mutations and includes cookies', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ username: 'researcher', csrf_token: 'review-token', permissions: [], mode: 'demo' }), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    await getSession(); await post('/plans/p1/approve', { content_hash: 'reviewed-hash' });
    const [url, options] = fetcher.mock.calls[1];
    expect(url).toBe('/api/v1/plans/p1/approve');
    expect(options.credentials).toBe('include');
    expect(options.headers.get('X-CSRF-Token')).toBe('review-token');
    expect(JSON.parse(options.body)).toEqual({ content_hash: 'reviewed-hash' });
  });
  it('surfaces a rejected approval instead of inventing a successful response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Plan content changed' }), { status: 409 })));
    await expect(post('/plans/p1/approve', { content_hash: 'old' })).rejects.toMatchObject({ status: 409, message: 'Plan content changed' });
  });
  it('notifies the app when the server rejects an expired session', async () => {
    const listener = vi.fn(); window.addEventListener('srs:unauthorized', listener);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Session expired' }), { status: 401 })));
    await expect(api('/jobs')).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledOnce(); window.removeEventListener('srs:unauthorized', listener);
  });
});
