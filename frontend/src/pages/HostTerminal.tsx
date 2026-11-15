import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { api, errorMessage, post } from '../api';
import { useConsole } from '../context';
import type { Node } from '../types';
import { Button, Field, InlineError, Loading, Modal } from '../ui';

export function HostTerminal({ node, onClose }: { node: Node; onClose: () => void }) {
  const { session } = useConsole();
  const element = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState('連線中…');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true, ready = false;
    const terminal = new Terminal({ cursorBlink:true, fontSize:14, fontFamily:'Menlo, monospace', theme:{background:'#0b0f17',foreground:'#e2e8f0',cursor:'#5eead4',selectionBackground:'#334155'}, scrollback:3000 });
    const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(element.current!); fit.fit();
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/v1/ssh-hosts/${encodeURIComponent(node.id)}/terminal`);
    setStatus('連線中…');
    const resize = () => { fit.fit(); if (ready && socket.readyState===WebSocket.OPEN) socket.send(JSON.stringify({type:'resize',cols:terminal.cols,rows:terminal.rows})); };
    socket.onopen = () => socket.send(JSON.stringify({csrf:session.csrf_token,revision:node.ssh_connection?.revision}));
    socket.onmessage = event => { if(!alive) return; const data=JSON.parse(event.data); if(data.type==='ready') { ready=true; setStatus('已連線'); resize(); terminal.focus(); } else if(data.type==='output') terminal.write(data.data); else if(data.type==='error') {setStatus(data.message);terminal.writeln('\r\n'+data.message);} };
    socket.onclose = () => {ready=false;if(alive)setStatus(previous=>previous==='已連線'||previous==='連線中…'?'連線已關閉':previous);};
    socket.onerror = () => {if(alive)setStatus('無法連線，請確認服務與 SSH 設定。');};
    const input=terminal.onData(data=>{if(ready&&socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'input',data}));});
    const observer = new ResizeObserver(resize); observer.observe(element.current!);
    return () => {alive=false;observer.disconnect();input.dispose();socket.close();terminal.dispose();};
  }, [node.id,node.ssh_connection?.revision,session.csrf_token,attempt]);
  return <Modal title={`終端 · ${node.ssh_connection?.display_name || node.alias}`} onClose={onClose} closeOnEscape={false}><div className="terminal-panel"><div className="terminal-status"><span>{node.ssh_connection?.username}@{node.ssh_connection?.hostname} · {status}</span><Button onClick={()=>setAttempt(x=>x+1)}>重新連線</Button></div><div className="web-terminal"><div className="terminal-mount" ref={element} aria-label="SSH 互動終端" /></div><p className="field-hint">指令會直接在此主機執行。關閉視窗會中斷連線；長時間工作請使用 tmux。Esc 保留給終端程式。</p></div></Modal>;
}

