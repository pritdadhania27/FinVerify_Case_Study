import { Link, useParams } from 'react-router-dom'
import { ApiError, api, formatAnchor, formatRisk, type AnswerSummaryOut } from '../lib/api'
import { Empty, ErrorBox, Loading } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * One question, every configuration that answered it.
 *
 * This is the contrast the whole project is built on — "the same question, one
 * field changed" — and it was the one view the UI could not produce. Reaching it
 * meant pulling 1,646 rows and sifting them by hand, so the comparison the
 * research is about was invisible in the research tool.
 *
 * Two things it must not do, both of which would quietly invalidate what it
 * shows:
 *
 * 1. **Never compare across channel bindings.** Rows are grouped by run, and
 *    each group states its binding. Arms A/G/H ran on a model that has since
 *    been retired while B–F ran on another; a table that stacked them would
 *    show a model swap as though it were a component effect (RX-047).
 * 2. **Never present agreement as correctness.** The blind spot — both channels
 *    agreeing on a wrong figure — is the failure mode the method cannot catch by
 *    construction, so it is called out rather than left for the reader to spot.
 */

function Verdict({ row }: { row: AnswerSummaryOut }) {
  if (row.correct === null) return <span className="undefined-value">not graded</span>
  return row.correct ? (
    <span className="risk-LOW">correct</span>
  ) : (
    <span className="risk-HIGH">incorrect</span>
  )
}

export default function QuestionPage() {
  const { qid = '' } = useParams()
  const answers = useAsync(() => api.answers({ question_id: qid, limit: 1000 }), [qid])
  // Only a 404 is swallowed, and only because it genuinely means "this question
  // has no validated spans". Catching everything turned an unreachable API or a
  // 500 into the empty state, which reads as the positive claim "no gold
  // evidence was recorded for this question" - the same bug the arbiter panel
  // on Verification already had to have fixed once.
  const evidence = useAsync(
    () =>
      api.evidence(qid).catch((e) => {
        if (e instanceof ApiError && e.status === 404) return []
        throw e
      }),
    [qid],
  )
  const experiments = useAsync(() => api.experiments(), [])

  const bindingOf = new Map((experiments.data ?? []).map((r) => [r.run_id, r]))
  const rows = answers.data ?? []
  const question = rows[0]?.question ?? ''

  // Grouped by run: a contrast is only meaningful within one binding.
  const byRun = new Map<string, AnswerSummaryOut[]>()
  for (const row of rows) {
    if (!byRun.has(row.run_id)) byRun.set(row.run_id, [])
    byRun.get(row.run_id)!.push(row)
  }
  for (const list of byRun.values()) list.sort((a, b) => a.arm.localeCompare(b.arm))

  const blindSpot = rows.filter((r) => r.agreed === true && r.correct === false)
  const graded = rows.filter((r) => r.correct !== null)
  const right = graded.filter((r) => r.correct)

  return (
    <section>
      <h2>Question</h2>
      <p className="note">
        <Link to="/verification">← all recorded answers</Link>
      </p>

      {answers.loading && <Loading what="the answers for this question" />}
      {answers.error && <ErrorBox title="Could not load answers" message={answers.error} />}
      {answers.data && rows.length === 0 && (
        <Empty
          what="answers"
          hint={`No ingested run recorded an answer for ${qid}.`}
        />
      )}

      {rows.length > 0 && (
        <>
          <div className="panel">
            <p style={{ marginTop: 0 }}>{question}</p>
            <p className="note" style={{ marginBottom: 0 }}>
              <code>{qid}</code> · answered by {rows.length} configuration
              {rows.length === 1 ? '' : 's'} across {byRun.size} run
              {byRun.size === 1 ? '' : 's'}
            </p>
          </div>

          <div className="grid">
            <div className="panel stat">
              <div className="label">Graded correct</div>
              <div className="value">
                {graded.length ? `${right.length}/${graded.length}` : '—'}
              </div>
              <div className="sub-value">
                {rows.length - graded.length > 0
                  ? `${rows.length - graded.length} not graded`
                  : 'every recorded answer is graded'}
              </div>
            </div>
            <div className="panel stat">
              <div className="label">Blind spot</div>
              <div className="value">{blindSpot.length}</div>
              <div className="sub-value">
                channels agreed and were wrong
              </div>
            </div>
            <div className="panel stat">
              <div className="label">Abstained</div>
              <div className="value">{rows.filter((r) => r.abstained).length}</div>
              <div className="sub-value">declined to answer</div>
            </div>
          </div>

          {blindSpot.length > 0 && (
            <div className="error">
              <strong>
                {blindSpot.length} configuration{blindSpot.length === 1 ? '' : 's'} agreed
                on a wrong figure
              </strong>
              <span>
                Both channels concurred and both were wrong ({blindSpot
                  .map((r) => r.arm)
                  .join(', ')}
                ). Cross-channel agreement cannot catch this case by construction —
                it is the method's blind spot, not a bug in it.
              </span>
            </div>
          )}

          {[...byRun.entries()].map(([runId, list]) => {
            const meta = bindingOf.get(runId)
            return (
              <div key={runId}>
                <h3>
                  {runId} <span className="badge">{meta?.split ?? 'split not recorded'}</span>
                </h3>
                <p className="note">
                  {meta?.independence ??
                    'Channel binding not recorded for this run — its rows cannot be contrasted against another run.'}
                </p>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Arm</th>
                        <th>Answer</th>
                        <th>Verdict</th>
                        <th>Agreed</th>
                        <th className="num">Risk</th>
                        <th>Graded</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((row) => (
                        <tr key={row.answer_id}>
                          <td>
                            <span className="badge arm">{row.arm}</span>
                          </td>
                          <td>
                            {/* answer_text, so the unit travels with the figure. */}
                            {row.answer_text ?? (
                              <span className="undefined-value">
                                {row.abstained ? 'abstained' : 'no answer'}
                              </span>
                            )}
                          </td>
                          <td>
                            {row.verdict ?? (
                              <span className="undefined-value">no cross-check</span>
                            )}
                          </td>
                          <td>
                            {row.agreed === null ? (
                              <span className="undefined-value">n/a</span>
                            ) : row.agreed ? (
                              'yes'
                            ) : (
                              'no'
                            )}
                          </td>
                          <td className="num">
                            <span className={`pill risk-${row.band}`}>
                              {formatRisk(row.risk_score)}
                            </span>
                          </td>
                          <td>
                            <Verdict row={row} />
                          </td>
                          <td>
                            <Link to={`/verification?answer=${row.answer_id}`}>inspect</Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}

          <h3>Gold evidence</h3>
          {evidence.loading && <Loading what="gold evidence" />}
          {evidence.error && (
            <ErrorBox title="Could not load gold evidence" message={evidence.error} />
          )}
          {evidence.data && evidence.data.length === 0 && (
            <Empty
              what="gold evidence"
              hint="This question has no validated evidence spans recorded, so retrieval cannot be scored against it."
            />
          )}
          {evidence.data && evidence.data.length > 0 && (
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
                  {evidence.data.map((span, index) => (
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
        </>
      )}
    </section>
  )
}
