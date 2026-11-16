import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { Cpu, HardDrive, Layers3, MemoryStick, Network, Server } from 'lucide-react';
import type { Node } from '../types';
import { Badge, KeyValue, Meter } from '../ui';
import { formatBytes, formatDate, formatMemory, formatRate, percent } from '../utils';

export function NodeCard({ node, detailed = false, emptyLabel = "尚無快照 · 需核准盤點計畫", showStatus = true, expanded = true, header, actions, hardwareDetails, children }: { node: Node; detailed?: boolean; emptyLabel?: string; showStatus?: boolean; expanded?: boolean; header?: ReactNode; actions?: ReactNode; hardwareDetails?: ReactNode; children?: ReactNode }) {
  const snapshot = node.snapshot;
  const profile = node.ssh_connection?.profile;
  const network = snapshot?.network || [];
  const inventoryGpus = node.hardware?.gpus || [];
  // A successful observation is authoritative, even when first-time inventory failed.
  // If the query failed, retain inventory names without inventing live readings.
  const gpus = snapshot && !snapshot.gpu_error && snapshot.gpus !== undefined
    ? snapshot.gpus.map(gpu => ({
      index: gpu.index, uuid: gpu.uuid || '', name: gpu.name, memory_mb: gpu.memory_total_mb,
      in_scope: inventoryGpus.find(item => gpu.uuid && item.uuid ? item.uuid === gpu.uuid : item.index === gpu.index)?.in_scope,
    }))
    : inventoryGpus;
  const scrollable = gpus.length > 2;
  const gpuPreview = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const firstTwo = gpuPreview.current;
    const list = firstTwo?.parentElement;
    if (!firstTwo || !list) return;
    const fitPreview = () => {
      // Use layout pixels so long names, text resizing and zoom can change row height.
      const height = getComputedStyle(firstTwo).height;
      if (parseFloat(height) > 0) list.style.maxBlockSize = height;
    };
    fitPreview();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(fitPreview) : undefined;
    observer?.observe(firstTwo);
    return () => { observer?.disconnect(); list.style.removeProperty('max-block-size'); };
  }, [scrollable]);
  const gpuRow = (gpu: typeof gpus[number]) => {
    const observed = snapshot?.gpus?.find(item => gpu.uuid && item.uuid ? item.uuid === gpu.uuid : item.index === gpu.index);
    const vramUsed = observed?.memory_used_mb;
    const vramPercent = percent(vramUsed ?? undefined, observed?.memory_total_mb ?? undefined);
    return <div className={`gpu-row ${gpu.in_scope === false ? 'gpu-out-of-scope' : ''}`} key={gpu.uuid || gpu.index}>
      <div className="gpu-chip"><Layers3 size={17} aria-hidden="true" /></div>
      <div className="gpu-description">
        <strong>{gpu.name.replace(/^NVIDIA\s+/, '')}</strong>
        <span>GPU {gpu.index} · {formatMemory(gpu.memory_mb)}{gpu.in_scope === false ? ' · 不納管' : ''}</span>
      </div>
      <div className="gpu-utilization">
        <div className="gpu-compute-reading"><span>運算</span><strong>{typeof observed?.utilization_percent === 'number' ? `${observed.utilization_percent}%` : '—'}</strong></div>
        {observed && <span>VRAM {vramUsed == null ? '—' : `${formatMemory(vramUsed)} 已用${vramPercent == null ? '' : ` · ${Math.round(vramPercent)}%`}`}</span>}
      </div>
    </div>;
  };
  const networkRow = (nic: typeof network[number]) => <div className="network-reading" key={nic.name}>
    <div><strong>{nic.name}</strong><span className={nic.state === 'up' ? 'text-green' : 'muted'}>{nic.state === 'up' ? '已連線' : nic.state === 'down' ? '未連線' : nic.state}{nic.speed_mbps ? ` · ${nic.speed_mbps} Mbps` : ''}</span></div>
    <dl><KeyValue label="接收">{formatRate(nic.rx_bytes_per_second)}</KeyValue><KeyValue label="傳送">{formatRate(nic.tx_bytes_per_second)}</KeyValue></dl>
  </div>;
  return <article id={`node-summary-${node.id}`} className={`node-card ${!node.enabled ? 'node-excluded' : ''}`}>
    <div className="host-summary-left">{header}
    {showStatus && <div className="node-top"><div className="node-symbol"><Server size={21} strokeWidth={1.6} /></div><div><h3>{node.display_name || node.alias}</h3><span className="mono muted">{node.alias}</span></div>{showStatus && <Badge status={node.enabled ? snapshot?.status || 'unknown' : 'disabled'} label={!node.enabled ? '排除於試點' : undefined} />}</div>}
    <div className="hardware-line"><span><Cpu size={14} />{node.hardware?.logical_cpus ?? '—'} 邏輯核心</span><span title="主記憶體（RAM）"><MemoryStick size={14} aria-hidden="true" />{node.hardware?.memory_gb ?? '—'} GB RAM</span></div>
    <div className="node-meters"><Meter label="CPU" value={typeof snapshot?.cpu_percent === 'number' ? snapshot.cpu_percent : null} /><Meter label="RAM" value={percent(snapshot?.memory_used_mb, snapshot?.memory_total_mb)} detail={snapshot?.memory_total_mb ? `${formatMemory(snapshot.memory_used_mb)} / ${formatMemory(snapshot.memory_total_mb)}` : undefined} /></div>
    {!header && <footer className="node-footer"><span>{snapshot ? `${snapshot.status === 'monitor_stopped' ? '上次觀測' : snapshot.status === 'error' ? '上次資料' : snapshot.source === 'remote' ? '更新於' : '模擬資料'} ${formatDate(snapshot.collected_at || node.observed_at)}` : emptyLabel}</span></footer>}
    </div>
    <div className="gpu-summary">
      <div className="gpu-section-heading"><strong>GPU</strong><span>共 {gpus.length} 張{scrollable && ' · 可上下捲動'}</span></div>
      <div className={`gpu-list${scrollable ? ' gpu-list-scroll' : ''}`} role="group" aria-label={`${node.display_name || node.alias} 的 GPU 使用狀態`} tabIndex={scrollable ? 0 : undefined}>
        {scrollable ? <><div className="gpu-preview" ref={gpuPreview}>{gpus.slice(0, 2).map(gpuRow)}</div>{gpus.slice(2).map(gpuRow)}</> : gpus.length ? gpus.map(gpuRow) : <p className="muted small">{snapshot && !snapshot.gpu_error && snapshot.gpus !== undefined ? '本次觀測未回報 GPU 設備' : '尚無 GPU 設備紀錄'}</p>}
      </div>
    </div>
    {actions}
    <div className="machine-detail-area" id={`host-details-${node.id}`} hidden={!expanded}>
      <div className="machine-detail-grid">
        {hardwareDetails}
        <section className="machine-detail-section">
          <h3><HardDrive size={16} aria-hidden="true" />磁碟</h3>
          {snapshot?.storage?.length ? snapshot.storage.map(disk => <div className="storage-reading" key={disk.device + disk.mountpoint}><Meter label={disk.mountpoint} value={percent(disk.used_bytes, disk.total_bytes)} /><small>{formatBytes(disk.used_bytes)} / {formatBytes(disk.total_bytes)} · 可用 {formatBytes(disk.available_bytes)}</small><span className="mono muted">{disk.device} · {disk.filesystem}</span></div>) : <p className="muted small">{snapshot?.storage ? '未回傳已掛載的本機磁碟' : '尚無磁碟使用量'}</p>}
          {profile && <details className="device-inventory" open={!snapshot?.storage?.length}>
            <summary>實體磁碟清單</summary>
            {profile.disks.map(disk => <div className="profile-device" key={disk.name}><strong>{disk.rotational ? 'HDD' : 'SSD'} · {disk.model || disk.name}</strong><span>{formatBytes(disk.size_bytes)} · {disk.transport || '介面未知'} · {disk.name}</span><small>{disk.mountpoints.join('、') || '無掛載點'}</small></div>)}
            {!profile.disks.length && <p className="muted small">{profile.disk_error || '未回傳實體磁碟'}</p>}
          </details>}
        </section>
        <section className="machine-detail-section">
          <h3><Network size={16} aria-hidden="true" />網路</h3>
          {network.length ? network.slice(0, 2).map(networkRow) : <p className="muted small">{snapshot?.network ? '未回傳網路介面' : '尚無網路流量資料'}</p>}
          {network.length > 2 && <details className="device-inventory">
            <summary>其他 {network.length - 2} 張網路介面</summary>
            {network.slice(2).map(networkRow)}
          </details>}
          {typeof snapshot?.uptime_seconds === 'number' && <p className="muted small">已開機 {Math.floor(snapshot.uptime_seconds / 86400)} 天 {Math.floor(snapshot.uptime_seconds % 86400 / 3600)} 小時</p>}
          {profile && <details className="device-inventory" open={!snapshot?.network?.length}>
            <summary>網路介面設定</summary>
            {profile.interfaces?.map(nic => <div className="profile-device" key={nic.name}><strong>{nic.name}</strong><span>{nic.mac || '—'} · MTU {nic.mtu || '—'}</span><small>{nic.addresses.join('、') || '未配置 IP'}</small></div>)}
            {!profile.interfaces?.length && <p className="muted small">未回傳網路介面設定</p>}
          </details>}
        </section>
        {children && <section className="machine-detail-section machine-processes">{children}</section>}
      </div>
      {detailed && <dl className="node-details"><KeyValue label="CPU 型號">{node.hardware?.cpu_model || '未記錄'}</KeyValue><KeyValue label="主機名稱">{node.hostname || '未記錄'}</KeyValue><KeyValue label="SSH 身分">{node.ssh_username || '未設定'}</KeyValue><KeyValue label="清單版本">{node.inventory_revision ?? '—'}</KeyValue><KeyValue label="主機金鑰">{node.host_key_sha256 ? <code className="break-all">{node.host_key_sha256}</code> : <span className="text-amber">尚未釘選</span>}</KeyValue></dl>}
    </div>
    {snapshot?.gpu_error && <p className="monitor-error" role="status">{snapshot.gpu_error}</p>}
  </article>;
}
