import { useEffect, useRef, useState, type InputHTMLAttributes } from "react";

const mime: Record<string, string> = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", mp4: "video/mp4", mov: "video/quicktime", mkv: "video/x-matroska", webm: "video/webm", mp3: "audio/mpeg", wav: "audio/wav", pdf: "application/pdf" };
export function validateFiles(files: File[], accept: string, multiple: boolean): string | null {
  if (!multiple && files.length > 1) return "此入口一次接收 1 个文件，请分次上传。";
  const types = accept.toLowerCase().split(",").map(v => v.trim()).filter(Boolean);
  for (const file of files) {
    const name = file.name.toLowerCase();
    const type = file.type.toLowerCase() || mime[name.split(".").pop() ?? ""] || "";
    if (types.length && !types.some(t => t.startsWith(".") ? name.endsWith(t) : t.endsWith("/*") ? type.startsWith(t.slice(0, -1)) : type === t)) return `不支持 ${file.name}。请选择 ${accept}；本批文件未提交。`;
  }
  return null;
}

let mounted = 0;
function preventFileNavigation(event: DragEvent) {
  if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
}
export function FileDropInput({ ref, onChange, accept = "", multiple = false, disabled, ...props }: InputHTMLAttributes<HTMLInputElement> & { ref?: React.Ref<HTMLInputElement> }) {
  const input = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (mounted++ === 0) { document.addEventListener("dragover", preventFileNavigation); document.addEventListener("drop", preventFileNavigation); }
    return () => { if (--mounted === 0) { document.removeEventListener("dragover", preventFileNavigation); document.removeEventListener("drop", preventFileNavigation); } };
  }, []);
  return <span className={`file-drop-input${over ? " is-drag-over" : ""}`} onDragOver={e => { e.preventDefault(); if (!disabled) setOver(true); }} onDragLeave={() => setOver(false)} onDrop={e => {
    e.preventDefault(); e.stopPropagation(); setOver(false);
    if (disabled || !input.current) return;
    const problem = validateFiles(Array.from(e.dataTransfer.files), accept, multiple);
    setError(problem ?? ""); if (problem || !e.dataTransfer.files.length) return;
    input.current.files = e.dataTransfer.files;
    input.current.dispatchEvent(new Event("change", { bubbles: true }));
  }}>
    <input {...props} type="file" accept={accept} multiple={multiple} disabled={disabled} ref={node => { input.current = node; if (typeof ref === "function") ref(node); else if (ref) ref.current = node; }} onChange={e => {
      const problem = validateFiles(Array.from(e.target.files ?? []), accept, multiple);
      setError(problem ?? ""); if (!problem) onChange?.(e);
    }} />
    <small>{disabled ? "正在处理，请稍候" : "可拖动文件到此处，也可以点击选择"}</small>
    {error && <span role="alert">{error}</span>}
  </span>;
}
