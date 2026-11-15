import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import * as api from './api';
import { ConsoleContext, type ConsoleContextValue } from './context';
import { HostAccounts } from './pages/HostAccess';
import type { Node } from './types';

const account = { username: 'student', name: 'Student', uid: 1001, gid: 1001, home: '/home/student', shell: '/bin/bash' };
const plan = { id: 'plan-1', content_hash: 'abc', expires_at: '2099-01-01T00:00:00Z', document: { action: 'update', username: 'student', commands: [['usermod', '-c', 'Researcher', '--', 'student'], ['chpasswd']], change_password: true, impact: '影響範圍', failure_policy: '錯誤停止', runner_script: 'fixed script', host: { hostname: 'fixture.test', username: 'observer' } } };
function mount(can = true) {
  vi.spyOn(api, 'api').mockImplementation(async path => ({ items: path.endsWith('/accounts') ? [account, { ...account, username: 'firstuser', uid: 1000 }, { ...account, username: 'root', uid: 0 }, { ...account, username: 'service', uid: 999 }, { ...account, username: 'nobody', uid: 65534 }] : [] }) as never);
  return render(<ConsoleContext.Provider value={{ can: () => can } as unknown as ConsoleContextValue}><HostAccounts node={{ id: 'host-1', alias: 'Lab', ssh_connection: { username: 'observer', revision: 7 } } as Node} onBusy={vi.fn()} /></ConsoleContext.Provider>);
}
afterEach(() => vi.restoreAllMocks());
it('previews commands before requiring explicit approval and never writes through legacy API', async () => {
  const user = userEvent.setup(); const post = vi.spyOn(api, 'post').mockResolvedValueOnce(plan).mockResolvedValueOnce({ ok: true });
  mount();
  await user.click(await screen.findByRole('button', { name: '管理 student' }));
  await user.clear(screen.getByLabelText('顯示名稱')); await user.type(screen.getByLabelText('顯示名稱'), 'Researcher');
  await user.click(screen.getByLabelText('同時重設密碼（審核時輸入）'));
  await user.click(screen.getByRole('button', { name: '產生操作計畫' }));
  expect(post).toHaveBeenCalledExactlyOnceWith('/ssh-hosts/host-1/account-plans', { revision: 7, action: 'update', username: 'student', name: 'Researcher', change_password: true });
  const approve = screen.getByRole('button', { name: '核准並執行' }); expect(approve).toBeDisabled();
  expect(screen.getByRole('region', { name: '帳號操作審核' })).toHaveFocus();
  await user.type(screen.getByLabelText('帳號新密碼'), 'new-password'); await user.type(screen.getByLabelText('sudo 密碼'), 'sudo-password');
  await user.type(screen.getByLabelText('輸入 student 確認'), 'student');
  expect(approve).toBeDisabled();
  await user.click(screen.getByLabelText('我已審查命令、主機與影響，同意執行此計畫。'));
  await user.click(approve);
  await waitFor(() => expect(post).toHaveBeenCalledWith('/ssh-hosts/host-1/account-plans/plan-1/execute', { content_hash: 'abc', confirmation: 'student', password: 'new-password', sudo_password: 'sudo-password' }));
  expect(await screen.findByText('已執行審核計畫，帳號清單已重新讀取。')).toBeVisible();
  expect(screen.queryByLabelText('帳號新密碼')).not.toBeInTheDocument();
});
it('shows UID 1000 users while hiding system accounts and nobody for read-only identities', async () => {
  mount(false); expect(await screen.findByText('firstuser')).toBeVisible();
  expect(screen.getByText('student')).toBeVisible();
  for (const username of ['root', 'service', 'nobody']) expect(screen.queryByText(username)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新增帳號' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '管理 student' })).not.toBeInTheDocument();
});
