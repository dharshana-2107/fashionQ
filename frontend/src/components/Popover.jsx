import { useEffect, useRef, useState } from "react";

// Minimal stand-in for st.popover: a button that toggles a floating panel.
// The panel closes on outside click, Escape, or when a child calls close().
export default function Popover({ label, children, align = "left" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="fq-popover" ref={ref}>
      <button type="button" className="fq-btn fq-btn-block" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {label} <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className={`fq-popover-panel fq-popover-${align}`} role="dialog">
          {typeof children === "function" ? children(() => setOpen(false)) : children}
        </div>
      )}
    </div>
  );
}
