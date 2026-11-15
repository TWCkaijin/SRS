import { useEffect, useState } from 'react';
import { Monitor, Moon, Sun } from 'lucide-react';
export type ThemeChoice = 'system' | 'light' | 'dark';
const key = 'srs-theme';
function stored(): ThemeChoice { try { const value=localStorage.getItem(key); return value==='light'||value==='dark'?value:'system'; } catch { return 'system'; } }
export function ThemeControl() {
  const [choice,setChoice]=useState<ThemeChoice>(stored);
  useEffect(()=>{
    const media=window.matchMedia('(prefers-color-scheme: dark)');
    const apply=()=>{
      const resolved=choice==='system'?(media.matches?'dark':'light'):choice;
      document.documentElement.dataset.theme=resolved;
      document.documentElement.style.colorScheme=resolved;
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content',resolved==='dark'?'#000000':'#f5f5f7');
    };
    apply(); media.addEventListener('change',apply);
    return()=>media.removeEventListener('change',apply);
  },[choice]);
  useEffect(()=>{const sync=(event:StorageEvent)=>{if(event.key===key||event.key===null)setChoice(stored());};window.addEventListener('storage',sync);return()=>window.removeEventListener('storage',sync);},[]);
  const Icon=choice==='system'?Monitor:choice==='dark'?Moon:Sun;
  return <label className="theme-control"><Icon size={16} aria-hidden="true"/><select aria-label="外觀模式" value={choice} onChange={event=>{const value=event.target.value as ThemeChoice;setChoice(value);try{localStorage.setItem(key,value);}catch{/* Private browsing may disable persistence. */}}}><option value="system">跟隨系統</option><option value="light">淺色模式</option><option value="dark">深色模式</option></select></label>;
}
