import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, formatRisk, type AnswerOut, type ArmOut } from '../lib/api'
import { ErrorBox } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * The picker is served from `/arms`, not hard-coded here.
 *
 * It used to be a literal list of seven, against the fourteen configurations
 * `evaluation/arms.py` defines and `POST /questions/ask` accepts. Two separate
 * faults: the single-field ablation arms B-F - the ones the entire research
 * design rests on, and the ones the Configurations screen explains - could not
 * be selected at all on the only screen that runs anything; and a second copy
 * of the arm table in the UI is a copy that drifts from the one the campaign
 * ran, silently, the first time an arm is added or renamed.
 */
function armLabel(arm: ArmOut): string {
  if (arm.removes) return `${arm.name} — minus ${arm.removes}`
  return `${arm.name} — ${arm.description}`
}

/** Baselines differ from A in many fields at once; ablations in exactly one. */
function armGroup(name: string): string {
  if (name === 'A') return 'Proposed system'
  if (name.startsWith('B') && name.length > 1) return 'Baselines — not part of the ablation'
  if (name === 'O') return 'Diagnostic — given the gold evidence'
  return 'Ablations — arm A minus one component'
}

const GROUP_ORDER = [
  'Proposed system',
  'Ablations — arm A minus one component',
  'Baselines — not part of the ablation',
  'Diagnostic — given the gold evidence',
]

/**
 * Screens 4 and 5 of spec §29.
 *
 * Three things this screen must not do, each of which would misrepresent the
 * system it is a window onto:
 *
 * 1. Show a bare number. `answer_text` carries the unit; 3956 next to a gold of
 *    3,956 crore looks like a match and is a hundredfold error.
 * 2. Invent a risk score. Arms B1-B4 have no detector, and the display says
 *    "not scored" rather than showing a neutral-looking 0.5.
 * 3. Present the score as a probability. It is uncalibrated and ranks answers;
 *    the caveat travels with it.
 */
export default function AskPage() {
  const [question, setQuestion] = useState('')
  const [arm, setArm] = useState('A')
  // null = the user has not chosen, so the first filing is used.
  const [documentId, setDocumentId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<AnswerOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Checked here and not only on the dashboard. This is the screen where the
  // switch actually bites, and a user who lands on it directly would otherwise
  // compose a question and meet a 503 - which reads as a broken deployment
  // rather than as the deliberate guard on the campaign's quota.
  const health = useAsync(() => api.health(), [])
  const arms = useAsync(() => api.arms(), [])
  const documents = useAsync(() => api.documents(), [])
  // Defaults to FALSE when the state is unknown. `?? true` assumed the endpoint
  // was open whenever /health had not answered - so before the response landed,
  // and permanently if it errored, the button was enabled and no warning showed.
  // The failure mode of guessing "on" is spending campaign quota; of guessing
  // "off" it is a disabled button next to an explanation. Only one of those is
  // recoverable.
  const live = health.data?.live_questions_enabled === true

  // This screen used to send only the question and the arm. Retrieval is scoped
  // by company (RX-012) and the question parser does not extract one from free
  // text, so every question asked here searched all filings at once - the
  // configuration this project measured as not working. The default is
  // therefore a real filing, and "all filings" is an explicit, labelled choice.
  const selectedId = documentId ?? documents.data?.[0]?.document_id ?? ''
  const selectedDocument = documents.data?.find((d) => d.document_id === selectedId) ?? null

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setAnswer(null)
    try {
      setAnswer(
        await api.ask({
          question,
          arm,
          document_id: selectedId || undefined,
          company: selectedDocument?.company ?? undefined,
        }),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Financial QA</h2>
      <p className="note">
        Each question runs the selected configuration end to end — retrieval, two
        independent reasoning channels, a sandboxed program, the consistency check
        and, when the channels disagree, the arbiter — and spends free-tier LLM
        quota.
      </p>

      {health.error && (
        <ErrorBox
          title="Could not check whether asking is enabled"
          message={`${health.error} — the Ask button stays disabled while this is unknown, because the alternative is spending quota on a guess.`}
        />
      )}

      {!health.loading && !health.error && !live && (
        <div className="panel">
          <strong>Asking is switched off on this API.</strong>
          <p className="note" style={{ marginBottom: 0 }}>
            Each question runs both channels and can trigger the arbiter, so the
            endpoint stays off unless it is turned on deliberately. Start the demo
            with <code>RUN_LIVE_DEMO.bat</code>, which runs the API with the research
            engine and <code>FINVERIFY_ENABLE_LIVE_QA=1</code>. Recorded answers
            from the campaigns are inspectable on{' '}
            <Link to="/verification">Verification</Link>, and they are the ones the
            results are computed from.
          </p>
        </div>
      )}

      <form onSubmit={onSubmit}>
        <label htmlFor="filing">Filing</label>
        <select
          id="filing"
          value={selectedId}
          onChange={(e) => setDocumentId(e.target.value)}
          disabled={documents.loading}
        >
          {documents.loading && <option value="">Loading filings…</option>}
          {(documents.data ?? []).map((doc) => (
            <option key={doc.document_id} value={doc.document_id}>
              {doc.company ?? doc.filename}
              {doc.fiscal_year ? ` — FY ${doc.fiscal_year}` : ''}
            </option>
          ))}
          {!documents.loading && (
            <option value="">All filings at once (unscoped — retrieval is much weaker)</option>
          )}
        </select>
        {documents.error && (
          <p className="note">
            The filing list could not be loaded ({documents.error}), so a question
            asked now searches every filing at once.
          </p>
        )}

        <label htmlFor="q">Question</label>
        <textarea
          id="q"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What were diluted earnings per share for the year ended March 31, 2023?"
          required
          minLength={3}
        />
        <label htmlFor="arm">Configuration</label>
        <select id="arm" value={arm} onChange={(e) => setArm(e.target.value)}>
          {/* Arm A is always offered, so the control is usable before /arms
              answers and still usable if it fails - the alternative is an empty
              picker above an enabled button. */}
          {arms.data === null || arms.data.length === 0 ? (
            <option value="A">A — full system</option>
          ) : (
            GROUP_ORDER.filter((group) =>
              arms.data!.some((a) => armGroup(a.name) === group),
            ).map((group) => (
              <optgroup key={group} label={group}>
                {arms.data!
                  .filter((a) => armGroup(a.name) === group)
                  .map((option) => (
                    <option key={option.name} value={option.name}>
                      {armLabel(option)}
                    </option>
                  ))}
              </optgroup>
            ))
          )}
        </select>
        <p className="note">
          {arms.error
            ? `The configuration list could not be loaded (${arms.error}), so only arm A is offered.`
            : 'Served from the same arm table the campaign runs, so this list cannot drift from it. '}
          {!arms.error && <Link to="/arms">What each configuration is</Link>}
        </p>
        <button
          type="submit"
          disabled={busy || !live || documents.loading || question.trim().length < 3}
        >
          {/* "Checking" while /health is in flight. The label used to read
              "Asking is disabled" during that second, with no explanation beside
              it - the panel only appears once the check has answered - which
              looked like a broken deployment to anyone who arrived early. */}
          {busy
            ? 'Running both channels…'
            : health.loading
              ? 'Checking whether asking is enabled…'
              : live
                ? 'Ask'
                : 'Asking is disabled'}
        </button>
        {busy && (
          <p className="note" role="status">
            Expect a few minutes. On this machine a question measured about three:
            two model calls, a program executed in a container, and the arbiter if
            the channels disagree. Leaving this page abandons the wait, but the
            answer is still saved and will appear on Verification under the{' '}
            <code>live-qa</code> run.
          </p>
        )}
      </form>

      {error && <ErrorBox title="The question was not answered" message={error} />}

      {answer && <AnswerView answer={answer} />}
    </section>
  )
}

function AnswerView({ answer }: { answer: AnswerOut }) {
  const explanation = answer.explanation
  return (
    <>
      <h3>Answer</h3>
      <div className="panel">
        <div className="grid">
          <div className="stat">
            <div className="label">Answer</div>
            {/* answer_text, never the bare magnitude: the unit is part of it. */}
            <div className="value">
              {answer.answer_text ?? (
                <span className="undefined-value">
                  {answer.abstained ? 'declined to answer' : 'none'}
                </span>
              )}
            </div>
          </div>
          <div className="stat">
            <div className="label">Risk</div>
            <div className={`value risk-${answer.band}`}>
              {formatRisk(answer.risk_score)}
            </div>
            <div className="sub-value">
              <span className={`pill risk-${answer.band}`}>{answer.band}</span>
            </div>
          </div>
          <div className="stat">
            <div className="label">Verdict</div>
            <div className="value text">
              {answer.verdict ?? 'no cross-check in this arm'}
            </div>
          </div>
          <div className="stat">
            <div className="label">Latency</div>
            <div className="value">
              {answer.latency_seconds !== null ? `${answer.latency_seconds}s` : '—'}
            </div>
          </div>
        </div>
        {!answer.calibrated && answer.risk_score !== null && (
          <p className="note" style={{ marginBottom: 0 }}>
            The risk score is <strong>uncalibrated</strong>. It ranks answers by
            relative risk; it is not a probability and must not be read as one.
          </p>
        )}
        {answer.answer_id !== null ? (
          <p className="note" style={{ marginBottom: 0 }}>
            Saved.{' '}
            <Link to={`/verification?answer=${answer.answer_id}`}>
              Open it in the full verification view
            </Link>{' '}
            — it stays there after you leave this page.
          </p>
        ) : null}
      </div>

      <h3>What each channel said</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th>Reported</th>
              {/* Without this column a pure SCALE disagreement is invisible:
                  both channels report "12232" while one means crore and the
                  other million, and the table shows two identical numbers for a
                  hundredfold difference. The canonical form is the same figure
                  in base units, so the disagreement becomes readable. Scale is
                  the highest-frequency source of plausible wrong answers in
                  this domain. */}
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
                <td className="num">
                  {channel.canonical ?? <span className="undefined-value">—</span>}
                </td>
                <td>
                  {!channel.applicable
                    ? 'does not apply to this question'
                    : channel.available
                      ? 'answered'
                      : (channel.failure_reason ?? 'no figure')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {answer.verification?.triggered && (
        <>
          <h3>Arbiter</h3>
          <div className="panel">
            <p style={{ marginTop: 0 }}>
              <strong>
                {answer.verification.resolved
                  ? `Resolved: ${answer.verification.resolution}`
                  : 'Declined to resolve the disagreement'}
              </strong>
            </p>
            {answer.verification.reasoning && (
              <p className="note" style={{ marginBottom: 0 }}>
                {answer.verification.reasoning}
              </p>
            )}
          </div>
        </>
      )}

      {answer.evidence.length > 0 && (
        <>
          <h3>Evidence retrieved</h3>
          <ul className="reasons">
            {/* Keyed by position, not by ref. Two spans can share a group id -
                3 of the 120 recorded answers sampled do - and a duplicate React
                key makes the second one vanish from the list, dropping a
                citation from the evidence a reader is being asked to check. */}
            {answer.evidence.map((block, index) => (
              <li key={`${block.ref}-${index}`}>
                <code>{block.ref}</code>{' '}
                {block.document ? <strong>{block.document}</strong> : null}
                {block.document ? ' · ' : ''}
                {block.citation}
              </li>
            ))}
          </ul>
        </>
      )}

      {explanation && (
        <>
          <h3>Why</h3>
          <ul className="reasons">
            {explanation.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <h3>What would change it</h3>
          <ul className="reasons">
            {explanation.what_would_change_it.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          {explanation.caveats.length > 0 && (
            <>
              <h3>Caveats</h3>
              <ul className="reasons">
                {explanation.caveats.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}

      {answer.quota_note && <p className="note">{answer.quota_note}</p>}
    </>
  )
}
