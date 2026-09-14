export function ErrorBox({ title, message }: { title: string; message: string }) {
  return (
    <div className="error">
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  )
}

export function Loading({ what }: { what: string }) {
  return <p className="note">Loading {what}…</p>
}

export function Empty({ what, hint }: { what: string; hint?: string }) {
  return (
    <div className="panel">
      <p className="note" style={{ margin: 0 }}>
        No {what} yet.{hint ? ` ${hint}` : ''}
      </p>
    </div>
  )
}
