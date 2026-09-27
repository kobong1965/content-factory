type IconName = 'analysis' | 'editing' | 'finished' | 'settings' | 'updates' | 'refresh' | 'plus';

const paths: Record<IconName, string> = {
  analysis: 'M4 4h16v16H4z M8 8h8 M8 12h5 M8 16h3',
  editing: 'M4 5h16v14H4z M4 9h16 M8 5l3 4 M14 5l3 4 M10 12l5 2-5 2z',
  finished: 'M3 6h7l2 2h9v12H3z M8 14l3 3 5-6',
  settings: 'M4 7h16 M4 17h16 M8 4v6 M16 14v6',
  updates: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
  refresh: 'M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-2l2 3 M4 16l2 3a7 7 0 0 0 12-2',
  plus: 'M12 5v14 M5 12h14',
};

export function StudioIcon({ name }: { name: IconName }) {
  return <svg className="studio-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
