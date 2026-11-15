import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from './api';
import { ConsoleContext } from './context';
import { NodesPage } from './pages/Nodes';
import type { MonitorState, Node } from './types';

function node(id: string, enabled = true): Node {
  return { id, alias: `AI_Lab_${id}`, display_name: id, enabled, hostname: 'host', ssh_username: 'admin',
    host_key_sha256: null, inventory_revision: 1, observed_at: null, hardware: {},
    snapshot: { status: 'online', source: 'demo', collected_at: new Date().toISOString(), cpu_percent: 99 } };
}
const empty: MonitorState = { active: false, host_ids: [], results: {}, generation: '', interval_seconds: 10 };
function mount() {
  return render(<ConsoleContext.Provider value={{ session: { username: 'admin', csrf_token: 'c', permissions: ['read', 'monitor.manage'], mode: 'demo' }, data: { nodes: [node('225'), node('226', false), node('250')], projects: [], drafts: [], plans: [], executions: [], schedules: [], jobs: [] }, can: () => true, refresh: vi.fn(), navigate: vi.fn(), notify: vi.fn() }}><NodesPage /></ConsoleContext.Provider>);
}

afterEach(() => vi.restoreAllMocks());
describe('explicit real host monitoring', () => {
  it('treats every registered host uniformly and starts/stops with the exact selection; never shows demo readings', async () => {
    vi.spyOn(api, 'api').mockResolvedValue(empty);
    const post = vi.spyOn(api, 'post').mockImplementation(async path => path === '/monitor'
      ? { ...empty, active: true, generation: 'g1', host_ids: ['225', '226', '250'], expires_at: new Date(Date.now() + 45000).toISOString() }
      : { ...empty, generation: 'g1' });
    mount();
    await waitFor(() => expect(api.api).toHaveBeenCalledWith('/monitor'));
    expect(screen.queryByText('99%')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '開始實際監控' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: '監控 AI_Lab_226' })).toBeEnabled();
    await userEvent.click(screen.getByRole('checkbox', { name: '全選主機' }));
    await userEvent.click(screen.getByRole('button', { name: '開始實際監控' }));
    expect(post).toHaveBeenCalledWith('/monitor', { host_ids: ['225', '226', '250'], interval_seconds: 2 });
    expect(screen.getByText('監控中 · 3 台')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: '監控 AI_Lab_226' }));
    expect(post).toHaveBeenLastCalledWith('/monitor', { host_ids: ['225', '250'], interval_seconds: 2 });
    await userEvent.click(screen.getByRole('button', { name: '停止監控' }));
    expect(post).toHaveBeenCalledWith('/monitor/stop', { generation: 'g1' });
    expect(screen.queryByRole('button', { name: '停止監控' })).not.toBeInTheDocument();
  });
  it('shows per-host failures and sends a stop request when leaving the page', async () => {
    const state = { ...empty, active: true, generation: 'g2', host_ids: ['225'], expires_at: new Date(Date.now() + 45000).toISOString(), results: { '225': { status: 'error', error: 'SSH 登入失敗', checked_at: new Date().toISOString(), snapshot: null } } };
    const request = vi.spyOn(api, 'api').mockResolvedValue(state);
    const view = mount();
    await screen.findByText(/SSH 登入失敗/);
    expect(screen.queryByText('99%')).not.toBeInTheDocument();
    act(() => view.unmount());
    expect(request).toHaveBeenCalledWith('/monitor/stop', expect.objectContaining({ method: 'POST', body: JSON.stringify({ generation: 'g2' }), keepalive: true }));
  });
});

it('uses green for fresh readings, red for failures, and gray for inactive hosts', async () => {
  const state = { ...empty, active: true, generation: 'colors', host_ids: ['225', '226'], expires_at: new Date(Date.now() + 45000).toISOString(), results: {
    '225': { status: 'online', error: null, checked_at: new Date().toISOString(), snapshot: { source: 'remote', status: 'online', collected_at: new Date().toISOString(), cpu_percent: 12 } },
    '226': { status: 'error', error: '連線失敗', checked_at: new Date().toISOString(), snapshot: null },
    '250': { status: 'error', error: '舊錯誤', checked_at: new Date().toISOString(), snapshot: null },
  } };
  vi.spyOn(api, 'api').mockResolvedValue(state);
  const view = mount();
  await screen.findByText('已連線');
  expect(view.container.querySelector('[data-host-id="225"]')).toHaveAttribute('data-status', 'live');
  expect(view.container.querySelector('[data-host-id="226"]')).toHaveAttribute('data-status', 'error');
  expect(view.container.querySelector('[data-host-id="250"]')).toHaveAttribute('data-status', 'idle');
  expect(screen.queryByText('舊錯誤')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: '實際監控' })).not.toBeInTheDocument();
});

it('deletes a host with the displayed revisions and refreshes the list', async () => {
  const request = vi.spyOn(api, 'api').mockResolvedValue(empty);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mount();
  await waitFor(() => expect(request).toHaveBeenCalledWith('/monitor'));
  await userEvent.click(screen.getByRole('button', { name: '刪除主機：AI_Lab_226' }));
  await waitFor(() => expect(request).toHaveBeenCalledWith('/ssh-hosts/226', { method: 'DELETE', body: JSON.stringify({ inventory_revision: 1, revision: null }) }));
});

it('shows ping only for monitored hosts and hides it after stopping', async () => {
  const ping = {status:'ok' as const,ms:1.25,checked_at:new Date().toISOString()};
  const state = {...empty,active:true,generation:'ping',host_ids:['225'],results:{
    '225':{status:'error' as const,error:'SSH failed',snapshot:null,checked_at:ping.checked_at,ping},
    '250':{status:'error' as const,error:null,snapshot:null,checked_at:ping.checked_at,ping},
  }};
  vi.spyOn(api,'api').mockResolvedValue(state);
  vi.spyOn(api,'post').mockResolvedValue(empty);
  mount();
  expect(await screen.findByText('Ping 1.3 ms')).toBeVisible();
  expect(screen.getAllByText('Ping 1.3 ms')).toHaveLength(1);
  await userEvent.click(screen.getByRole('button',{name:'停止監控'}));
  expect(screen.queryByText('Ping 1.3 ms')).not.toBeInTheDocument();
});
