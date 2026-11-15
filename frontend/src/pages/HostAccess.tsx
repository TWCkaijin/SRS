import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, errorMessage, post } from '../api';
import { ConsoleContext } from '../context';
import { useContext } from 'react';
import type { Node } from '../types';
import { Button, CodeBlock, Field, InlineError, Loading, Notice } from '../ui';
import { formatDate } from '../utils';

type Account = { username: string; name: string; uid: number; gid: number; home: string; shell: string };
type Action = 'create' | 'update' | 'delete' | 'lock' | 'unlock';
type AccountPlan = { id: string; content_hash: string; status: string; expires_at: string; created_at: string; document: { action: Action; username: string; commands: string[][]; change_password: boolean; impact: string; failure_policy: string; runner_script: string; host: { hostname: string; username: string } } };
const labels: Record<Action, string> = { create: '新增帳號', update: '修改帳號', delete: '刪除帳號（保留家目錄）', lock: '鎖定密碼登入', unlock: '解鎖密碼登入' };
const quote = (s: string) => /^[\w/.-]+$/.test(s) ? s : `'${s.replaceAll("'", "'\\''")}'`;

export function HostAccounts({ node, onBusy }: { node: Node; onBusy: (busy: boolean) => void }) {
  const context = useContext(ConsoleContext);
  const canManage = context?.can('hosts.accounts') ?? false;
  const canApprove = context?.can('plan.approve') ?? false;
  const [items, setItems] = useState<Account[]>([]), [plans, setPlans] = useState<AccountPlan[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [editing, setEditing] = useState(false), [action, setAction] = useState<Action>('create');
  const [username, setUsername] = useState(''), [name, setName] = useState(''), [changePassword, setChangePassword] = useState(false);
  const [plan, setPlan] = useState<AccountPlan | null>(null), [confirmation, setConfirmation] = useState(''), [reviewed, setReviewed] = useState(false);
  const [password, setPassword] = useState(''), [sudo, setSudo] = useState('');
  const visibleAccounts = items.filter(account => account.uid >= 1000 && account.uid !== 65534);
  const alive = useRef(true);
  const reviewRef = useRef<HTMLDivElement>(null);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);
  async function reload() {
    setLoading(true); setError('');
    try {
      const data = await api<{ items: Account[] }>(`/ssh-hosts/${node.id}/accounts`);
      if (alive.current) setItems(data.items);
      if (canManage) { const history = await api<{ items: AccountPlan[] }>(`/ssh-hosts/${node.id}/account-plans`); if (alive.current) setPlans(history.items); }
    } catch (e) { if (alive.current) setError(errorMessage(e)); }
    finally { if (alive.current) setLoading(false); }
  }
  useEffect(() => { alive.current = true; void reload(); return () => { alive.current = false; }; }, [node.id]);
  useEffect(() => { if (plan) { reviewRef.current?.focus({ preventScroll: true }); reviewRef.current?.scrollIntoView?.({ block: 'start' }); } }, [plan]);
  function choose(account: Account | null) {
    setEditing(true); setAction(account ? 'update' : 'create'); setUsername(account?.username || ''); setName(account?.name || ''); setChangePassword(false); setPlan(null); setPassword(''); setSudo(''); setMessage('');
  }
  async function prepare(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      setPlan(await post<AccountPlan>(`/ssh-hosts/${node.id}/account-plans`, { revision: node.ssh_connection?.revision, action, username, name, change_password: changePassword }));
      setConfirmation(''); setReviewed(false);
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  async function execute(event: FormEvent) {
    event.preventDefault(); if (!plan || !reviewed) return;
    setBusy(true); setError('');
    try {
      await post(`/ssh-hosts/${node.id}/account-plans/${plan.id}/execute`, { content_hash: plan.content_hash, confirmation, password: password || null, sudo_password: sudo || null });
      setPlan(null); setEditing(false); setMessage('已執行審核計畫，帳號清單已重新讀取。'); await reload();
    } catch (e) { setError(errorMessage(e)); setPlan(null); setEditing(false); }
    finally { setPassword(''); setSudo(''); setBusy(false); }
  }
  return <div className="accounts-panel">
    <p className="form-description">僅顯示 UID ≥ 1000 的帳號，排除 nobody（UID 65534）。目前 SSH 帳號仍受保護；所有變更均須先產生計畫並審核。</p>
    <div className="account-toolbar">{canManage && <Button onClick={() => choose(null)} disabled={busy || !!plan}>新增帳號</Button>}<Button onClick={() => void reload()} disabled={busy || loading}>重新載入</Button></div>
    <InlineError message={error} />{message && <p role="status">{message}</p>}
    {loading ? <Loading label="正在讀取使用者…" /> : <div className="table-scroll"><table><caption className="sr-only">Linux 帳號清單</caption><thead><tr><th>帳號 / 名稱</th><th>UID / GID</th><th>家目錄 / Shell</th><th>操作</th></tr></thead><tbody>{visibleAccounts.map(item => {
      const protectedUser = item.uid < 1000 || item.uid >= 65534 || item.username === node.ssh_connection?.username;
      return <tr key={item.username}><td><strong>{item.username}</strong><br />{item.name || '—'}</td><td>{item.uid} / {item.gid}</td><td>{item.home}<br /><small>{item.shell}</small></td><td>{protectedUser ? '受保護' : canManage ? <Button disabled={busy || !!plan} onClick={() => choose(item)}>管理 {item.username}</Button> : '唯讀'}</td></tr>;
    })}</tbody></table>{!visibleAccounts.length && <p>沒有符合條件的一般使用者帳號。</p>}</div>}
    {editing && !plan && <form onSubmit={prepare} className="account-editor"><h3>{action === 'create' ? '新增帳號計畫' : `管理 ${username}`}</h3><div className="form-grid">
      <Field label="使用者帳號"><input value={username} onChange={e => setUsername(e.target.value)} disabled={action !== 'create' || busy} required pattern="[a-z_][a-z0-9_-]{0,31}" maxLength={32} /></Field>
      {action !== 'create' && <Field label="操作"><select value={action} onChange={e => setAction(e.target.value as Action)} disabled={busy}>{(['update', 'lock', 'unlock', 'delete'] as Action[]).map(a => <option key={a} value={a}>{labels[a]}</option>)}</select></Field>}
      {['create', 'update'].includes(action) && <Field label="顯示名稱"><input value={name} onChange={e => setName(e.target.value)} maxLength={128} disabled={busy} /></Field>}
    </div>{action === 'update' && <label className="checkbox-row"><input type="checkbox" checked={changePassword} onChange={e => setChangePassword(e.target.checked)} />同時重設密碼（審核時輸入）</label>}
      <div className="form-actions"><Button onClick={() => setEditing(false)} type="button" disabled={busy}>取消</Button><Button type="submit" variant="primary" busy={busy}>產生操作計畫</Button></div></form>}
    {plan && <div className="account-review approval-box" ref={reviewRef} tabIndex={-1} role="region" aria-label="帳號操作審核"><h3>審核：{labels[plan.document.action]}</h3><p>{node.alias} · {plan.document.host.hostname} · {plan.document.username}</p><Notice>{plan.document.impact}</Notice><p>{plan.document.failure_policy}</p>
      <CodeBlock label="將執行的命令" value={plan.document.commands.map(argv => (plan.document.host.username === 'root' ? '' : 'sudo ') + argv.map(quote).join(' ')).join('\n')} />
      {plan.document.change_password && <p>chpasswd 透過標準輸入接收新密碼；密碼不列入計畫或日誌。</p>}
      <details><summary>檢視完整執行腳本與前置檢查</summary><CodeBlock value={plan.document.runner_script} /></details>
      <p>有效期限：{formatDate(plan.expires_at)}</p><code className="hash-value">{plan.content_hash}</code>
      <form onSubmit={execute}><div className="form-grid">{plan.document.change_password && <Field label="帳號新密碼"><input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} required disabled={busy} maxLength={256} /></Field>}<Field label="sudo 密碼" hint="僅供本次操作，不會保存；root 或免密 sudo 可留白。"><input type="password" autoComplete="off" value={sudo} onChange={e => setSudo(e.target.value)} disabled={busy} maxLength={256} /></Field><Field label={`輸入 ${plan.document.username} 確認`}><input value={confirmation} onChange={e => setConfirmation(e.target.value)} disabled={busy} required autoComplete="off" /></Field></div>
        <label className="checkbox-row"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={busy} />我已審查命令、主機與影響，同意執行此計畫。</label><div className="form-actions"><Button type="button" disabled={busy} onClick={() => { setPlan(null); setPassword(''); setSudo(''); }}>返回修改</Button><Button type="submit" variant={plan.document.action === 'delete' ? 'danger' : 'primary'} busy={busy} disabled={!reviewed || confirmation !== plan.document.username || !canApprove}>核准並執行</Button></div>{!canApprove && <p>目前帳號沒有計畫核准權限。</p>}</form>
    </div>}
    {!!plans.length && <details><summary>最近帳號操作紀錄</summary><ul>{plans.map(p => <li key={p.id}>{formatDate(p.created_at)} · {p.document.username} · {labels[p.document.action]} · {p.status}</li>)}</ul></details>}
  </div>;
}
