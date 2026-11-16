import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowUpRight, Bell, ChevronRight, Layers3, MemoryStick, Search, Server, ShieldCheck, UsersRound } from 'lucide-react';
import { api, errorMessage, post } from '../api';
import { useConsole } from '../context';
import type { MonitorState, Node } from '../types';
import { Badge, Button, EmptyState, Field, InlineError, Loading, Meter, Modal, PageIntro, Section } from '../ui';
import { formatDate, percent } from '../utils';
import { BatchAccounts, batchHostReady } from './BatchAccounts';
import { HostAccounts } from './HostAccess';
import { GpuFinder, isRecentGpuObservation, observedGpus } from './GpuFinder';

export type ActivitySnapshot = NonNullable<Node['snapshot']> & {
  login_users?: { username: string; terminal: string; login_time: string; origin: string }[];
  login_users_error?: string | null;
  gpu_processes?: { gpu_uuid: string; pid: number; name: string; username: string | null; memory_mb: number | null }[];
  gpu_process_error?: string | null;
};
type Sample = { id: string; host_id: string; collected_at: string; observer: string; result: { status: string; error: string | null; snapshot: ActivitySnapshot | null }; cpu_peak?: number; memory_peak?: number; sample_count?: number };
type Alert = { id: string; host_id: string; rule: string; message: string; opened_at: string; last_seen_at: string; resolved_at: string | null; resolved_by?: string | null };
type Overview = { hosts: { host_id: string; alias: string; latest: Sample | null }[]; alerts: Alert[]; web_sessions: { username: string; expires_at: string }[]; events: { id: number; actor: string; action: string; created_at: string }[]; retention_days: number };
function useOverview() {
  const [value, setValue] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [resolveError, setResolveError] = useState('');
  const [resolving, setResolving] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const version = useRef(0);

  useEffect(() => {
    let active = true, fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      const requestVersion = version.current;
      try {
        const result = await api<Overview>('/observability');
        if (active && requestVersion === version.current) { setValue(result); setError(''); }
      } catch (e) {
        if (active && requestVersion === version.current) setError(errorMessage(e));
      } finally { fetching = false; }
    };
    void load();
    const timer = window.setInterval(load, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function resolve(alert: Alert) {
    setResolving(alert.id);
    setResolveError('');
    setStatus('');
    try {
      const closed = await post<Alert>(`/observability/alerts/${encodeURIComponent(alert.id)}/resolve`);
      version.current += 1;
      setValue(previous => previous && { ...previous, alerts: previous.alerts.map(item => item.id === closed.id ? closed : item) });
      setStatus(closed.resolved_by ? '告警已標為已處理。' : '此告警已自動恢復。');
    } catch (e) { setResolveError(errorMessage(e)); }
    finally { setResolving(null); }
  }

  return { value, error, resolve, resolving, resolveError, status };
}

function ResolveAlertButton({ alert, resolving, onResolve }: { alert: Alert; resolving: string | null; onResolve: (alert: Alert) => Promise<void> }) {
  return <Button className="alert-resolve" variant="ghost" busy={resolving === alert.id} disabled={resolving !== null}
    aria-label={`標為已處理：${alert.message}`} onClick={() => void onResolve(alert)}>標為已處理</Button>;
}
export function HostActivity({ snapshot, defaultOpen = false }: { snapshot: ActivitySnapshot | null; defaultOpen?: boolean }) {
  const processes = snapshot?.gpu_processes;
  return <details className="host-activity" open={defaultOpen}>
    <summary>GPU 程序{processes?.length ? <span className="host-activity-count">{processes.length} 筆</span> : null}</summary>
    {!snapshot ? <p>啟動監控後顯示，尚無觀測資料。</p> : <>
      <InlineError message={snapshot.gpu_process_error || ''} />
      {processes === undefined ? <p>這筆舊觀測尚未包含程序，請重新開始監控。</p> :
        processes.length ? <ul className="gpu-process-grid">
          {processes.map((process, index) => {
            const gpu = snapshot.gpus?.find(item => item.uuid === process.gpu_uuid);
            return <li key={`${process.gpu_uuid}:${process.pid}:${index}`}>
              <span className="gpu-process-device">{gpu ? `GPU ${gpu.index}` : process.gpu_uuid}</span>
              <strong className="gpu-process-name">{process.name}</strong>
              <dl>
                <div><dt>PID / 使用者</dt><dd>{process.pid} / {process.username || '無法讀取'}</dd></div>
                <div><dt>VRAM</dt><dd>{process.memory_mb == null ? '不支援回報' : `${process.memory_mb} MiB`}</dd></div>
              </dl>
            </li>;
          })}
        </ul> : !snapshot.gpu_process_error && <p>未回報 GPU 計算程序。</p>}
      <p className="muted gpu-process-note">顯示 NVIDIA 計算程序與顯示記憶體用量。</p>
    </>}
  </details>;
}
export function DashboardPage() {
  const { value, error, resolve, resolving, resolveError, status } = useOverview();
  const { data, navigate, can } = useConsole();
  const [clock, setClock] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const renderClock = Math.max(clock, Date.now());
  const fresh = value?.hosts.filter(host => host.latest && isRecentGpuObservation(host.latest.collected_at, renderClock)
    && host.latest.result.status === 'online') || [];
  const observations = observedGpus(value?.hosts || [], renderClock);
  const gpus = observations.filter(row => row.fresh);
  const knownVram = gpus.filter(row => row.freeMiB !== null);
  const freeVram = knownVram.reduce((sum, row) => sum + (row.freeMiB ?? 0), 0);
  const activeAlerts = value?.alerts.filter(alert => !alert.resolved_at) || [];
  const latestAlerts = [...activeAlerts].sort((a, b) => Date.parse(b.last_seen_at) - Date.parse(a.last_seen_at)).slice(0, 3);
  const metrics = [
    { icon: Server, label: '近期回報主機', number: `${fresh.length} / ${value?.hosts.length ?? 0}`, detail: '最近 60 秒內觀測成功' },
    { icon: Layers3, label: '已觀測 GPU', number: String(gpus.length), detail: '來自近期成功觀測的主機' },
    { icon: MemoryStick, label: '可用 VRAM', number: knownVram.length ? `${(freeVram / 1024).toFixed(1)} GiB` : '—', detail: knownVram.length ? `${knownVram.length} 張 GPU 有完整回報` : '尚無完整記憶體回報' },
    { icon: Bell, label: '未解除告警', number: String(activeAlerts.length), detail: '待確認或處理的告警' },
  ];
  return <>
    <PageIntro title="實驗室總覽" description="查看主機負載、可用 GPU 資源與待處理告警。" action={<Button variant="primary" onClick={() => navigate('/nodes')}><Activity size={16} />主機監控</Button>} />
    <InlineError message={error} />
    {!value ? !error && <Loading /> : <>
      <div className="overview-metrics dashboard-metrics">
        {metrics.map(({ icon: Icon, label, number, detail }) => <div className="overview-metric" key={label}>
          <span className="dashboard-metric-label"><Icon size={17} aria-hidden="true" />{label}</span>
          <strong>{number}</strong><small>{detail}</small>
        </div>)}
      </div>
      <GpuFinder rows={observations} />
      <div className="dashboard-layout">
        <Section className="dashboard-hosts" title="主機觀測摘要">
          {value.hosts.length ? <ul className="dashboard-host-list">{value.hosts.map(host => {
            const sample = host.latest;
            const snapshot = sample?.result.snapshot;
            const current = sample && isRecentGpuObservation(sample.collected_at, renderClock);
            const label = !sample ? '尚未觀測' : !current ? '舊紀錄' : sample.result.status === 'online' ? '近期成功' : '觀測失敗';
            return <li key={host.host_id}>
              <div className="dashboard-host-identity">
                <strong>{host.alias}</strong>
                {sample && <time dateTime={sample.collected_at}>{formatDate(sample.collected_at)}</time>}
              </div>
              <div className="dashboard-host-meters">
                <Meter label="CPU" value={snapshot?.cpu_percent ?? null} />
                <Meter label="RAM" value={percent(snapshot?.memory_used_mb, snapshot?.memory_total_mb)} />
              </div>
              <div className="dashboard-host-actions">
                <Badge status={current && sample ? sample.result.status : 'unknown'} label={label} />
                <a className="button button-ghost" href={`#/history/${host.host_id}`} aria-label={`查看 ${host.alias} 歷史`}>歷史<ChevronRight size={14} aria-hidden="true" /></a>
              </div>
            </li>;
          })}</ul> : <EmptyState title="尚無主機" description="加入主機後，即可從這裡查看觀測紀錄。" />}
        </Section>
        <Section className="dashboard-alerts" title="待處理告警" aside={<span className="muted">{activeAlerts.length} 筆</span>}>
          <InlineError message={resolveError} />
          <span className="sr-only" role="status">{status}</span>
          {latestAlerts.length ? <ul>{latestAlerts.map(alert => {
            const host = value.hosts.find(item => item.host_id === alert.host_id);
            return <li key={alert.id}><a href={`#/history/${alert.host_id}`}>
              <span><strong>{alert.message}</strong><small>{host?.alias || alert.host_id} · {formatDate(alert.last_seen_at)}</small></span>
              <ChevronRight size={16} aria-hidden="true" />
            </a>{can('monitor.manage') && <ResolveAlertButton alert={alert} resolving={resolving} onResolve={resolve} />}</li>;
          })}</ul> : <div className="dashboard-alert-empty"><ShieldCheck size={22} aria-hidden="true" /><p>目前沒有未解除的告警</p></div>}
          <a className="button button-ghost dashboard-history-link" href="#/history">查看告警與趨勢 <ArrowUpRight size={14} aria-hidden="true" /></a>
        </Section>
      </div>
      <details className="panel dashboard-audit">
        <summary>工作與網站活動</summary>
        <div className="overview-columns">
          <Section className="observability-panel" title="網站登入工作階段">
            {!value.web_sessions.length && <p className="muted">目前沒有網站登入紀錄。</p>}
            {value.web_sessions.map((session, i) => <p key={i}><strong>{session.username}</strong> · 到期 {formatDate(session.expires_at)}</p>)}
          </Section>
          <Section className="observability-panel" title="平台工作紀錄">
            <p>{data.jobs.filter(job => ['pending', 'queued'].includes(job.status.toLowerCase())).length} 個待執行 · {data.jobs.filter(job => job.status.toLowerCase() === 'running').length} 個執行中</p>
            <p className="muted">僅統計平台已記錄的工作。</p>
          </Section>
        </div>
        <Section className="observability-panel" title="最近登入與操作紀錄">
          {value.events.length ? <div className="table-scroll"><table>
            <thead><tr><th>時間</th><th>網站使用者</th><th>事件</th></tr></thead>
            <tbody>{value.events.map(event => <tr key={event.id}><td>{formatDate(event.created_at)}</td><td>{event.actor}</td><td>{event.action}</td></tr>)}</tbody>
          </table></div> : <p>尚無事件紀錄。</p>}
        </Section>
      </details>
    </>}
  </>;
}
export function HistoryPage({ id }: { id?: string }) {
  const { data, can } = useConsole();
  const { value, error: overviewError, resolve, resolving, resolveError, status } = useOverview();
  const [host, setHost] = useState(id || data.nodes[0]?.id || ''), [hours, setHours] = useState(24), [points, setPoints] = useState<Sample[]>([]), [count, setCount] = useState(0), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  useEffect(() => { if (id) setHost(id); }, [id]);
  const lastSelection = useRef('');
  useEffect(() => { let active = true; if (lastSelection.current !== `${host}/${hours}`) { setLoading(true); setPoints([]); setCount(0); lastSelection.current = `${host}/${hours}`; } setError(''); if (!host) { setLoading(false); return; }
    api<{ items: Sample[]; sample_count: number }>(`/monitor/history/${encodeURIComponent(host)}?hours=${hours}`).then(result => { if (active) { setPoints(result.items); setCount(result.sample_count); } }).catch(e => { if (active) setError(errorMessage(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; };
  }, [host, hours, value]);
  const alerts = value?.alerts.filter(a => a.host_id === host) || [];
  const latest = value?.hosts.find(h => h.host_id === host)?.latest;
  const end = Date.now(), start = end - hours * 3600000;
  // Never draw across failures or unobserved intervals.
  const series = (key: 'cpu_peak' | 'memory_peak') => {
    const runs: string[][] = []; let previous: Sample | null = null;
    for (const point of points) {
      if (!point.result.snapshot) { previous = null; continue; }
      const gap = previous ? Date.parse(point.collected_at) - Date.parse(previous.collected_at) : Infinity;
      if (!previous || gap > Math.max(90000, hours * 3600000 / 480 * 2)) runs.push([]);
      runs[runs.length - 1].push(`${Math.max(0, Math.min(1000, (Date.parse(point.collected_at) - start) / (end - start) * 1000))},${180 - (point[key] || 0) * 1.6}`);
      previous = point;
    }
    return runs.map(run => run.join(' '));
  };
  return <><PageIntro title="歷史監控與告警" description="保留最近 7 天；監控期間約每 30 秒保存一筆，告警轉變另行保存。未監控的時間沒有資料。" />
    <div className="history-filters"><Field label="主機"><select value={host} onChange={e => setHost(e.target.value)}>{data.nodes.map(n => <option key={n.id} value={n.id}>{n.alias}</option>)}</select></Field><Field label="時間範圍"><select value={hours} onChange={e => setHours(Number(e.target.value))}><option value={1}>最近 1 小時</option><option value={24}>最近 24 小時</option><option value={168}>最近 7 天</option></select></Field></div>
    <InlineError message={error || overviewError} /><Section className="observability-panel" title="CPU 與 RAM 趨勢"><p className="history-legend"><span className="series-key series-cpu">CPU</span><span className="series-key series-ram">RAM</span><span className="sample-count">區間峰值 · {count} 筆樣本</span></p>{loading ? <Loading /> : !points.length ? <EmptyState title="此時段尚無監控紀錄" description="請在主機監控頁明確啟動觀測，歷史資料才會累積。" /> : <><svg className="history-chart" viewBox="0 0 1000 210" role="img" aria-label={`最近 ${hours} 小時 CPU 與 RAM 使用率趨勢，詳細數值見下方資料表`}><text x="0" y="14">100%</text><text x="0" y="202">0%</text><line x1="0" y1="180" x2="1000" y2="180" className="chart-axis" /><>{series('cpu_peak').map((line, i) => <polyline key={i} points={line} className="chart-cpu" />)}{series('memory_peak').map((line, i) => <polyline key={i} points={line} className="chart-memory" />)}</>{points.filter(p => p.result.snapshot).map(p => <circle key={p.id} cx={Math.max(0, Math.min(1000, (Date.parse(p.collected_at) - start) / (end - start) * 1000))} cy={180 - (p.cpu_peak || 0) * 1.6} r="3" className="chart-dot" />)}</svg><div className="chart-range"><span>{formatDate(new Date(start).toISOString())}</span><span>{formatDate(new Date(end).toISOString())}</span></div><details><summary>檢視歷史數值與採樣使用者</summary><div className="table-scroll history-values"><table><thead><tr><th>時間</th><th>CPU 峰值</th><th>RAM 峰值</th><th>樣本</th><th>觀測者</th><th>狀態</th></tr></thead><tbody>{points.map(p => <tr key={p.id}><td>{formatDate(p.collected_at)}</td><td>{p.result.snapshot ? `${p.cpu_peak?.toFixed(1)}%` : '—'}</td><td>{p.result.snapshot ? `${p.memory_peak?.toFixed(1)}%` : '—'}</td><td>{p.sample_count}</td><td>{p.observer}</td><td>{p.result.status}</td></tr>)}</tbody></table></div></details></>}</Section>
    <Section className="observability-panel" title="告警紀錄">
      <p className="muted">CPU ≥95%、RAM ≥90%、VRAM ≥95%、磁碟可用 ≤10% 或觀測失敗。手動處理會關閉這筆告警，下次觀測若仍異常會建立新告警。</p>
      <InlineError message={resolveError} />
      <span className="sr-only" role="status">{status}</span>
      {alerts.length ? <div className="table-scroll"><table>
        <thead><tr><th>狀態</th><th>告警</th><th>首次 / 最近觀測</th><th>結案時間</th>{can('monitor.manage') && <th>操作</th>}</tr></thead>
        <tbody>{alerts.map(alert => <tr key={alert.id}>
          <td><Badge status={alert.resolved_by ? 'unknown' : alert.resolved_at ? 'success' : 'error'} label={alert.resolved_by ? '已手動處理' : alert.resolved_at ? '已恢復' : '待確認'} /></td>
          <td>{alert.message}</td>
          <td>{formatDate(alert.opened_at)}<br />{formatDate(alert.last_seen_at)}</td>
          <td>{alert.resolved_at ? <><time dateTime={alert.resolved_at}>{formatDate(alert.resolved_at)}</time>{alert.resolved_by && <><br /><small className="muted">處理人：{alert.resolved_by}</small></>}</> : '—'}</td>
          {can('monitor.manage') && <td>{!alert.resolved_at && <ResolveAlertButton alert={alert} resolving={resolving} onResolve={resolve} />}</td>}
        </tr>)}</tbody>
      </table></div> : <EmptyState title="尚無告警紀錄" />}
    </Section>
    <Section className="observability-panel" title="最後觀測的 GPU 程序"><p>{latest ? `觀測時間：${formatDate(latest.collected_at)} · 網站觀測者：${latest.observer}` : '尚無觀測'}</p><HostActivity snapshot={latest?.result.snapshot || null} /></Section>
  </>;
}
export function LoginUsersPanel() {
  const { data, navigate } = useConsole();
  const [monitor, setMonitor] = useState<MonitorState | null>(null);
  const [error, setError] = useState('');
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    let alive = true, fetching = false;
    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        // Read existing monitoring only; never start or extend a monitoring lease.
        const result = await api<MonitorState>('/monitor');
        if (alive) { setMonitor(result); setError(''); setClock(Date.now()); }
      } catch (e) {
        if (alive) { setMonitor(null); setError(errorMessage(e)); }
      } finally { fetching = false; }
    };
    void load();
    const timer = window.setInterval(load, 30000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  const active = monitor?.active && (!monitor.expires_at || Date.parse(monitor.expires_at) > clock);
  return <Section className="observability-panel login-users-panel" title="目前登入使用者" aside={active ? <Badge status="online" label="監控中" /> : undefined}>
    <p className="login-users-note"><UsersRound size={17} aria-hidden="true" /><span>每 30 秒更新。只計入系統回報的終端登入，每個帳號算一人。</span></p>
    <InlineError message={error} />
    {!monitor && !error ? <Loading /> : data.nodes.length ? <ul className="login-host-list">{data.nodes.map(node => {
      const watching = active && monitor?.host_ids.includes(node.id);
      const result = monitor?.results[node.id];
      const snapshot = result?.snapshot as ActivitySnapshot | null | undefined;
      const stale = !snapshot?.collected_at || clock - Date.parse(snapshot.collected_at) > 60000;
      const valid = watching && result?.status === 'online' && !result.error && !snapshot?.login_users_error && !stale && snapshot?.login_users !== undefined;
      const users = valid ? [...new Set(snapshot!.login_users!.map(user => user.username))].sort() : [];
      const status = error ? '更新失敗' : !watching ? '未開啟監控' : result?.error || snapshot?.login_users_error ? '讀取失敗' : !snapshot ? '等待觀測' : stale ? '資料已過期' : !valid ? '登入資料無法取得' : '監控中';
      return <li key={node.id}>
        <strong>{node.ssh_connection?.display_name || node.alias}</strong>
        <span className="login-count">{valid ? `${users.length} 人` : '—'}</span>
        <span className="login-user-names">{valid ? users.join('、') || '目前無終端登入帳號' : ''}</span>
        <span className="login-observation"><Badge status={valid ? 'online' : error || status === '讀取失敗' ? 'error' : 'unknown'} label={status} />{watching && snapshot && <time dateTime={snapshot.collected_at}>{formatDate(snapshot.collected_at)}</time>}</span>
      </li>;
    })}</ul> : <p className="muted">尚無主機可觀測。</p>}
    {!active && <p><Button onClick={() => navigate('/nodes')}>前往開啟主機監控</Button></p>}
  </Section>;
}
export function AccountsPage() {
  const { data, can } = useConsole();
  const [selected, setSelected] = useState<Node | null>(null);
  const [batchOpen, setBatchOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  const onBusy = useCallback((value: boolean) => setBusy(value), []);
  const filter = query.trim().toLowerCase();
  const hosts = data.nodes.filter(host => [
    host.alias, host.hostname, host.ssh_connection?.display_name, host.ssh_connection?.hostname, host.ssh_connection?.username,
  ].some(value => value?.toLowerCase().includes(filter)));
  return <>
    <PageIntro title="帳號管理" description="依主機查看 Linux 帳號、管理群組和權限。"
      action={can('hosts.accounts') && <Button variant="primary" disabled={!data.nodes.some(batchHostReady)} onClick={() => setBatchOpen(true)}>
        <UsersRound size={16} aria-hidden="true" />批次建立帳號
      </Button>} />
    {batchOpen && <BatchAccounts hosts={data.nodes} onClose={() => setBatchOpen(false)} />}
    <Section className="accounts-directory" title="主機帳號清單" aside={<span className="muted">{filter ? `${hosts.length} / ` : ''}{data.nodes.length} 台</span>}>
      {data.nodes.length > 1 && <div className="accounts-search">
        <label><Search size={17} aria-hidden="true" /><input type="search" aria-label="搜尋主機" placeholder="主機、位址或 SSH 帳號" value={query} onChange={event => setQuery(event.target.value)} /></label>
      </div>}
      {hosts.length ? <ul className="accounts-host-list">{hosts.map(host => {
        const connection = host.ssh_connection;
        const name = connection?.display_name || host.alias;
        const hostname = connection?.hostname || host.hostname;
        const unavailable = !connection ? '尚未設定 SSH' : !connection.enabled ? 'SSH 已停用' : connection.pending_host_key_sha256 || !connection.host_key_sha256 ? '需確認 SSH 指紋' : '';
        return <li key={host.id}>
          <div className="accounts-host-name"><span className="accounts-host-icon"><Server size={18} aria-hidden="true" /></span>
            <div><strong>{name}</strong>{hostname && <span className="muted">{hostname}</span>}</div>
          </div>
          <div className="accounts-host-ssh"><span className="muted">SSH 帳號</span><strong>{connection?.username || '—'}</strong></div>
          {unavailable ? <span className="accounts-host-state"><Badge status="unknown" label={unavailable} /></span> :
            <Button className="accounts-open" onClick={() => setSelected(host)} aria-label={`查看 ${name} 帳號`}>查看帳號<ChevronRight size={15} aria-hidden="true" /></Button>}
        </li>;
      })}</ul> : data.nodes.length ? <EmptyState title="找不到符合的主機" action={<Button onClick={() => setQuery('')}>清除搜尋</Button>} /> :
        <EmptyState title="尚無主機" description="先在主機監控加入 SSH 主機，再查看帳號清單。" action={<a className="button" href="#/nodes">前往主機監控</a>} />}
    </Section>
    <LoginUsersPanel />
    {selected && <Modal title={`${selected.ssh_connection?.display_name || selected.alias} · Linux 帳號管理`} onClose={() => { if (!busy) setSelected(null); }}><HostAccounts key={selected.id} node={selected} onBusy={onBusy} /></Modal>}
  </>;
}
