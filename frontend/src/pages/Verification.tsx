import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  api,
  formatAnchor,
  formatRisk,
  type AnswerOut,
  type VerificationOut,
} from '../lib/api'
import { Empty, ErrorBox, Loading } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * Screen 5 of spec §29: inspect a recorded answer's verification.
 *
 * The arbiter's ABSTENTION is displayed as a first-class outcome rather than as
 * a missing result. When the evidence does not settle a disagreement, declining
 * to resolve it is the correct behaviour, and a UI that showed "—" there would
 * present a deliberate refusal as a gap in the data.
 *
 * The arbiter panel distinguishes FOUR states that it previously collapsed into
 * one sentence. Reading only `triggered`, it asserted "Not triggered. The
 * arbiter runs only when the channels actually disagree" for every one of them —
 * including arms D and B1–B4, which have no arbiter and no consistency engine at
 * all, and including the case where the fetch simply failed. Claiming a
 * component declined to act when the component does not exist in that
 * configuration is the same class of error as reporting an unmeasured metric as
 * zero.
 */

const PAGE_SIZE = 200

function Arbiter({
  answer,
  verification,
  failed,
}: {
  answer: AnswerOut
  verification: VerificationOut | null
  failed: boolean
}) {
  const arm = answer.arm_config

  // 1. The configuration has no arbiter. Nothing declined; nothing exists.
  if (arm && !arm.uses_arbiter) {
    return (
      <p className="note" style={{ margin: 0 }}>
        <strong>Not part of this configuration.</strong> Arm {arm.name} runs
        without the verification agent
        {arm.uses_consistency ? '' : ' and without the consistency engine that would trigger it'}
        , so there is no arbiter decision to report — this is the arm's
        definition, not a missing record.
      </p>
    )
  }

  // 2. The lookup failed. Previously indistinguishable from "did not fire".
  if (failed) {
    return (
      <p className="note" style={{ margin: 0 }}>
        <strong>Could not be loaded.</strong> The arbiter record for this answer
        could not be fetched, so whether it fired is unknown. This is not the
        same as it having declined to act.
      </p>
    )
  }

  // 3. The arbiter exists and did not fire.
  if (!verification || !verification.triggered) {
    return (
      <p className="note" style={{ margin: 0 }}>
        <strong>Not triggered.</strong> The arbiter runs only when the channels
        actually disagree — an UNCERTAIN verdict is left standing, because an
        arbiter cannot adjudicate between an answer and an absence.
      </p>
    )
  }

  // 4. It fired.
  return verification.resolved ? (
    <>
      <p style={{ marginTop: 0 }}>
        Resolved as <span className="badge">{verification.resolution}</span>{' '}
        {verification.value && <>→ {verification.value}</>}
      </p>
      {verification.reasoning ? (
        <p className="note">{verification.reasoning}</p>
      ) : (
        <p className="note">
          The arbiter's reasoning was not recorded by this run, so only its
          decision is available.
        </p>
      )}
    </>
  ) : (
    <p className="note" style={{ margin: 0 }}>
      <strong>Abstained.</strong> The evidence did not settle the disagreement,
      so the arbiter declined to resolve it. That is a refusal to guess, not a
      failure — and the answer correctly stays flagged as risky.
    </p>
  )
}

export default function VerificationPage() {
  const [params, setParams] = useSearchParams()
  const [answerId, setAnswerId] = useState('')
  const [answer, setAnswer] = useState<AnswerOut | null>(null)
  const [verification, setVerification] = useState<VerificationOut | null>(null)
  const [verificationFailed, setVerificationFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const detailRef = useRef<HTMLDivElement | null>(null)

  // Filters live in the URL so a particular view is linkable and survives a
  // refresh - which matters because the interesting rows are found by filtering.
  const arm = params.get('arm') ?? ''
  const runId = params.get('run') ?? ''
  const urlSearch = params.get('q') ?? ''

  // The SEARCH BOX, unlike the two selects, is driven from local state.
  //
  // It used to read its value straight back out of the URL, and typing into it
  // DROPPED CHARACTERS: `setSearchParams` is asynchronous, so a keystroke
  // arriving before the previous navigation committed rebuilt the params from a
  // stale copy and snapped the input back. Typing "borrowings" at ordinary speed
  // left "browngs" in the box and filtered on that - a text field that quietly
  // rewrites what you typed, on the one control whose whole job is finding a row.
  //
  // Local state renders every keystroke immediately; the URL is written behind a
  // short debounce so the view stays linkable. `pushed` records the last value
  // this component sent to the URL, which is what tells an inbound URL change
  // (back, forward, a pasted link) apart from the echo of our own write - without
  // it, that echo would arrive late and overwrite newer keystrokes.
  const [search, setSearch] = useState(urlSearch)
  const pushed = useRef(urlSearch)

  useEffect(() => {
    if (urlSearch !== pushed.current) {
      pushed.current = urlSearch
      setSearch(urlSearch)
    }
  }, [urlSearch])

  useEffect(() => {
    if (search === pushed.current) return
    const timer = setTimeout(() => {
      pushed.current = search
      setFilter('q', search)
    }, 250)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  const recorded = useAsync(
    () => api.answers({ arm: arm || undefined, run_id: runId || undefined, limit: PAGE_SIZE }),
    [arm, runId],
  )
  const experiments = useAsync(() => api.experiments(), [])
  const arms = useAsync(() => api.arms(), [])

  const rows = useMemo(() => {
    const all = recorded.data ?? []
    if (!search.trim()) return all
    const needle = search.trim().toLowerCase()
    return all.filter(
      (r) =>
        r.question.toLowerCase().includes(needle) ||
        r.question_id.toLowerCase().includes(needle) ||
        (r.answer_text ?? '').toLowerCase().includes(needle),
    )
  }, [recorded.data, search])

  // Updated from the PREVIOUS params, not from the render's captured copy. Two
  // filter writes landing in one batch each rebuilt from the same stale snapshot,
  // so the second silently discarded the first.
  function setFilter(key: string, value: string) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  async function load(id: number) {
    if (!Number.isInteger(id) || id <= 0) {
      setError('Answer id must be a positive integer.')
      return
    }
    setBusy(true)
    setError(null)
    setAnswer(null)
    setVerification(null)
    setVerificationFailed(false)
    try {
      const loaded = await api.answer(id)
      // Fetched separately and its failure RECORDED, not swallowed: a 404 here
      // used to render as the positive claim "the arbiter did not fire".
      let record: VerificationOut | null = null
      let failed = false
      try {
        record = await api.verification(id)
      } catch {
        failed = true
      }
      setAnswerId(String(id))
      setAnswer(loaded)
      setVerification(record)
      setVerificationFailed(failed)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // Deep-linkable: /#/verification?answer=1598 opens that answer directly, which
  // is what the cross-arm question view links to.
  //
  // This is now the ONLY way the detail opens. The Inspect button and the
  // look-up-by-id form used to call `load` straight, leaving the URL untouched -
  // so an answer opened from the table was the one view on this page that was
  // not linkable, did not survive a refresh, and could not be closed with Back,
  // while the identical detail reached from the question screen was all three.
  const requested = params.get('answer')
  useEffect(() => {
    if (requested) void load(Number(requested))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requested])

  /** Opens an answer by putting it in the URL and letting the effect above load it. */
  function open(id: number) {
    if (String(id) === requested) {
      // Same row re-clicked: the effect will not re-fire, so without this the
      // button does nothing visible when the detail is scrolled off-screen.
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    setFilter('answer', String(id))
  }

  // The detail renders below a 200-row table, so without this the button
  // appeared to do nothing at all - the result was thousands of pixels down.
  useEffect(() => {
    if (answer) detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [answer])

  const truncated = (recorded.data?.length ?? 0) >= PAGE_SIZE

  return (
    <section>
      <h2>Verification</h2>
      <p className="note">
        Pick a recorded answer to see the arbiter's decision on it. Answers come
        from ingested run artifacts, which remain the source of truth on disk.
      </p>

      <div className="filters">
        <div>
          <label htmlFor="f-arm">Configuration</label>
          <select id="f-arm" value={arm} onChange={(e) => setFilter('arm', e.target.value)}>
            <option value="">every arm</option>
            {(arms.data ?? []).map((a) => (
              <option key={a.name} value={a.name}>
                {a.name} — {a.removes ? `minus ${a.removes}` : 'full system'}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="f-run">Run</label>
          <select id="f-run" value={runId} onChange={(e) => setFilter('run', e.target.value)}>
            <option value="">every run</option>
            {(experiments.data ?? [])
              .filter((r) => r.answers > 0)
              .map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} ({r.answers})
                </option>
              ))}
          </select>
        </div>
        <div>
          <label htmlFor="f-q">Search question, id or answer</label>
          <input
            id="f-q"
            type="search"
            value={search}
            placeholder="Tata Motors, borrowings, FI82a9…"
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      <h3>Recorded answers</h3>
      {recorded.loading && <Loading what="recorded answers" />}
      {recorded.error && <ErrorBox title="Could not load answers" message={recorded.error} />}
      {recorded.data && rows.length === 0 && (
        <Empty
          what="answers matching these filters"
          hint="Clear the search, or pick a different arm or run."
        />
      )}

      {rows.length > 0 && (
        <>
          <p className="note">
            Showing {rows.length}
            {search.trim() ? ` of ${recorded.data?.length ?? 0} loaded` : ''} answer
            {rows.length === 1 ? '' : 's'}
            {truncated && (
              <>
                {' '}— the newest {PAGE_SIZE} of this selection. Narrow by arm or run
                to reach older rows.
              </>
            )}
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Question</th>
                  <th>Arm</th>
                  <th>Answer</th>
                  <th>Verdict</th>
                  <th className="num">Risk</th>
                  <th>Graded</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.answer_id}>
                    {/* Not truncated. Every gold question opens with the same
                        stem ("For <Company> Limited, as reported for the year
                        ended March 31, …"), so a 64-character cut landed inside
                        the boilerplate and collapsed 45 distinct questions into
                        6 displayed strings - 199 of 200 rows rendered as
                        byte-identical text in the column meant to identify them.
                        The qid is shown too: it is short, unique, and the handle
                        the cross-arm view is keyed on. */}
                    <td>
                      <Link to={`/questions/${row.question_id}`}>
                        <code>{row.question_id}</code>
                      </Link>{' '}
                      {row.question}
                    </td>
                    <td>
                      <span className="badge arm">{row.arm}</span>
                    </td>
                    <td>
                      {row.answer_text ?? (
                        <span className="undefined-value">
                          {row.abstained ? 'abstained' : 'no answer'}
                        </span>
                      )}
                    </td>
                    <td>
                      {/* A null verdict is not a missing datum: the arm has no
                          consistency engine, so no cross-check exists. */}
                      {row.verdict ?? (
                        <span className="undefined-value">no cross-check</span>
                      )}
                    </td>
                    <td className="num">
                      <span className={`pill risk-${row.band}`}>
                        {formatRisk(row.risk_score)}
                      </span>
                    </td>
                    <td>
                      {row.correct === null ? (
                        <span className="undefined-value">not graded</span>
                      ) : row.correct ? (
                        <span className="risk-LOW">correct</span>
                      ) : (
                        <span className="risk-HIGH">incorrect</span>
                      )}
                    </td>
                    <td>
                      <button type="button" disabled={busy} onClick={() => open(row.answer_id)}>
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          const id = Number(answerId)
          // Checked before it reaches the URL, so a typo does not become a
          // linkable address. `load` keeps its own guard for a pasted one.
          if (!Number.isInteger(id) || id <= 0) {
            setError('Answer id must be a positive integer.')
            return
          }
          open(id)
        }}
        style={{ maxWidth: 320 }}
      >
        <label htmlFor="answer-id">…or look one up by id</label>
        <input
          id="answer-id"
          value={answerId}
          onChange={(e) => setAnswerId(e.target.value)}
          inputMode="numeric"
          placeholder="1"
        />
        <button type="submit" disabled={busy}>
          {busy ? 'Loading…' : 'Look up'}
        </button>
      </form>

      {error && <ErrorBox title="Not found" message={error} />}

      {answer && (
        <div ref={detailRef}>
          <h3>Answer</h3>
          <div className="panel">
            <p style={{ marginTop: 0 }}>{answer.question}</p>
            {answer.arm_config && (
              <p className="note">
                Arm <span className="badge arm">{answer.arm_config.name}</span>{' '}
                {answer.arm_config.description}
                {answer.arm_config.removes && (
                  <> — arm A minus {answer.arm_config.removes}.</>
                )}
              </p>
            )}
            <div className="grid">
              <div className="stat">
                <div className="label">Answer</div>
                <div className="value text">
                  {answer.answer_text ?? (
                    <span className="undefined-value">
                      {answer.abstained ? 'abstained' : 'none'}
                    </span>
                  )}
                </div>
              </div>
              <div className="stat">
                <div className="label">Verdict</div>
                <div className="value text">
                  {answer.verdict ?? (
                    <span className="undefined-value">no cross-check in this arm</span>
                  )}
                </div>
              </div>
              <div className="stat">
                <div className="label">Risk</div>
                <div className="value text">
                  <span className={`pill risk-${answer.band}`}>
                    {formatRisk(answer.risk_score)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <h3>What each channel said</h3>
          {answer.channels.length === 0 ? (
            <Empty what="channel records" hint="This run did not record per-channel output." />
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Channel</th>
                    <th>Reported</th>
                    <th className="num">In base units</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {answer.channels.map((channel) => (
                    <tr key={channel.name}>
                      <td>{channel.name}</td>
                      <td>
                        {channel.value ?? <span className="undefined-value">no figure</span>}
                      </td>
                      {/* The canonical form is the column that makes a SCALE
                          disagreement visible. Two channels can report the same
                          magnitude - "12232" and "12232" - while meaning crore
                          and million; without this the hundredfold difference
                          renders as two identical numbers. */}
                      <td className="num">
                        {channel.canonical ?? <span className="undefined-value">—</span>}
                      </td>
                      <td>
                        {channel.available ? (
                          'answered'
                        ) : (
                          <span className="undefined-value">
                            {channel.failure_reason ?? 'unavailable'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3>Arbiter</h3>
          <div className="panel">
            <Arbiter answer={answer} verification={verification} failed={verificationFailed} />
          </div>

          <h3>Why this answer carries the risk it does</h3>
          {answer.explanation ? (
            <div className="panel">
              <ul className="reasons">
                {answer.explanation.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
              {answer.explanation.caveats.length > 0 && (
                <>
                  <p className="note" style={{ marginBottom: 4 }}>
                    <strong>Caveats</strong>
                  </p>
                  <ul className="reasons">
                    {answer.explanation.caveats.map((caveat) => (
                      <li key={caveat}>{caveat}</li>
                    ))}
                  </ul>
                </>
              )}
              {answer.explanation.citations.length > 0 && (
                <>
                  <p className="note" style={{ marginBottom: 4 }}>
                    <strong>Evidence the answer was grounded in</strong>
                  </p>
                  <ul className="reasons">
                    {answer.explanation.citations.map((citation) => (
                      <li key={citation}>{citation}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ) : (
            <div className="panel">
              <p className="note" style={{ margin: 0 }}>
                <strong>This run recorded no explanation.</strong> Runs before
                2026-09-07 did not persist Module 16's output, so there is nothing
                to show — which is different from an answer having been produced
                without one. Runs from the B–F ablation onward carry it.
              </p>
            </div>
          )}

          <h3>Gold evidence for this question</h3>
          {answer.evidence[0]?.document && (
            <p className="note">
              From <strong>{answer.evidence[0].document}</strong>.
            </p>
          )}
          {answer.evidence.length === 0 ? (
            <Empty
              what="gold evidence"
              hint={
                // A live question has no gold by construction; saying "no spans
                // are recorded" would read as a gap in the benchmark.
                answer.question_id?.startsWith('LIVE-')
                  ? 'This question was asked live, so there is no gold answer or gold evidence to compare it against. The evidence it retrieved is listed above.'
                  : 'No validated evidence spans are recorded for this question.'
              }
            />
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Ref</th>
                    <th>Citation</th>
                    <th className="num">Page</th>
                    <th>Anchor text</th>
                  </tr>
                </thead>
                <tbody>
                  {answer.evidence.map((span, index) => (
                    <tr key={`${span.ref}-${span.page}-${index}`}>
                      <td>
                        <code>{span.ref}</code>
                      </td>
                      <td>{span.citation}</td>
                      <td className="num">{span.page ?? '—'}</td>
                      <td>
                        {formatAnchor(span.text) ?? (
                          <span className="undefined-value">not recorded</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
