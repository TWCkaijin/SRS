import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CircleHelp } from 'lucide-react';

/** A hoverable, dismissible tooltip which also works with keyboard and touch. */
export function FieldHelp({ label, children }: { label: string; children: string }) {
  const id = useId();
  const anchor = useRef<HTMLButtonElement>(null);
  const bubble = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pinned = useRef(false);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 12, top: 12 });
  const cancel = () => clearTimeout(timer.current);
  const close = () => { cancel(); pinned.current = false; setOpen(false); };
  const show = () => { cancel(); setOpen(true); };
  const leave = () => { cancel(); if (!pinned.current && document.activeElement !== anchor.current) timer.current = setTimeout(() => setOpen(false), 160); };
  useEffect(() => () => clearTimeout(timer.current), []);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = anchor.current?.getBoundingClientRect();
      const tip = bubble.current?.getBoundingClientRect();
      if (!rect || !tip) return;
      const below = rect.bottom + 8;
      setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - tip.width - 12)), top: Math.max(12, below + tip.height <= window.innerHeight - 12 ? below : rect.top - tip.height - 8) });
    };
    place(); window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const keyboard = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); close(); } };
    const outside = (event: PointerEvent) => { if (!anchor.current?.contains(event.target as Node) && !bubble.current?.contains(event.target as Node)) close(); };
    document.addEventListener('keydown', keyboard, true); document.addEventListener('pointerdown', outside);
    return () => { document.removeEventListener('keydown', keyboard, true); document.removeEventListener('pointerdown', outside); };
  }, [open]);
  return <span className="field-help"><button ref={anchor} type="button" className="field-help-trigger" aria-label={`${label}說明`} aria-describedby={open ? id : undefined} aria-expanded={open} onMouseEnter={show} onMouseLeave={leave} onFocus={show} onBlur={() => { pinned.current = false; timer.current = setTimeout(() => setOpen(false), 160); }} onClick={() => { cancel(); pinned.current = !pinned.current; setOpen(pinned.current); }}><CircleHelp size={16} aria-hidden="true"/></button>
    {open && createPortal(<div ref={bubble} id={id} role="tooltip" className="field-help-bubble" style={position} onMouseEnter={cancel} onMouseLeave={leave}><strong>{label}</strong><p>{children}</p></div>, document.body)}
  </span>;
}
