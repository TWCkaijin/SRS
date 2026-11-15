import { useEffect, useRef, useState, type FormEvent } from 'react';
import { post, errorMessage } from '../api';
import { useConsole } from '../context';
import type { Node } from '../types';
import { Badge, Button, CodeBlock, Field, InlineError, Modal, Notice } from '../ui';
import { formatDate } from '../utils';
import { accountCommands, type AccountPlan } from './HostAccess';

type BatchPlan = { uid: number; gid: number; items: AccountPlan[] };
type Outcome = { state: '完成' | '未確認' | '未執行'; detail?: string };

export function batchHostReady(host: Node) {
  const ssh = host.ssh_connection;
  return Boolean(ssh?.enabled && ssh.host_key_sha256 && !ssh.pending_host_key_sha256);
}

const hostName = (host: Node) => host.ssh_connection?.display_name || host.alias;

export function BatchAccounts({ hosts, onClose }: { hosts: Node[]; onClose: () => void }) {
  const { can, navigate } = useConsole();
  const [selected, setSelected] = useState<string[]>([]);
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [uid, setUid] = useState('');
  const [plan, setPlan] = useState<BatchPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [current, setCurrent] = useState('');
  const [error, setError] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome> | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const names = new Map(hosts.map(host => [host.id, hostName(host)]));
  const ready = hosts.filter(host => batchHostReady(host) && selected.includes(host.id));
  const readyToApprove = !busy && reviewed && confirmation === plan?.items[0]?.document.username && can('plan.approve');
  useEffect(() => { if (plan) stage.current?.focus(); }, [plan, outcomes]);

  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (!ready.length || busy) return;
    setBusy(true); setError('');
    try {
      const result = await post<BatchPlan>('/accounts/batch-create/plan', {
        hosts: Object.fromEntries(ready.map(host => [host.id, host.ssh_connection!.revision])),
        username, name, uid: uid ? Number(uid) : null,
      });
      setPlan(result); setReviewed(false); setConfirmation('');
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  async function execute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plan || !readyToApprove) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const password = String(values.get('shared-password') || '');
    if (!password || /[:\u0000-\u001f]/.test(password) || plan.items.some((item, i) => /[:\u0000-\u001f]/.test(String(values.get(`sudo-${i}`) || '')))) {
      setError('帳號密碼必填。密碼欄位不能包含冒號或控制字元。');
      return;
    }
    form.reset();
    setBusy(true); setError('');
    const next: Record<string, Outcome> = {};
    let halted = false;
    try {
      for (const [index, item] of plan.items.entries()) {
        if (halted) {
          next[item.host_id] = { state: '未執行' };
          continue;
        }
        setCurrent(names.get(item.host_id) || item.host_id);
        try {
          await post(`/ssh-hosts/${encodeURIComponent(item.host_id)}/account-plans/${item.id}/execute`, {
            content_hash: item.content_hash, confirmation, password,
            sudo_password: String(values.get(`sudo-${index}`) || '') || null,
          });
          next[item.host_id] = { state: '完成' };
        } catch (err) {
          next[item.host_id] = { state: '未確認', detail: errorMessage(err) };
          halted = true;
        }
      }
      setOutcomes(next);
    } finally {
      setCurrent(''); setBusy(false); setConfirmation(''); setReviewed(false);
    }
  }

  function returnToTargets() {
    setError(''); setReviewed(false); setConfirmation('');
    if (outcomes && plan) {
      setUid(String(plan.uid));
      setSelected(plan.items.filter(item => outcomes[item.host_id].state === '未執行').map(item => item.host_id));
    }
    setOutcomes(null); setPlan(null);
  }

  return <Modal title="批次建立 Linux 帳號" onClose={() => { if (!busy) onClose(); }}>
    <div className="accounts-panel batch-accounts">
      <InlineError message={error} />
      {!plan ? <form onSubmit={prepare}>
        <p>在勾選的主機建立同一組登入資料，並配對 Slurm 所需的 UID／GID。</p>
        <fieldset disabled={busy}>
          <legend>目標主機（已選 {ready.length} 台）</legend>
          <div className="batch-targets">{hosts.map(host => <label className="checkbox-row" key={host.id}>
            <input type="checkbox" checked={selected.includes(host.id)} disabled={!batchHostReady(host)}
              onChange={event => setSelected(previous => event.target.checked ? [...previous, host.id] : previous.filter(id => id !== host.id))} />
            <span><strong>{hostName(host)}</strong><small>{batchHostReady(host) ? host.ssh_connection?.hostname : '需啟用 SSH 並確認主機指紋'}</small></span>
          </label>)}</div>
        </fieldset>
        <div className="form-grid">
          <Field label="Linux 帳號"><input value={username} onChange={event => setUsername(event.target.value)}
            pattern="[a-z_][a-z0-9_\x2d]{0,31}" maxLength={32} required disabled={busy} autoComplete="off" /></Field>
          <Field label="顯示名稱（選填）"><input value={name} onChange={event => setName(event.target.value)} maxLength={128} disabled={busy} /></Field>
          <Field label="共同 UID／GID（選填）" hint="留白會自動選號。補建節點時請使用已建立的 UID／GID。">
            <input type="number" value={uid} onChange={event => setUid(event.target.value)} min={1000} max={59999} step={1} disabled={busy} />
          </Field>
        </div>
        <div className="form-actions"><Button type="submit" variant="primary" busy={busy} disabled={!ready.length || ready.length > 32}>檢查並產生批次計畫</Button></div>
        {ready.length > 32 && <p className="muted">一次最多選擇 32 台主機。</p>}
      </form> : <div className="batch-review" role="region" aria-label={outcomes ? '批次執行結果' : '批次帳號審核'} ref={stage} tabIndex={-1}>
        <h3>{outcomes ? '批次執行結果' : '審查所選主機'}</h3>
        <p><strong>{plan.items[0].document.username}</strong> · UID {plan.uid} ／ GID {plan.gid} · {plan.items.length} 台</p>
        <ul className="batch-results">{plan.items.map(item => {
          const outcome = outcomes?.[item.host_id];
          return <li key={item.id}>
            <strong>{names.get(item.host_id) || item.host_id}</strong>
            {outcome ? <><Badge status={outcome.state === '完成' ? 'success' : outcome.state === '未確認' ? 'error' : 'unknown'} label={outcome.state} />
              {outcome.detail && <p>{outcome.detail}</p>}</> : <details>
              <summary>{item.document.host.hostname} · 檢視命令</summary>
              <CodeBlock value={accountCommands(item)} />
              <span className="mono break-all">{item.content_hash}</span>
            </details>}
          </li>;
        })}</ul>
        {outcomes ? <>
          {Object.values(outcomes).every(item => item.state === '完成')
            ? <p role="status">所有主機已完成。設定 Slurm 時可選用此帳號。</p>
            : <Notice tone="amber">遇到錯誤後已停止。請先到該主機的帳號清單確認是否部分完成。補建節點時請沿用上方的 UID／GID。</Notice>}
          <div className="form-actions">
            <Button onClick={returnToTargets}>返回選擇主機</Button>
            {Object.values(outcomes).every(item => item.state === '完成') && <Button variant="primary" onClick={() => { onClose(); navigate('/training'); }}>前往 Slurm 設定</Button>}
          </div>
        </> : <>
          <Notice>每台主機會建立同名私人群組與家目錄。遇到失敗即停止，已完成的主機會保留。</Notice>
          <p className="muted">計畫有效至 {formatDate(plan.items[0].expires_at)}。密碼只用於本次執行。</p>
          <form onSubmit={execute}>
            <fieldset className="form-grid" disabled={busy}>
              <Field label="共用帳號密碼"><input name="shared-password" type="password" autoComplete="new-password" maxLength={256} required /></Field>
              <Field label={`輸入 ${plan.items[0].document.username} 確認`}>
                <input value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" required />
              </Field>
              {plan.items.map((item, index) => item.document.host.username !== 'root' && <Field key={item.id}
                label={`${names.get(item.host_id) || item.host_id} 的 sudo 密碼`} hint="免密 sudo 可留白">
                <input name={`sudo-${index}`} type="password" autoComplete="off" maxLength={256} />
              </Field>)}
            </fieldset>
            <label className="checkbox-row"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} disabled={busy} />我已核對所有主機、UID／GID 與變更範圍。</label>
            {current && <p role="status">正在設定 {current}…</p>}
            {!can('plan.approve') && <p className="muted">需要計畫核准權限。</p>}
            <div className="form-actions"><Button type="button" onClick={returnToTargets} disabled={busy}>返回修改</Button>
              <Button type="submit" variant="primary" busy={busy} disabled={!readyToApprove}>核准並建立帳號</Button></div>
          </form>
        </>}
      </div>}
    </div>
  </Modal>;
}
