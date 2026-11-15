import { useState } from 'react';
import { api, errorMessage, post } from '../api';
import { useConsole } from '../context';
import { Button, Field, InlineError, Modal, Notice } from '../ui';
type Candidate = { display_name: string; hostname: string; port: number; username: string; enabled: boolean; problem: string | null };
type Preview = { items: Candidate[]; warnings: string[] };
export function SSHImport({ onClose }: { onClose: () => void }) {
  const { data, refresh, notify } = useConsole();
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [passwords, setPasswords] = useState<Record<string,string>>({});
  const [saved, setSaved] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const duplicate = (item: Candidate) => data.nodes.some(n => n.ssh_connection?.hostname === item.hostname && n.ssh_connection.port === item.port && n.ssh_connection.username === item.username);
  async function load(local: boolean) {
    setBusy(true); setError('');
    try { const result = local ? await api<Preview>('/ssh-import/local') : await post<Preview>('/ssh-import/preview', { text }); setPreview(result); setSelected([]); setPasswords({}); setSaved([]); }
    catch(err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  async function save() {
    setBusy(true); setError(''); let count = 0;
    try {
      for (const item of preview?.items || []) {
        if (!selected.includes(item.display_name) || saved.includes(item.display_name)) continue;
        const { problem: _problem, ...fields } = item;
        await post('/ssh-hosts', { ...fields, password: passwords[item.display_name] });
        setSaved(prev => [...prev, item.display_name]); setPasswords(prev => ({ ...prev, [item.display_name]: '' })); count++;
      }
      notify('所選 SSH 主機已匯入，正在建立連線。'); await refresh(); onClose();
    } catch(err) { setError(errorMessage(err)); if (count) await refresh(); } finally { setBusy(false); }
  }
  return <Modal title="從 SSH config 匯入主機" onClose={() => !busy && onClose()}><div className="ssh-host-form">
    <p className="form-description">讀取目前執行 SRS 電腦的 ~/.ssh/config，或選擇你自己的設定檔。匯入前可確認連線欄位，再個別輸入密碼。</p>
    <Button disabled={busy} onClick={() => void load(true)}>讀取本機 SSH 設定</Button>
    <Field label="選擇 SSH config 檔案"><input type="file" disabled={busy} onChange={async e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 262144) { setError('檔案須小於 256 KB'); return; } setText(await file.text()); }} /></Field>
    <Field label="或貼上 SSH config"><textarea rows={5} value={text} disabled={busy} onChange={e=>setText(e.target.value)} placeholder={'Host lab-node\n  HostName 192.168.1.20\n  User researcher\n  Port 22'} /></Field>
    <Button disabled={busy || !text.trim()} onClick={()=>void load(false)}>預覽匯入內容</Button>
    {preview?.warnings.map(w=><Notice key={w}>{w}</Notice>)}
    {preview && !preview.items.length && <Notice>沒有可匯入的具名 Host，請確認設定檔內容。</Notice>}
    {preview?.items.map(item => { const done = saved.includes(item.display_name), exists = duplicate(item); return <div className="import-host" key={item.display_name}>
      <label className="checkbox-row"><input type="checkbox" checked={selected.includes(item.display_name)} disabled={busy || done || exists || !!item.problem} onChange={e=>setSelected(prev=>e.target.checked?[...prev,item.display_name]:prev.filter(x=>x!==item.display_name))}/><strong>{item.display_name}</strong></label>
      <p>{item.username}@{item.hostname}:{item.port}</p>{(item.problem || exists || done) && <small>{done?'已匯入':exists?'已有相同連線設定':item.problem}</small>}
      {selected.includes(item.display_name) && !done && <Field label={`${item.display_name} SSH 密碼`}><input type="password" autoComplete="new-password" disabled={busy} value={passwords[item.display_name] || ''} onChange={e=>setPasswords(p=>({...p,[item.display_name]:e.target.value}))}/></Field>}
    </div>; })}
    <InlineError message={error}/><div className="form-actions"><Button disabled={busy} onClick={onClose}>取消</Button><Button variant="primary" busy={busy} disabled={!selected.some(x=>!saved.includes(x)) || selected.some(x=>!saved.includes(x) && !passwords[x])} onClick={()=>void save()}>匯入所選主機並連線</Button></div>
  </div></Modal>;
}
