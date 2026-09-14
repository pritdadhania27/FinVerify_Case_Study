import { NavLink, Outlet } from 'react-router-dom'

const LINKS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/documents', label: 'Documents' },
  { to: '/ask', label: 'Financial QA' },
  { to: '/verification', label: 'Verification' },
  { to: '/arms', label: 'Configurations' },
  { to: '/research', label: 'Research' },
]

export default function App() {
  return (
    <div className="shell">
      <header>
        <h1>FinVerify-AI</h1>
        <p className="sub">
          Dual-channel numerical QA over financial reports. The UI is secondary to
          the research engine.
        </p>
        <nav>
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end}>
              {link.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  )
}
