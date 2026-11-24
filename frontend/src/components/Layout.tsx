import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Download, Heart, Library, LayoutDashboard, Menu, Settings, Users, X, AlertTriangle } from 'lucide-react';
import { useMe } from '../auth';
import { LogoMark } from './ui';

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/subscriptions', label: 'Subscriptions', icon: Users },
  { to: '/likes', label: 'Liked videos', icon: Heart },
  { to: '/downloads', label: 'Downloads', icon: Download },
  { to: '/library', label: 'Library', icon: Library },
  { to: '/settings', label: 'Settings', icon: Settings },
];

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <ul className="space-y-1">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <li key={to}>
          <NavLink to={to} end={end} onClick={onNavigate}
            className={({ isActive }) => `flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium ${isActive ? 'bg-brand-600 text-white' : 'hover:bg-slate-200 dark:hover:bg-slate-800'}`}>
            <Icon size={18} aria-hidden /> {label}
          </NavLink>
        </li>
      ))}
    </ul>
  );
}

export function Layout() {
  const me = useMe();
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen md:flex">
      <aside className="hidden w-60 shrink-0 border-r border-slate-200 p-4 dark:border-slate-800 md:block">
        <div className="mb-6 flex items-center gap-2 text-xl font-bold text-brand-600"><LogoMark size={32} />DownTube</div>
        <nav aria-label="Main"><NavList /></nav>
        <p className="mt-6 truncate text-xs text-slate-500" title={me.email}>{me.email}</p>
      </aside>
      <header className="flex items-center justify-between border-b border-slate-200 p-3 dark:border-slate-800 md:hidden">
        <span className="flex items-center gap-2 text-lg font-bold text-brand-600"><LogoMark size={28} />DownTube</span>
        <button className="btn" aria-expanded={open} aria-controls="mobile-nav" aria-label={open ? 'Close menu' : 'Open menu'} onClick={() => setOpen(!open)}>
          {open ? <X size={18} /> : <Menu size={18} />}
        </button>
      </header>
      {open && (
        <nav id="mobile-nav" aria-label="Main" className="border-b border-slate-200 p-3 dark:border-slate-800 md:hidden">
          <NavList onNavigate={() => setOpen(false)} />
        </nav>
      )}
      <main className="min-w-0 flex-1 p-4 md:p-6">
        {me.account_status === 'reauth_required' && (
          <div role="alert" className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
            <AlertTriangle size={18} aria-hidden />
            <span>Your Google connection needs to be renewed. Syncing is paused until you reconnect.</span>
            <a className="btn btn-primary" href="/api/auth/login">Reconnect Google account</a>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
