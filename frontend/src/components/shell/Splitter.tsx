// Poignée de redimensionnement d'un panneau (glisser ; double-clic : taille par défaut).
export function Splitter({ dir, onDrag, onReset, label }: { dir: "v" | "h"; onDrag: (delta: number, first: boolean) => void; onReset: () => void; label: string }) {
  const start = (e: React.MouseEvent) => {
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY;
    let first = true;
    document.body.classList.add(dir === "v" ? "resizing-v" : "resizing-h");
    const move = (ev: MouseEvent) => { onDrag(dir === "v" ? ev.clientX - x0 : ev.clientY - y0, first); first = false; };
    const up = () => {
      document.body.classList.remove("resizing-v", "resizing-h");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
  return <div className={`splitter ${dir}`} role="separator" aria-label={label} aria-orientation={dir === "v" ? "vertical" : "horizontal"}
    title={`${label} (double-clic : taille par défaut)`} onMouseDown={start} onDoubleClick={onReset} />;
}
