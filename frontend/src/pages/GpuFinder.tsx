import { useState } from 'react';
import { Layers3 } from 'lucide-react';
import type { Node } from '../types';
import { Badge, EmptyState, Field, Section } from '../ui';
import { formatDate } from '../utils';

type GpuHost = {
  host_id: string;
  alias: string;
  latest: { collected_at: string; result: { status: string; error: string | null; snapshot: Node['snapshot'] } } | null;
};
type GpuReading = {
  hostId: string;
  alias: string;
  key: string;
  index: number;
  name: string;
  totalMiB: number | null;
  freeMiB: number | null;
  compute: number | null;
  observedAt: string;
  fresh: boolean;
};

export const GPU_FRESH_MS = 60000;

export function isRecentGpuObservation(date: string, clock: number) {
  const age = clock - Date.parse(date);
  return age >= 0 && age < GPU_FRESH_MS;
}

export function observedGpus(hosts: GpuHost[], clock: number): GpuReading[] {
  return hosts.flatMap(host => {
    const sample = host.latest;
    const snapshot = sample?.result.snapshot;
    if (!sample || !snapshot || sample.result.status !== 'online' || sample.result.error || snapshot.gpu_error) return [];
    const observedAt = snapshot.collected_at || sample.collected_at;
    const fresh = isRecentGpuObservation(observedAt, clock);
    return (snapshot.gpus || []).map(gpu => {
      const total = gpu.memory_total_mb;
      const used = gpu.memory_used_mb;
      const hasCapacity = typeof total === 'number' && Number.isFinite(total) && total > 0;
      const hasUsage = typeof used === 'number' && Number.isFinite(used) && used >= 0;
      return {
        hostId: host.host_id, alias: host.alias, key: `${host.host_id}:${gpu.uuid || gpu.index}`,
        index: gpu.index, name: gpu.name.replace(/^NVIDIA\s+/, ''),
        totalMiB: hasCapacity ? total : null,
        freeMiB: hasCapacity && hasUsage ? Math.max(0, total - used) : null,
        compute: typeof gpu.utilization_percent === 'number' && Number.isFinite(gpu.utilization_percent) ? gpu.utilization_percent : null,
        observedAt, fresh,
      };
    });
  });
}

const gib = (mib: number) => `${(mib / 1024).toFixed(2)} GiB`;

function GpuResultList({ items, old = false }: { items: GpuReading[]; old?: boolean }) {
  return <ul className="gpu-finder-results" aria-label={old ? '舊觀測結果' : '近期觀測結果'}>
    {items.map(row => <li key={row.key}>
      <div className="gpu-finder-identity">
        <strong>{row.name}</strong>
        <span><a href={`#/history/${encodeURIComponent(row.hostId)}`}>{row.alias}</a> · GPU {row.index}</span>
      </div>
      <dl>
        <div className="gpu-finder-capacity"><dt>剩餘 VRAM</dt>
          <dd><strong>{row.freeMiB === null ? '—' : gib(row.freeMiB)}</strong>{row.totalMiB !== null && <small>總量 {gib(row.totalMiB)}</small>}</dd>
        </div>
        <div><dt>運算</dt><dd>{row.compute === null ? '—' : `${row.compute}%`}</dd></div>
        <div className="gpu-finder-time"><dt>最後觀測</dt>
          <dd><time dateTime={row.observedAt}>{formatDate(row.observedAt)}</time>{old && <Badge status="unknown" label="舊觀測" />}</dd>
        </div>
      </dl>
    </li>)}
  </ul>;
}

export function GpuFinder({ rows }: { rows: GpuReading[] }) {
  const [minimum, setMinimum] = useState('8');
  const [order, setOrder] = useState('desc');
  const [showOld, setShowOld] = useState(false);
  const threshold = Math.max(0, Number(minimum) || 0) * 1024;
  const numeric = rows.filter((row): row is GpuReading & { freeMiB: number } => row.freeMiB !== null);
  const matches = numeric.filter(row => row.freeMiB >= threshold).sort((a, b) =>
    (order === 'desc' ? b.freeMiB - a.freeMiB : a.freeMiB - b.freeMiB) ||
    a.alias.localeCompare(b.alias) || a.index - b.index);
  const current = matches.filter(row => row.fresh);
  const old = matches.filter(row => !row.fresh);
  const unknown = rows.length - numeric.length;

  return <Section className="gpu-finder" title="找可用 GPU" aside={<span className="muted" role="status">{current.length} 張符合</span>}>
    <p className="gpu-finder-note">只把最近 60 秒內、VRAM 回報完整的 GPU 列入結果。剩餘容量會隨其他工作變動。</p>
    <div className="gpu-finder-controls">
      <Field label="最低剩餘 VRAM（GiB）">
        <input type="number" min={0} step="any" value={minimum}
          onChange={event => setMinimum(event.target.value)}
          onBlur={() => { if (Number(minimum) < 0) setMinimum('0'); }} />
      </Field>
      <Field label="排序">
        <select value={order} onChange={event => setOrder(event.target.value)}>
          <option value="desc">剩餘 VRAM 多到少</option><option value="asc">剩餘 VRAM 少到多</option>
        </select>
      </Field>
      <label className="checkbox-row"><input type="checkbox" checked={showOld} onChange={event => setShowOld(event.target.checked)} />顯示舊觀測</label>
    </div>
    {current.length ? <GpuResultList items={current} /> : <EmptyState icon={<Layers3 size={24} aria-hidden="true" />}
      title={numeric.some(row => row.fresh) ? '沒有 GPU 達到剩餘容量門檻' : '沒有近期 GPU 容量回報'}
      description={numeric.some(row => row.fresh) ? '可調低門檻再比較。' : '開始主機監控並等候觀測後，再回來查看。'} />}
    {showOld && <div className="gpu-finder-old">
      <h3>舊觀測</h3>
      <p className="muted">以下僅供參考，不計入近期符合張數。</p>
      {old.length ? <GpuResultList items={old} old /> : <p className="muted">沒有符合門檻的舊觀測。</p>}
    </div>}
    {unknown > 0 && <p className="gpu-finder-note">{unknown} 張 GPU 缺少容量或用量回報，無法比較剩餘 VRAM。</p>}
  </Section>;
}
