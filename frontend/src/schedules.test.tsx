import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import * as api from './api';
import { ConsoleContext } from './context';
import { SchedulesPage } from './pages/Schedules';
import type { Schedule } from './types';

afterEach(() => vi.restoreAllMocks());
it('deletes a schedule from the list after confirmation', async () => {
  const request = vi.spyOn(api, 'api').mockResolvedValue(undefined);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const refresh = vi.fn().mockResolvedValue(undefined);
  const item = { id: 's1', title: '夜間觀測', operation: 'host_snapshot', host_ids: ['225'], interval_seconds: 30, expires_at: '2099-01-01T00:00:00Z', status: 'draft', revision: 1 } as Schedule;
  render(<ConsoleContext.Provider value={{ session: { username: 'admin', csrf_token: 'c', permissions: ['schedule.manage'], mode: 'demo' }, data: { nodes: [], schedules: [item], projects: [], drafts: [], plans: [], jobs: [], executions: [] }, can: () => true, refresh, navigate: vi.fn(), notify: vi.fn() }}><SchedulesPage /></ConsoleContext.Provider>);
  await userEvent.click(screen.getByRole('button', { name: '刪除排程：夜間觀測' }));
  await waitFor(() => expect(request).toHaveBeenCalledWith('/schedules/s1', { method: 'DELETE' }));
  expect(refresh).toHaveBeenCalled();
});
