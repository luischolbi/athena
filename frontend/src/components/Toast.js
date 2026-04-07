import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';

let listeners = [];

export function showToast(message) {
  listeners.forEach(fn => fn(message));
}

export default function Toast() {
  const [toast, setToast] = useState(null);
  const [visible, setVisible] = useState(false);

  const handleShow = useCallback((message) => {
    setToast(message);
    // Force reflow before showing
    requestAnimationFrame(() => setVisible(true));
    setTimeout(() => {
      setVisible(false);
      setTimeout(() => setToast(null), 400);
    }, 3000);
  }, []);

  useEffect(() => {
    listeners.push(handleShow);
    return () => { listeners = listeners.filter(fn => fn !== handleShow); };
  }, [handleShow]);

  if (!toast) return null;

  return createPortal(
    <div
      className="fixed bottom-6 right-6 z-[200] transition-all duration-300 ease-out"
      style={{
        transform: visible ? 'translateX(0)' : 'translateX(calc(100% + 24px))',
        opacity: visible ? 1 : 0,
      }}
    >
      <div
        className="flex items-center gap-3 px-5 py-3.5 rounded-lg shadow-[0_8px_30px_rgba(0,0,0,0.5)]"
        style={{
          background: '#1e293b',
          borderLeft: '3px solid #4ade80',
        }}
      >
        <span className="text-emerald-400 text-[15px]">&#10003;</span>
        <span className="text-white text-[13px] font-medium">{toast}</span>
      </div>
    </div>,
    document.body
  );
}
