import { HostAccounts } from './HostAccess';
import { useState, type FormEvent } from 'react';
import { HardDrive, KeyRound, RefreshCw, Save, ShieldCheck } from 'lucide-react';
import { errorMessage, post } from '../api';
import { useConsole } from '../context';
import type { Node, SSHHost } from '../types';
import { Button, Field, InlineError, KeyValue, Modal, Notice } from '../ui';
import { formatDate, formatMemory } from '../utils';

export function SSHHostForm({ node, onClose }: { node: Node | null; onClose: () => void }) {
  const { refresh, notify, can } = useConsole();
  const [tab, setTab] = useState('connection');
  const connection = node?.ssh_connection;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true); setError('');
    try {
      await post<SSHHost>('/ssh-hosts', {
        node_id: node?.id || null, revision: connection?.revision || null,
        display_name: values.get('display_name'), hostname: values.get('hostname'), port: Number(values.get('port')),
        username: values.get('username'), password: values.get('password') || null, enabled: true,
      });
      form.reset(); await refresh(); onClose();
      notify('SSH 設定已儲存，正在連線並建立硬體資訊。');
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  return <Modal title={node ? `SSH 主機設定 · ${connection?.display_name || node.alias}` : '新增 SSH 主機'} onClose={() => !busy && onClose()}>
    {node && <div className="host-tabs"><button className={tab==='connection'?'selected':''} onClick={()=>setTab('connection')} disabled={busy}>SSH 連線</button>{can('hosts.accounts') && <button className={tab==='accounts'?'selected':''} onClick={()=>setTab('accounts')} disabled={busy || !connection?.host_key_sha256}>使用者帳號</button>}</div>}
    {tab==='accounts' && node ? <HostAccounts node={node} onBusy={setBusy}/> : <form className="ssh-host-form" onSubmit={save}>
      <p className="form-description">第一次成功登入後，自動保存 CPU、RAM、GPU、SSD／HDD、網路介面與作業系統資料。</p>
      <div className="form-grid">
        <Field label="顯示名稱"><input name="display_name" defaultValue={connection?.display_name || node?.alias || ''} required maxLength={128} placeholder="例如：實驗室工作站" /></Field>
        <Field label="主機 IP／名稱"><input name="hostname" defaultValue={connection?.hostname || node?.hostname || ''} required maxLength={255} placeholder="192.168.1.20" /></Field>
        <Field label="SSH 連接埠"><input name="port" type="number" min={1} max={65535} defaultValue={connection?.port || 22} required /></Field>
        <Field label="SSH 帳號"><input name="username" autoComplete="off" defaultValue={connection?.username || node?.ssh_username || ''} required maxLength={64} placeholder="使用者名稱" /></Field>
        <Field label="SSH 密碼" className="span-2" hint={connection?.password_configured ? '已儲存加密密碼。留白保留原密碼；輸入新值即可更新。' : '密碼加密儲存在本機，不會回傳至頁面。'}><input name="password" type="password" autoComplete="new-password" required={!connection?.password_configured} maxLength={4096} placeholder={connection?.password_configured ? '留白保留原密碼' : '輸入 SSH 密碼'} /></Field>
      </div>
      <InlineError message={error} />
      <div className="form-actions"><Button type="button" onClick={onClose} disabled={busy}>取消</Button><Button variant="primary" type="submit" busy={busy}><Save size={16} />儲存並連線</Button></div>
    </form>}
  </Modal>;
}

export function HostProfile({ host, showError = true, defaultOpen = false }: { host: SSHHost; showError?: boolean; defaultOpen?: boolean }) {
  const { refresh, notify, can } = useConsole();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [trustOpen, setTrustOpen] = useState(false);
  const [verified, setVerified] = useState(false);
  const profile = host.profile;
  async function action(kind: 'profile' | 'trust') {
    setBusy(true); setError('');
    try {
      await post(`/ssh-hosts/${host.node_id}/${kind}`, { revision: host.revision, ...(kind === 'trust' ? { fingerprint: host.pending_host_key_sha256 } : {}) });
      await refresh(); setTrustOpen(false); setVerified(false); notify('正在連線並更新硬體資訊。');
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  return <div className="host-profile">
    {['queued', 'connecting'].includes(host.status) && <p className="profile-state" role="status">正在首次連線／更新硬體資訊…</p>}
    {showError && host.error && <p className="host-read-error" role="status">{host.error}</p>}
    {host.pending_host_key_sha256 && can('hosts.manage') && <Button onClick={() => { setVerified(false); setTrustOpen(true); }}><KeyRound size={15} />核對 SSH 指紋</Button>}
    <details className="profile-disclosure" open={defaultOpen}><summary><HardDrive size={15} />硬體資訊 <span>{profile ? formatDate(host.profile_updated_at) : '尚未建立'}</span><span className="disclosure-chevron" aria-hidden="true">⌄</span></summary>
      {!showError && host.error && <p className="muted small">上次讀取：{host.error}</p>}
      {profile ? <>
        <dl className="profile-facts"><KeyValue label="主機名稱">{profile.hostname}</KeyValue><KeyValue label="系統">{profile.os}</KeyValue><KeyValue label="核心 / 架構">{profile.kernel} / {profile.architecture}</KeyValue><KeyValue label="CPU">{profile.cpu_model}</KeyValue><KeyValue label="核心 / 執行緒">{profile.physical_cores || '—'} / {profile.logical_cpus}</KeyValue><KeyValue label="實體記憶體">{formatMemory(profile.memory_total_mb)}</KeyValue></dl>
        {profile.gpu_error && <p className="muted small">盤點時無法取得 GPU 清單。</p>}
        <details className="device-inventory">
          <summary>SSH 身分與指紋</summary>
          <dl className="profile-facts">
            <KeyValue label="SSH 帳號">{host.username}</KeyValue>
            <KeyValue label="SSH 指紋">{host.host_key_sha256 || '尚未確認'}</KeyValue>
            <KeyValue label="首次盤點">{formatDate(host.first_seen_at)}</KeyValue>
          </dl>
        </details>
      </> : <p className="muted small">成功登入後會自動建立，後續不必重新輸入硬體規格。</p>}
      {can('hosts.manage') && <Button busy={busy} disabled={['queued', 'connecting'].includes(host.status)} onClick={() => void action('profile')}><RefreshCw size={14} />{profile ? '重新盤點硬體' : '連線並讀取硬體'}</Button>}
    </details>
    <InlineError message={error} />
    {trustOpen && <Modal title="確認 SSH 主機身分" onClose={() => !busy && setTrustOpen(false)}><div className="ssh-trust-content"><Notice>請核對這台主機的 SSH 指紋。確認後會保存此指紋，並使用已儲存的帳密登入。</Notice><p>{host.hostname}:{host.port}</p><code className="fingerprint">{host.pending_host_key_sha256}</code>{host.host_key_sha256 && <p className="text-amber">目前金鑰與先前保存的金鑰不同，請先確認主機是否重新安裝或更換。</p>}<label className="checkbox-row"><input type="checkbox" checked={verified} onChange={e => setVerified(e.target.checked)} />我已核對此主機的 SSH 指紋</label><InlineError message={error} /><Button variant="primary" busy={busy} disabled={!verified} onClick={() => void action('trust')}><ShieldCheck size={15} />確認並建立 profile</Button></div></Modal>}
  </div>;
}
