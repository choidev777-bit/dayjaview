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
}: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrapRef = useRef<HTMLSpanElement>(null);
  const panelRef = useRef<HTMLSpanElement>(null);

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
        <>
          {/* 화면이 갱신되면 툴팁이 물음표를 따라 움직여 읽기 어려웠다. 화면 아래에 고정해
              올라오게 하고, 바깥을 누르면 닫는다. */}
          <span className="info-tip__scrim" aria-hidden="true" />
          <span className="info-tip__panel" id={id} role="note" ref={panelRef}>
            {children}
          </span>
        </>
      ) : null}
    </span>
  );
}
