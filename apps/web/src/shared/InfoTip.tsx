import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

/**
 * 물음표 버튼과 툴팁.
 *
 * 기준·출처처럼 "필요할 때만 확인하면 되는" 설명을 화면에서 접어 둔다. 지우는 게 아니라
 * 접는 것이라 근거 제공 규칙(screen_spec §4.2 · §8.3)은 그대로 지킨다.
 */
export function InfoTip({
  label,
  children,
  /** 화면 아래쪽에 있는 물음표는 위로 열어야 잘리지 않는다. */
  placement = 'down',
}: {
  label: string;
  children: ReactNode;
  placement?: 'down' | 'up';
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrapRef = useRef<HTMLSpanElement>(null);
  const panelRef = useRef<HTMLSpanElement>(null);

  // 물음표가 화면 어디에 있느냐에 따라 툴팁이 좌우로 삐져나간다. 열릴 때 실제로 재서
  // 넘친 만큼만 밀어 넣는다. 화면이 393px밖에 안 돼 여백을 미리 계산할 수 없다.
  useEffect(() => {
    const panel = panelRef.current;
    if (!open || !panel) return;

    const MARGIN = 8;
    panel.style.transform = '';
    const box = panel.getBoundingClientRect();
    const overflowRight = box.right - (window.innerWidth - MARGIN);
    const overflowLeft = MARGIN - box.left;
    const shift = overflowRight > 0 ? -overflowRight : overflowLeft > 0 ? overflowLeft : 0;
    if (shift) panel.style.transform = `translateX(${Math.round(shift)}px)`;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;

    function onPointerDown(event: PointerEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }

    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <span className="info-tip" ref={wrapRef}>
      <button
        type="button"
        className="info-tip__button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((current) => !current)}
      >
        ?
      </button>
      {open ? (
        <span className="info-tip__panel" data-placement={placement} id={id} role="note" ref={panelRef}>
          {children}
        </span>
      ) : null}
    </span>
  );
}
