import { useCallback, useEffect, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

/**
 * Load once and expose loading/error explicitly.
 *
 * `error` is a rendered string rather than a swallowed console log: the API's
 * refusals carry the reason a user needs ("live QA is disabled because it
 * spends campaign quota"), and a blank screen would hide exactly the message
 * worth reading.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    // Cleared, not merely overwritten on success. Without this the PREVIOUS
    // request's payload stays on screen while the next one is in flight, so
    // selecting a different run on the research screen showed the old run's
    // metrics under the new run's name - and if the new request then failed,
    // they stayed there indefinitely beside an error. Stale data labelled as
    // current is worse than no data.
    setData(null)
    run()
      .then((value) => {
        // `live` guards the unmount case AND the out-of-order case: when deps
        // change quickly the cleanup runs before the older promise settles, so
        // a slow first response can no longer overwrite a fast second one.
        if (live) setData(value)
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (live) setLoading(false)
      })
    return () => {
      live = false
    }
  }, [run, nonce])

  return { data, error, loading, reload: () => setNonce((n) => n + 1) }
}
