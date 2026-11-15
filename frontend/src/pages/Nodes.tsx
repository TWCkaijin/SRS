import { PingSparkline } from '../PingSparkline';
import { HostActivity } from './Observability';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Activity, CheckCheck, Plus, Settings2, Square, TerminalSquare, Trash2 } from 'lucide-react';
import { api, ApiError, errorMessage, post } from '../api';
import { useConsole } from '../context';
import type { MonitorState, Node } from '../types';
import { Badge, Button, EmptyState, InlineError, PageIntro } from '../ui';
import { formatDate } from '../utils';
import { NodeCard } from './Overview';
const HostTerminal = lazy(() => import('./HostTerminal').then(module => ({ default: module.HostTerminal })));
import { SSHImport } from './SSHImport';
import { HostProfile, SSHHostForm } from './SSHHosts';

const empty: MonitorState = { active: false, generation: '', host_ids: [], results: {}, interval_seconds: 2 };

export function NodesPage() {
  const { data, can, refresh, notify } = useConsole();
  const [expandedHosts, setExpandedHosts] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const [monitor, setMonitor] = useState<MonitorState>(empty);
  const [terminal, setTerminal] = useState<Node | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [interval, setInterval] = useState(2);
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState<Node | null | undefined>(undefined);
  const [clock, setClock] = useState(Date.now());
  const current = useRef(monitor); current.current = monitor;
  const mounted = useRef(true);
  const mutation = useRef(0);
  const startedAt = useRef(Date.now());
  const enabled = data.nodes.filter(node => node.ssh_connection?.enabled !== false);
  const allSelected = enabled.length > 0 && enabled.every(node => selected.includes(node.id));
  const active = monitor.active && (!monitor.expires_at || Date.parse(monitor.expires_at) > clock);
  useEffect(() => {
    mounted.current = true;
    let pending = false;
    async function poll(renew: boolean) {
      if (pending) return;
      pending = true;
      const version = mutation.current;
      try {
        const state = current.current;
        const next = renew && state.active
          ? await post<MonitorState>('/monitor/heartbeat', { generation: state.generation }).catch(err => { if (err instanceof ApiError && err.status === 409) return api<MonitorState>('/monitor'); throw err; })
          : await api<MonitorState>('/monitor');
        if (mounted.current && version === mutation.current) {
          setMonitor(next); setError('');
          if (!renew && next.active) { setSelected(next.host_ids); setInterval(next.interval_seconds); }
        }
      } catch (err) { if (mounted.current && version === mutation.current) setError(errorMessage(err)); }
      finally { pending = false; }
    }
    void poll(false);
    const poller = window.setInterval(() => void poll(true), 1000);
    const ticker = window.setInterval(() => setClock(Date.now()), 1000);
    function stopOnExit() {
      const state = current.current;
      if (state.active) void api('/monitor/stop', { method: 'POST', body: JSON.stringify({ generation: state.generation }), keepalive: true }).catch(() => {});
    }
    window.addEventListener('pagehide', stopOnExit);
    return () => {
      mounted.current = false; window.clearInterval(poller); window.clearInterval(ticker);
      window.removeEventListener('pagehide', stopOnExit); stopOnExit();
    };
  }, []);
  async function start(hosts = selected, seconds = interval) {
    setBusy(true); setError(''); mutation.current += 1;
    try {
      const next = await post<MonitorState>('/monitor', { host_ids: hosts, interval_seconds: seconds });
      if (mounted.current) { current.current = next; setMonitor(next); startedAt.current = Date.now(); }
      else await post('/monitor/stop', { generation: next.generation });
    } catch (err) { if (mounted.current) setError(errorMessage(err)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function stop() {
    setBusy(true); setError(''); mutation.current += 1;
    try {
      const next = await post<MonitorState>('/monitor/stop', { generation: monitor.generation });
      if (mounted.current) { current.current = next; setMonitor(next); }
    } catch (err) { if (mounted.current) setError(errorMessage(err)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function removeHost(node: Node) {
    const name = node.ssh_connection?.display_name || node.alias;
    if (!window.confirm(`刪除主機「${name}」的 SSH 設定與 profile，並停止相關監控？遠端主機上的資料不會刪除。`)) return;
    setDeleting(node.id); setError(''); mutation.current += 1;
    try {
      await api(`/ssh-hosts/${encodeURIComponent(node.id)}`, { method: 'DELETE', body: JSON.stringify({ inventory_revision: node.inventory_revision, revision: node.ssh_connection?.revision ?? null }) });
      setSelected(previous => previous.filter(id => id !== node.id));
      if (editing?.id === node.id) setEditing(undefined);
      const next = await api<MonitorState>('/monitor'); current.current = next; setMonitor(next);
      await refresh(); notify('主機已刪除。');
    } catch (err) { setError(errorMessage(err)); } finally { setDeleting(null); }
  }
  function choose(hosts: string[]) {
    setSelected(hosts);
    if (active) { if (hosts.length) void start(hosts); else void stop(); }
  }
  function toggle(id: string) { choose(selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id]); }
  function changeInterval(seconds: number) { setInterval(seconds); if (active) void start(selected, seconds); }
  const changed = interval !== monitor.interval_seconds || selected.slice().sort().join(',') !== monitor.host_ids.slice().sort().join(',');
  const waitingTooLong = active && clock - (monitor.checked_at ? Date.parse(monitor.checked_at) : startedAt.current) > 30000;
  return <>
    <PageIntro title="主機監控" description="勾選主機開始監控；切換頁面仍持續採樣，關閉分頁、登出或按停止後結束。" action={can('hosts.manage') && <div className="monitor-actions"><Button onClick={() => setImporting(true)}>匯入 SSH 設定</Button><Button variant="primary" onClick={() => setEditing(null)}><Plus size={16} />新增 SSH 主機</Button></div>} />
    {importing && <SSHImport onClose={() => setImporting(false)} />}
    {terminal && <Suspense fallback={<p role="status">正在載入終端…</p>}><HostTerminal node={terminal} onClose={() => setTerminal(null)} /></Suspense>}
    {editing !== undefined && <SSHHostForm key={editing?.id || 'new'} node={editing} onClose={() => setEditing(undefined)} />}
    <section className="monitor-controls" aria-label="監控操作">
      <div className="monitor-actions"><label className="monitor-select-all"><input type="checkbox" checked={allSelected} disabled={!enabled.length || busy || !can('monitor.manage')} ref={input => { if (input) input.indeterminate = selected.length > 0 && !allSelected; }} onChange={() => choose(allSelected ? [] : enabled.map(node => node.id))} /><CheckCheck size={17} />全選主機</label><span className="muted">已選 {selected.length} / {enabled.length} 台</span><label className="monitor-interval">更新間隔<select aria-label="更新間隔" value={interval} disabled={busy || !can('monitor.manage')} onChange={event => changeInterval(Number(event.target.value))}>{[1, 2, 5, 10].map(seconds => <option key={seconds} value={seconds}>{seconds} 秒</option>)}</select></label><div className="monitor-buttons">{(!active || changed) && <Button variant="primary" busy={busy} disabled={!selected.length || !can('monitor.manage') || (active && !changed)} onClick={() => void start()}><Activity size={16} />{active && changed ? '套用選取並監控' : '開始實際監控'}</Button>}{active && <Button disabled={!active || busy || !can('monitor.manage')} onClick={stop}><Square size={14} />停止監控</Button>}</div></div>
      {waitingTooLong && <p className="monitor-error" role="status">監控資料尚未更新，請確認監控程序仍在執行。</p>}
      <span className="monitor-summary">{active ? `監控中 · ${monitor.host_ids.length} 台` : '監控已停止'}</span><InlineError message={error} />
    </section>
    <div className="node-grid node-grid-detail">{data.nodes.map(node => {
      const result = monitor.results[node.id];
      const targeted = active && monitor.host_ids.includes(node.id);
      const stale = Boolean(result?.snapshot && clock - Date.parse(result.snapshot.collected_at) > 30000);
      // Never use a demo or scheduled snapshot as live telemetry.
      const connection = node.ssh_connection;
      const profile = connection?.profile;
      const readingFailed = targeted && (result?.status === 'error' || Boolean(result?.error || result?.snapshot?.gpu_error) || stale || (!result && waitingTooLong));
      const readingOK = targeted && result?.status === 'online' && Boolean(result.snapshot) && !readingFailed;
      const tone = readingFailed ? 'error' : readingOK ? 'live' : 'idle';
      const stateLabel = readingFailed ? '離線' : readingOK ? '已連線' : targeted ? '連線中' : '離線';
      const visible: Node = { ...node, enabled: true,
        display_name: connection?.display_name || node.alias, hostname: connection?.hostname || node.hostname,
        ssh_username: result?.ssh_username || connection?.username || node.ssh_username,
        host_key_sha256: result?.host_key_sha256 || connection?.host_key_sha256 || node.host_key_sha256,
        hardware: profile ? { cpu_model: profile.cpu_model, logical_cpus: profile.logical_cpus, memory_gb: Math.round(profile.memory_total_mb / 1024), gpus: profile.gpus.map(gpu => ({ index: gpu.index, uuid: gpu.uuid || '', name: gpu.name, memory_mb: gpu.memory_total_mb || 0, in_scope: true })) } : { ...node.hardware, gpus: node.hardware?.gpus?.map(gpu => ({ ...gpu, in_scope: true })) },
        snapshot: result?.snapshot ? { ...result.snapshot, status: !targeted ? 'monitor_stopped' : readingFailed ? 'error' : 'online' } : null, observed_at: result?.snapshot?.collected_at || null };
      return <section className={`monitor-node host-row state-${tone}`} data-host-id={node.id} data-status={tone} key={node.id}>
        <NodeCard node={visible} showStatus={false} expanded={Boolean(expandedHosts[node.id])}
          header={<>
<label className="monitor-node-choice"><input type="checkbox" aria-label={`監控 ${node.alias}`} checked={selected.includes(node.id)} disabled={connection?.enabled === false || busy || !can('monitor.manage')} onChange={() => toggle(node.id)} /><div className="host-identity"><h2>{connection?.display_name || node.alias}</h2><span>{connection?.hostname || node.hostname}{connection?.port && connection.port !== 22 ? `:${connection.port}` : ''}</span></div><div className="host-observation">{targeted
            ? <PingSparkline key={`${node.id}:${monitor.generation}`} ping={result?.ping} clock={clock} />
            : visible.snapshot
              ? <time dateTime={visible.snapshot.collected_at}>上次觀測 {formatDate(visible.snapshot.collected_at)}</time>
              : <span>尚無實際觀測</span>}</div><Badge status={readingFailed ? 'error' : readingOK ? 'online' : 'unknown'} label={stateLabel} /></label>
        {targeted && result?.error && <p className="host-read-error" role="status">{result.error}<small>嘗試時間 {formatDate(result.checked_at)}</small></p>}
        {targeted && !result && <p className="monitor-footnote" role="status">等待第一筆真實觀測…</p>}
          </>}
          actions={<div className="host-settings-action" role="group" aria-label={`操作 ${connection?.display_name || node.alias}`}><Button variant="ghost" className="host-expand" aria-expanded={Boolean(expandedHosts[node.id])} aria-controls={`host-details-${node.id}`} onClick={() => setExpandedHosts(previous => ({ ...previous, [node.id]: !previous[node.id] }))}>{expandedHosts[node.id] ? '收合資料' : '展開資料'} <span aria-hidden="true">{expandedHosts[node.id] ? '⌃' : '⌄'}</span></Button>{can('hosts.terminal') && <Button variant="ghost" onClick={() => setTerminal(node)} disabled={!connection?.host_key_sha256 || !connection.enabled} title={!connection?.host_key_sha256 ? '請先設定 SSH 並確認主機指紋' : '開啟網頁終端'}><TerminalSquare size={14} />終端</Button>}{can('hosts.manage') && <Button variant="ghost" onClick={() => setEditing(node)}><Settings2 size={14} />設定</Button>}{can('hosts.manage') && <Button variant="ghost" className="text-red" aria-label={`刪除主機：${connection?.display_name || node.alias}`} busy={deleting === node.id} disabled={Boolean(deleting)} onClick={() => void removeHost(node)}><Trash2 size={14} />刪除</Button>}</div>}
          hardwareDetails={<section className="machine-detail-section machine-hardware">
            {connection ? <HostProfile host={connection} showError={targeted} defaultOpen /> : <><h3>硬體資訊</h3><p className="muted small">設定 SSH 後，首次連線會自動取得硬體資訊。</p></>}
          </section>}
        >
          <HostActivity snapshot={visible.snapshot} />
        </NodeCard>
      </section>;
    })}</div>
    {!data.nodes.length && <EmptyState title="新增第一台主機" description="輸入 SSH 連線資料後，即可取得硬體資訊並開始監控。" />}
  </>;
}
