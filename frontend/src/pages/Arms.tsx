import { Link } from 'react-router-dom'
import { api, type ArmOut } from '../lib/api'
import { ErrorBox, Loading } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * The ablation, made legible (spec Module 24).
 *
 * Every other screen labels rows with an arm name, and until this page existed
 * nothing in the UI said what an arm *is* — so "arm C scored 0.880" was a fact
 * about an unnamed thing. The whole design is "arm A minus exactly one field",
 * and that claim is only checkable if the fields are on screen.
 *
 * The matrix is the point: read down a column to see which configurations carry
 * a component, read across a row to see what one configuration is. Presence is
 * carried by a glyph AND a label, never by colour alone, because "this arm has
 * no arbiter" is exactly the distinction the verification screen was getting
 * wrong.
 */

const COMPONENTS = [
  { key: 'uses_natural', label: 'Natural', title: 'natural-language reasoning channel' },
  { key: 'uses_program', label: 'Program', title: 'generated program, executed in the sandbox' },
  { key: 'uses_deterministic', label: 'Determ.', title: 'deterministic numerical verifier' },
  { key: 'uses_consistency', label: 'Consist.', title: 'consistency engine across channels' },
  { key: 'uses_arbiter', label: 'Arbiter', title: 'verification agent, on disagreement' },
] as const

/** Baselines and diagnostics are not ablation arms and should not read as if they were. */
function family(name: string): string {
  if (name === 'A') return 'Proposed system'
  if (name.startsWith('B') && name.length > 1) return 'Baseline'
  if (name === 'O') return 'Diagnostic'
  return 'Ablation'
}

function Cell({ on, title }: { on: boolean; title: string }) {
  return (
    <td className="num" title={title}>
      <span className={on ? 'risk-LOW' : 'undefined-value'} aria-label={on ? 'yes' : 'no'}>
        {on ? '●' : '—'}
      </span>
    </td>
  )
}

export default function ArmsPage() {
  const arms = useAsync(() => api.arms(), [])

  const groups = new Map<string, ArmOut[]>()
  for (const arm of arms.data ?? []) {
    const key = family(arm.name)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(arm)
  }
  const order = ['Proposed system', 'Ablation', 'Baseline', 'Diagnostic']

  return (
    <section>
      <h2>Configurations</h2>
      <p className="note">
        Every ablation arm is <strong>arm A minus exactly one field</strong> — a
        property <code>evaluation/arms.py</code> has its own test for. This table
        is served from that same definition, so it cannot drift from what the
        campaign actually ran.
      </p>

      {arms.loading && <Loading what="configurations" />}
      {arms.error && <ErrorBox title="Could not load configurations" message={arms.error} />}

      {arms.data && (
        <>
          {order
            .filter((name) => groups.has(name))
            .map((name) => (
              <div key={name}>
                <h3>{name}</h3>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Arm</th>
                        <th>What it is</th>
                        {COMPONENTS.map((c) => (
                          <th key={c.key} className="num" title={c.title}>
                            {c.label}
                          </th>
                        ))}
                        <th>Retrieval</th>
                        <th>Removes, vs arm A</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(groups.get(name) ?? []).map((arm) => (
                        <tr key={arm.name}>
                          <td>
                            <span className="badge arm">{arm.name}</span>
                          </td>
                          <td>{arm.description}</td>
                          {COMPONENTS.map((c) => (
                            <Cell key={c.key} on={arm[c.key]} title={`${arm.name}: ${c.title}`} />
                          ))}
                          <td>{arm.retrieval ?? '—'}</td>
                          <td>
                            {arm.removes ?? (
                              <span className="undefined-value">
                                nothing — this is the baseline every other arm is read against
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}

          <h3>How to read this</h3>
          <div className="panel">
            <ul className="reasons">
              <li>
                An <strong>ablation</strong> arm differs from A in one field, so the
                difference in its score is attributable to that field. A{' '}
                <strong>baseline</strong> differs in many and is not part of the
                ablation — B1–B4 have no detector at all and are absent from the
                detection table by design.
              </li>
              <li>
                Arm <strong>O</strong> is a diagnostic, not a system: it is handed the
                chunks containing the gold evidence, so every remaining error is a
                reasoning error by construction. Its accuracy is not an end-to-end
                measurement and must never be quoted as one.
              </li>
              <li>
                A contrast is only meaningful between arms sharing a{' '}
                <strong>channel binding</strong>. Comparing arms run against different
                models measures the model swap, not the component — see the run table
                on the <Link to="/">dashboard</Link> for which binding each run used.
              </li>
            </ul>
          </div>
        </>
      )}
    </section>
  )
}
