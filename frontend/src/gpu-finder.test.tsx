import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { GpuFinder, observedGpus } from './pages/GpuFinder';
import type { SnapshotGpu } from './types';

const clock = Date.parse('2026-11-16T01:00:00Z');
function host(alias: string, age: number, used: number | null, options: { gpuError?: string; status?: string } = {}) {
  const observedAt = new Date(clock - age).toISOString();
  const gpu: SnapshotGpu = { index: 0, name: 'NVIDIA GPU', memory_total_mb: 48 * 1024, memory_used_mb: used, utilization_percent: 0 };
  return { host_id: alias, alias, latest: {
    collected_at: new Date(clock - 1000).toISOString(),
    result: { status: options.status || 'online', error: null, snapshot: {
      status: 'online', source: 'remote', collected_at: observedAt, gpus: [gpu], gpu_error: options.gpuError,
    } },
  } };
}

it('filters by reported remaining VRAM and keeps older measurements out of the live results', async () => {
  const rows = observedGpus([
    host('Idle but full', 1000, 45 * 1024),
    host('24 GiB free', 1000, 24 * 1024),
    host('32 GiB free', 2000, 16 * 1024),
    host('Old', 60000, 0),
    host('Unknown', 1000, null),
    host('Failed', 1000, 0, { status: 'error' }),
    host('GPU probe failed', 1000, 0, { gpuError: 'unavailable' }),
  ], clock);
  const user = userEvent.setup();
  render(<GpuFinder rows={rows} />);
  const current = screen.getByRole('list', { name: '近期觀測結果' });
  const names = () => within(current).getAllByRole('link').map(link => link.textContent);
  expect(names()).toEqual(['32 GiB free', '24 GiB free']);
  expect(within(current).getAllByRole('time')[0]).toHaveAttribute('datetime', new Date(clock - 2000).toISOString());
  const minimum = screen.getByRole('spinbutton', { name: '最低剩餘 VRAM（GiB）' });
  await user.clear(minimum);
  await user.type(minimum, '3');
  await user.selectOptions(screen.getByLabelText('排序'), 'asc');
  expect(names()).toEqual(['Idle but full', '24 GiB free', '32 GiB free']);
  await user.click(screen.getByRole('checkbox', { name: '顯示舊觀測' }));
  expect(within(screen.getByRole('list', { name: '舊觀測結果' })).getByRole('link', { name: 'Old' })).toBeVisible();
  expect(within(current).queryByRole('link', { name: 'Old' })).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('3 張符合');
  expect(screen.getByText('1 張 GPU 缺少容量或用量回報，無法比較剩餘 VRAM。')).toBeVisible();
});
