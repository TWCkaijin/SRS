import { useState, type FormEvent } from 'react';
import { FolderGit2, GitBranch, Plus, Save, X } from 'lucide-react';
import { post, errorMessage } from '../api';
import { useConsole } from '../context';
import { Button, EmptyState, Field, InlineError, KeyValue, Notice, PageIntro, Section } from '../ui';
import { profileLabels } from '../utils';

export function ProjectsPage() {
  const { data, refresh, notify, can } = useConsole();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [modes, setModes] = useState<string[]>(['cpu', 'single']);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError('');
    try { await post('/projects', { name: form.get('name'), path: form.get('path'), entrypoint: form.get('entrypoint'), modes, container: form.get('container'), revision: form.get('revision') }); await refresh(); setOpen(false); notify('專案已註冊。這個操作只儲存中繼資料，未連線至遠端。'); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <><PageIntro eyebrow="PROJECT REGISTRY" title="研究專案" description="登錄程式路徑、執行入口與版本，讓每次運算都能追溯到明確的來源。" action={can('project.manage') && <Button variant="primary" onClick={() => setOpen(!open)}>{open ? <X size={16} /> : <Plus size={16} />}{open ? '關閉表單' : '註冊專案'}</Button>} />
    {open && <Section title="註冊研究專案" className="form-panel"><form onSubmit={submit}><Notice>專案註冊只保存設定。路徑與環境是否可用，需另外建立盤點計畫並核准。</Notice><div className="form-grid"><Field label="專案名稱"><input name="name" required placeholder="例如：遙測影像分類" maxLength={120} /></Field><Field label="版本／Commit"><input name="revision" required placeholder="Git commit SHA 或固定版本" /></Field><Field label="專案絕對路徑"><input name="path" required placeholder="/srv/srs/projects/my-project" /></Field><Field label="訓練入口"><input name="entrypoint" required placeholder="train.py" /></Field><Field label="容器映像" className="span-2" hint="使用固定映像版本或 SIF 校驗值，避免漂移。"><input name="container" required placeholder="/srv/srs/images/research.sif" /></Field><fieldset className="field span-2"><legend className="field-label">允許的工作類型</legend><div className="checkbox-options">{Object.entries(profileLabels).map(([key, label]) => <label key={key} className="checkbox-row"><input type="checkbox" checked={modes.includes(key)} onChange={event => setModes(previous => event.target.checked ? [...previous, key] : previous.filter(item => item !== key))} />{label}</label>)}</div></fieldset></div><InlineError message={error} /><div className="form-actions"><Button type="button" onClick={() => setOpen(false)}>取消</Button><Button type="submit" variant="primary" busy={busy} disabled={!modes.length}><Save size={16} />儲存專案</Button></div></form></Section>}
    <div className="project-grid">{data.projects.map(project => <article className="panel project-card" key={project.id}><header><div className="project-icon"><FolderGit2 size={25} strokeWidth={1.5} /></div><span className="tag"><GitBranch size={12} />{project.revision?.slice(0, 14) || '未指定版本'}</span></header><h2>{project.name}</h2><dl><KeyValue label="路徑"><code>{project.path}</code></KeyValue><KeyValue label="執行入口"><code>{project.entrypoint}</code></KeyValue><KeyValue label="容器"><code>{project.container}</code></KeyValue></dl><div className="tag-row">{project.modes?.map(mode => <span className="tag" key={mode}>{profileLabels[mode] || mode}</span>)}</div></article>)}</div>{!data.projects.length && <Section title="專案清單"><EmptyState title="先註冊一個研究專案" description="建立工作草稿時，便能選擇固定版本的程式與容器。" /></Section>}</>;
}
