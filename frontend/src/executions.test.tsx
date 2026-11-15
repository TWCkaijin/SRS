import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, post } from './api';
import { ConsoleContext, type ConsoleContextValue } from './context';
import { ExecutionsPage } from './pages/Executions';
import type { Execution } from './types';

vi.mock('./api', () => ({ API_BASE: '/api/v1', api: vi.fn(), post: vi.fn(), errorMessage: (error: unknown) => String(error) }));
const execution: Execution = { id: 'run-unknown', plan_id: 'plan-reviewed', status: 'unknown', mode: 'demo', created_at: '2026-11-07T00:00:00Z', steps: [], log_complete: false };
let allowed = true;
const value: ConsoleContextValue = { session: { username: 'test', csrf_token: 'fixture', permissions: [], mode: 'demo' }, data: { nodes: [], projects: [], drafts: [], plans: [], executions: [], schedules: [], jobs: [] }, refresh: vi.fn(async () => {}), navigate: vi.fn(), notify: vi.fn(), can: () => allowed };
function mount(supported: boolean) {
  vi.mocked(api).mockImplementation(async path => path.includes('/logs?') ? { items: [], next_cursor: 0 } : { ...execution, reconciliation_supported: supported });
  return render(<ConsoleContext.Provider value={value}><ExecutionsPage id={execution.id} /></ConsoleContext.Provider>);
}
describe('unknown execution reconciliation', () => {
  beforeEach(() => { allowed = true; vi.clearAllMocks(); });
  it('requires supported durable reconciliation before enabling the action', async () => {
    mount(false);
    expect(await screen.findByRole('button', { name: '重新對帳' })).toBeDisabled();
    expect(screen.getByText(/請另建預檢計畫取得新的觀測/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it('reconciles the selected execution without creating a new plan', async () => {
    vi.mocked(post).mockResolvedValue({ ...execution, status: 'queued' });
    mount(true);
    await userEvent.click(await screen.findByRole('button', { name: '重新對帳' }));
    expect(post).toHaveBeenCalledExactlyOnceWith('/executions/run-unknown/reconcile');
    expect(value.refresh).toHaveBeenCalledOnce();
  });
  it('does not expose reconciliation without the capability', async () => {
    allowed = false; mount(true);
    await screen.findByRole('heading', { name: '執行詳情' });
    expect(screen.queryByRole('button', { name: '重新對帳' })).not.toBeInTheDocument();
  });
});
