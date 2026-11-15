import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import * as api from './api';
import { ConsoleContext } from './context';
import { SSHHostForm, HostProfile } from './pages/SSHHosts';
import type { Node, SSHHost } from './types';

const context = { session: { username: 'admin', csrf_token: 'c', permissions: ['hosts.manage'], mode: 'demo' as const }, data: { nodes: [], projects: [], drafts: [], plans: [], executions: [], schedules: [], jobs: [] }, can: () => true, refresh: vi.fn().mockResolvedValue(undefined), notify: vi.fn(), navigate: vi.fn() };
afterEach(() => vi.restoreAllMocks());
it('registers a new SSH host from the browser with its password', async () => {
  const request = vi.spyOn(api, 'post').mockResolvedValue({});
  const close = vi.fn();
  render(<ConsoleContext.Provider value={context}><SSHHostForm node={null} onClose={close} /></ConsoleContext.Provider>);
  await userEvent.type(screen.getByLabelText('顯示名稱'), 'My workstation');
  await userEvent.type(screen.getByLabelText('主機 IP／名稱'), '192.0.2.2');
  await userEvent.type(screen.getByLabelText('SSH 帳號'), 'observer');
  await userEvent.type(screen.getByLabelText('SSH 密碼'), 'fixture-password');
  await userEvent.click(screen.getByRole('button', { name: '儲存並連線' }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(request).toHaveBeenCalledWith('/ssh-hosts', expect.objectContaining({ node_id: null, hostname: '192.0.2.2', username: 'observer', password: 'fixture-password', port: 22 }));
  expect(screen.getByLabelText('SSH 密碼')).toHaveValue('');
});
it('retains an existing encrypted password when the edit form is blank', async () => {
  const request = vi.spyOn(api, 'post').mockResolvedValue({});
  const host = { node_id: '226', display_name: '226', hostname: 'host', port: 22, username: 'user', revision: 3, password_configured: true } as SSHHost;
  render(<ConsoleContext.Provider value={context}><SSHHostForm node={{ id: '226', alias: '226', ssh_connection: host } as Node} onClose={vi.fn()} /></ConsoleContext.Provider>);
  expect(screen.getByLabelText('SSH 密碼')).toHaveValue('');
  await userEvent.click(screen.getByRole('button', { name: '儲存並連線' }));
  expect(request).toHaveBeenCalledWith('/ssh-hosts', expect.objectContaining({ node_id: '226', revision: 3, password: null }));
});
it('requires fingerprint acknowledgement before trusting a new SSH key', async () => {
  const request = vi.spyOn(api, 'post').mockResolvedValue({});
  const fingerprint = 'SHA256:' + 'A'.repeat(43);
  const host = { node_id: 'new-host', hostname: 'host', port: 22, revision: 1, status: 'awaiting_trust', pending_host_key_sha256: fingerprint, profile: null } as SSHHost;
  render(<ConsoleContext.Provider value={context}><HostProfile host={host} /></ConsoleContext.Provider>);
  await userEvent.click(screen.getByRole('button', { name: '核對 SSH 指紋' }));
  expect(screen.getByRole('button', { name: '確認並建立 profile' })).toBeDisabled();
  await userEvent.click(screen.getByRole('checkbox', { name: '我已核對此主機的 SSH 指紋' }));
  await userEvent.click(screen.getByRole('button', { name: '確認並建立 profile' }));
  expect(request).toHaveBeenCalledWith('/ssh-hosts/new-host/trust', { revision: 1, fingerprint });
});
