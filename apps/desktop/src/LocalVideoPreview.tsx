import { useEffect, useState } from 'react';

export function LocalVideoPreview({ file }: { file: File }) {
  const [url, setUrl] = useState('');
  useEffect(() => { const next = URL.createObjectURL(file); setUrl(next); return () => URL.revokeObjectURL(next); }, [file]);
  return url ? <video className="source-thumbnail" muted playsInline preload="metadata" src={url} aria-label={`素材预览 ${file.name}`} /> : null;
}
