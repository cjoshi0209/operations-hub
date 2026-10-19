import { useEffect, useRef, useState } from "react";
import { Link, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { useNotifications } from "./api/hooks";
import { useAuth } from "./auth";
import { Spinner } from "./components/common";
import { timeAgo } from "./lib/format";
import ItemDetail from "./pages/ItemDetail";
import Login from "./pages/Login";
import Overview from "./pages/Overview";

const SEEN_KEY = "ops-hub-notifications-seen";
const readSeen = (): number => {
  try {
    return Number(localStorage.getItem(SEEN_KEY) ?? 0);
  } catch {
    return 0;
  }
};

function Notifications() {
  const { data } = useNotifications();
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(readSeen);
  const items = data?.items ?? [];
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const unread = items.filter((n) => n.id > seen).length;

  function toggle() {
    if (!open && items.length) {
      const top = items[0].id;
      setSeen(top);
      try {
        localStorage.setItem(SEEN_KEY, String(top));
      } catch {
        /* ignore */
      }
    }
    setOpen(!open);
  }

  return (
    <div className="notif" ref={ref}>
      <button onClick={toggle} aria-label={`Notifications${unread ? `, ${unread} new` : ""}`} aria-expanded={open}>
        🔔 {unread > 0 && <span className="badge-count">{unread}</span>}
      </button>
      {open && (
        <div className="notif-panel card">
          <strong>Notifications</strong>
          {items.length === 0 && <p className="muted small">Nothing yet.</p>}
          <ul>
            {items.map((n) => (
              <li key={n.id}>
                <Link to={`/items/${n.work_item_id}`} onClick={() => setOpen(false)}>
                  {n.message}
                </Link>
                <div className="muted small">{timeAgo(n.created_at)}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Shell() {
  const { me, loading, logout } = useAuth();
  if (loading) return <Spinner />;
  if (!me) return <Navigate to="/login" replace />;
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <nav className="topbar" aria-label="Primary">
        <Link to="/" className="brand">
          Operations Hub
        </Link>
        <span className="spacer" />
        <Notifications />
        <span className="who" title={me.memberships.map((m) => `${m.team.name}${m.role === "lead" ? " (lead)" : ""}`).join(", ")}>
          <strong>{me.user.name}</strong>
          <span className="muted teams">
            {me.memberships.map((m) => `${m.team.name}${m.role === "lead" ? " (lead)" : ""}`).join(", ")}
          </span>
        </span>
        <button onClick={logout}>Sign out</button>
      </nav>
      <main className="page" id="main">
        <Outlet />
      </main>
    </>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Shell />}>
        <Route path="/" element={<Overview />} />
        <Route path="/items/:id" element={<ItemDetail />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
