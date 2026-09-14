import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom'

/**
 * The catch-all, and the router's error element.
 *
 * Both jobs are one component because both are "the app cannot show you the
 * screen you asked for", and the useful response is the same: say which screen,
 * say why, and offer the way back. Before this existed an unmatched hash
 * rendered the shell with an empty content area - no message, nav intact - which
 * reads as a page that failed to load rather than one that does not exist.
 *
 * As `errorElement` it also stops a render throw from replacing the entire
 * document with React Router's default stack trace. A research UI showing a
 * white page with a stack trace is indistinguishable, to a reader, from the
 * backend being down.
 */
export function RouteError() {
  const error = useRouteError()

  const [title, detail] = isRouteErrorResponse(error)
    ? [`${error.status} ${error.statusText}`, error.data ? String(error.data) : null]
    : ['This screen failed to render', error instanceof Error ? error.message : null]

  return (
    <section>
      <h2>{title}</h2>
      <p className="note">
        The failure is in the page itself, not in the API — the other screens are
        unaffected, and the run artifacts on disk are untouched either way.
      </p>
      {detail && (
        <div className="error">
          <strong>What went wrong</strong>
          <span>{detail}</span>
        </div>
      )}
      <p className="note">
        <Link to="/">← back to the dashboard</Link>
      </p>
    </section>
  )
}

export default function NotFound() {
  return (
    <section>
      <h2>No such screen</h2>
      <p className="note">
        This address does not match any of the six screens. It was probably a
        typed or truncated link — routing is hash-based, so everything after the{' '}
        <code>#</code> is the screen name.
      </p>
      <div className="panel">
        <ul className="reasons">
          <li>
            <Link to="/">Dashboard</Link> — what is reachable, and what has been
            measured
          </li>
          <li>
            <Link to="/documents">Documents</Link> — the filing corpus and its
            provenance
          </li>
          <li>
            <Link to="/ask">Financial QA</Link> — run a question through a
            configuration
          </li>
          <li>
            <Link to="/verification">Verification</Link> — inspect any recorded
            answer
          </li>
          <li>
            <Link to="/research">Research</Link> — per-arm metrics and the
            ablation
          </li>
          <li>
            <Link to="/arms">Configurations</Link> — what each arm removes
          </li>
        </ul>
      </div>
    </section>
  )
}
