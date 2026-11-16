import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ModeBanner, PlanDocumentView, ReviewApproval } from './ui';
import { NodeCard } from './pages/Overview';
import { canApprovePlan, canEnableSchedule, scheduleExpired } from './utils';
import type { Node, Plan, Schedule } from './types';

describe('reviewed-content approval', () => {
  it('requires review acknowledgement and resets it when the content hash changes', async () => {
    const user = userEvent.setup(); const approve = vi.fn();
    const { rerender } = render(<ReviewApproval contentHash="hash-v1" eligible onApprove={approve} />);
    const button = screen.getByRole('button', { name: /核准並排入執行/ });
    expect(button).toBeDisabled();
    await user.click(screen.getByRole('checkbox')); expect(button).toBeEnabled();
    await user.click(button); expect(approve).toHaveBeenCalledOnce();
    rerender(<ReviewApproval contentHash="hash-v2" eligible onApprove={approve} />);
    expect(button).toBeDisabled(); expect(screen.getByRole('checkbox')).not.toBeChecked();
  });
  it('does not enable approval while evidence is unresolved', async () => {
    render(<ReviewApproval contentHash="hash" eligible issues={['缺少主機金鑰依據']} onApprove={vi.fn()} />);
    await userEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('button', { name: /核准並排入執行/ })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('缺少主機金鑰依據');
  });
  it('shows the full commands, changes and evidence as inert text', () => {
    render(<PlanDocumentView document={{ steps: [{ id: 's1', host_id: 'n101', title: '受審查的設定變更', kind: 'file', argv: ['python3', 'runner.py', 'argument with spaces'], script: '<script>window.bad = true</script>', file_change: { diff: '-old\n+new' }, reason: '先前盤點提供依據', evidence: { snapshot_id: 'snap-1' }, requires_sudo: true, execution_user: 'srs' }] }} />);
    expect(screen.getByText("python3 runner.py 'argument with spaces'")).toBeInTheDocument();
    expect(screen.getByText('<script>window.bad = true</script>')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByText('先前盤點提供依據', { exact: true })).toBeInTheDocument();
  });
});

describe('resource truth and mode', () => {
  it('keeps a clear demo disclosure', () => { render(<ModeBanner mode="demo" />); expect(screen.getByRole('status')).toHaveTextContent('模擬環境'); expect(screen.getByRole('status')).toHaveTextContent('開始實際監控'); });
  it('does not turn a missing snapshot into zero usage', () => {
    const node: Node = { id: 'n101', alias: 'Test_Node_101', display_name: '101', hostname: 'lab', ssh_username: 'test', enabled: true, inventory_revision: 1, host_key_sha256: null, observed_at: null, hardware: { logical_cpus: 12, memory_gb: 32, gpus: [{ index: 0, name: 'RTX 3060 Ti', memory_mb: 8192, uuid: 'gpu-1', in_scope: true }] }, snapshot: null };
    render(<NodeCard node={node} />);
    expect(screen.getAllByText('未觀測')).toHaveLength(2);
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.getByText('尚無快照 · 需核准盤點計畫')).toBeInTheDocument();
  });
});

describe('authorization state boundaries', () => {
  const now = Date.parse('2026-11-07T00:00:00Z');
  it('does not enable expired or reedited schedules', () => {
    const base = { status: 'approved', content_hash: 'h1', expires_at: '2026-11-07T01:00:00Z' } as Schedule;
    expect(canEnableSchedule(base, now)).toBe(true);
    expect(canEnableSchedule({ ...base, status: 'draft' }, now)).toBe(false);
    expect(canEnableSchedule({ ...base, expires_at: '2026-11-07T00:00:00Z' }, now)).toBe(false);
    expect(scheduleExpired({ ...base, expires_at: '' }, now)).toBe(true);
  });
  it('keeps approved, expired and unresolved plans from a new approval', () => {
    const base = { status: 'awaiting_approval', content_hash: 'h1', issues: [], approval_expires_at: '2026-11-07T01:00:00Z' } as unknown as Plan;
    expect(canApprovePlan(base, now)).toBe(true);
    expect(canApprovePlan({ ...base, status: 'approved' }, now)).toBe(false);
    expect(canApprovePlan({ ...base, issues: ['missing source'] }, now)).toBe(false);
    expect(canApprovePlan({ ...base, approval_expires_at: '2026-11-06T23:59:59Z' }, now)).toBe(false);
  });
});
