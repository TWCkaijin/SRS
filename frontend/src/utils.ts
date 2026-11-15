import type { Plan, Schedule } from './types';

export const statusLabels: Record<string, string> = {
  monitor_stopped: '監控已停止', stale: '資料已過期',
  draft: '草稿', planned: '待核准', pending: '等待中', awaiting_approval: '待核准', pending_approval: '待核准',
  approved: '已核准', queued: '已排隊', running: '執行中', completed: '已完成', succeeded: '已完成', success: '已完成',
  failed: '失敗', rejected: '已退回', expired: '已到期', invalidated: '已失效',
  stopped: '已停止派送', stopping: '停止派送中', canceled: '已取消', cancelled: '已取消',
  skipped: '已略過', unknown: '待確認', online: '有快照', offline: '離線', disabled: '已停用', enabled: '已啟用', active: '已啟用',
  PENDING: '排隊中', RUNNING: '執行中', COMPLETED: '已完成', FAILED: '失敗', CANCELLED: '已取消', TIMEOUT: '已逾時', NODE_FAIL: '節點失聯',
};
export const operationLabels: Record<string, string> = { preflight: '環境盤點', submit_job: '提交運算工作', cancel_job: '取消工作', collect_results: '收集工作成果', deploy: '部署元件', write_config: '寫入設定', cleanup: '清理已收集成果', host_snapshot: '主機快照', job_status: '工作狀態', job_logs: '工作日誌' };
export const profileLabels: Record<string, string> = { cpu: 'CPU 測試', single: '單卡訓練', ddp: 'DDP 跨機訓練', fsdp2: 'FSDP2 模型分片' };
export function formatDate(value?: string | null) { if (!value) return '尚無紀錄'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Taipei' }).format(date); }
export function shortId(value?: string | null) { return value ? value.slice(0, 8) : '—'; }
export function json(value: unknown) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '—'; }
export function percent(used?: number, total?: number) { return typeof used === 'number' && typeof total === 'number' && total > 0 ? Math.min(100, Math.max(0, used / total * 100)) : null; }
export function formatMemory(mb?: number | null) { return typeof mb === 'number' && Number.isFinite(mb) ? (mb < 1024 ? `${Math.round(mb)} MB` : `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB`) : '—'; }
export function localDateInput(date = new Date()) { const offset = date.getTimezoneOffset() * 60000; return new Date(date.getTime() - offset).toISOString().slice(0, 16); }
export function canApprovePlan(plan: Plan, now = Date.now()) { return ['awaiting_approval', 'pending_approval', 'planned'].includes(plan.status) && !plan.issues?.length && !!plan.content_hash && (!plan.approval_expires_at || new Date(plan.approval_expires_at).getTime() > now); }
export function scheduleExpired(schedule: Schedule, now = Date.now()) { return !schedule.expires_at || new Date(schedule.expires_at).getTime() <= now; }
export function canEnableSchedule(schedule: Schedule, now = Date.now()) { return ['approved', 'disabled', 'enabled', 'active'].includes(schedule.status) && !!schedule.content_hash && !scheduleExpired(schedule, now); }
export function shellPreview(argv: string[]) { return argv.map(arg => /^[a-zA-Z0-9_./:@=+-]+$/.test(arg) ? arg : `'${arg.replaceAll("'", "'\\''")}'`).join(' '); }

export function formatBytes(bytes?: number | null) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes)) return '—';
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes < 1024 ** 4) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${(bytes / 1024 ** 4).toFixed(2)} TB`;
}
export function formatRate(value?: number | null) { return typeof value === 'number' ? `${formatBytes(value)}/s` : '等待採樣'; }
