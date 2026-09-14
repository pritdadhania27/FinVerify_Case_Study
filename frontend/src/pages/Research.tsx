import { Link, useSearchParams } from 'react-router-dom'
import { api, formatMetric, type EvaluationResultOut, type ExperimentOut } from '../lib/api'
import { IntervalChart, type IntervalRow } from '../lib/IntervalChart'
import { Empty, ErrorBox, Loading } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * Screen 6 of spec §29: the research dashboard.
 *
 * The one thing this screen must get right is that an UNDEFINED metric is not a
 * zero. AUROC on a stratum containing no errors has no value, and rendering it
 * as 0.000 would say "the detector was worse than chance" where the truth is
 * "this stratum could not test it". `formatMetric` prints "undefined", and the
 * confidence interval is shown beside every point estimate because on a
 * 150-question dataset a gap of 0.05 sits comfortably inside the noise.
 */
/**
 * A metric note, with its standing flags lifted out of the prose.
 *
 * The notes are assembled server-side and read as a run-on sentence
 * ("UNDERPOWERED - 3 error(s)…; removes the executed-program channel;
 * p<0.0001; Holm significant; …"). The two things a reader must not skim past
 * are buried mid-string, so they are promoted to marks at the head of the same
 * cell — the same cell being the point. A caveat rendered anywhere but beside
 * its number is a caveat nobody reads, and a Holm-significant contrast resting
 * on three errors is the most misleading thing this table can show.
 *
 * Nothing is removed: the full note still follows the marks verbatim.
 */
function Note({ text }: { text: string | null }) {
  if (!text) return null
  const underpowered = text.includes('UNDERPOWERED')
  const significant = /Holm significant/.test(text)
  return (
    <>
      {underpowered && <span className="flag flag-underpowered">underpowered</span>}
      {significant && <span className="flag flag-significant">holm</span>}
      <span className={underpowered ? 'note-qualified' : undefined}>{text}</span>
    </>
  )
}

/** What a run offers this screen, said in the option itself. */
function runLabel(run: ExperimentOut): string {
  if (run.results === 0) return `${run.run_id} (no metrics · ${run.answers} answers)`
  if (run.answers === 0) {
    const kind = run.run_id.includes('+') ? 'pooled report' : 'report only'
    return `${run.run_id} (${kind} · ${run.results} metrics)`
  }
  return `${run.run_id} (${run.results} metrics · ${run.answers} answers)`
}

export default function ResearchPage() {
  const experiments = useAsync(() => api.experiments(), [])
  // In the URL, so the dashboard's run table can link straight to one run's
  // metrics. Those 42 links previously all pointed at this page with nothing
  // selected, so every one of them landed on the same unfiltered table.
  const [params, setParams] = useSearchParams()
  const runId = params.get('run') ?? ''
  // Updated from the previous params rather than this render's captured copy, so
  // a write cannot be built on a snapshot the router has already moved past.
  // Harmless on a select, where changes arrive one at a time; it is the same
  // defect that dropped characters from the verification screen's search box.
  const setRunId = (value: string) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set('run', value)
        else next.delete('run')
        return next
      },
      { replace: true },
    )
  }
  const results = useAsync(() => api.results(runId ? { run_id: runId } : {}), [runId])

  // The picker offers runs that carry METRICS, because metrics are what this
  // screen shows. It used to offer every registered run with its answer count,
  // and most of those have nothing to show here: the development and diagnostic
  // runs (the slice_, channels_, qu-llm_ and budget_ series) record no benchmark
  // answers, and several campaigns recorded answers that were never analysed.
  // Each appeared as an option leading to an empty page. Picking by answers
  // instead would have been worse - the pooled ablation report carries the
  // headline contrasts and has no answers of its own, because they belong to the
  // two campaigns it pools. The live-qa run (D51) has no metrics either, so this
  // one criterion also keeps demonstrations out.
  const withMetrics = (experiments.data ?? []).filter((run) => run.results > 0)
  const selectedRun = experiments.data?.find((run) => run.run_id === runId) ?? null
  // The URL names a run the database does not have - a typo, a stale bookmark.
  const unknownRun = runId !== '' && experiments.data !== null && selectedRun === null
  // Where a run's answers were analysed together with another run's.
  const pooledReport =
    selectedRun && selectedRun.results === 0
      ? (withMetrics.find((run) => run.run_id.split('+').includes(selectedRun.run_id)) ?? null)
      : null

  const byArm = new Map<string, typeof results.data>()
  for (const row of results.data ?? []) {
    const key = `${row.arm}`
    if (!byArm.has(key)) byArm.set(key, [])
    byArm.get(key)!.push(row)
  }

  // Charts are drawn ONLY for a single selected run. Plotting arms from several
  // campaigns on one axis is the RX-047 confound rendered as a picture: arms
  // A/G/H ran against a model that has since been retired and B-F against
  // another, so a shared axis would show a model swap as a component effect.
  // The default "all runs" view therefore gets tables and no chart.
  const chartable = runId ? (results.data ?? []) : []

  const detection: IntervalRow[] = [...new Set(chartable.map((r) => r.arm))]
    .sort()
    .map((armName) => {
      const row = chartable.find(
        (r: EvaluationResultOut) =>
          r.arm === armName && r.metric === 'auroc' && r.stratum === 'all',
      )
      return {
        label: armName,
        value: row?.value ?? null,
        low: row?.ci_low ?? null,
        high: row?.ci_high ?? null,
        note: row?.value == null ? 'this arm has no detector' : null,
      }
    })

  // Named from the run's recorded split. The caption used to say "held-out" for
  // every run, so the pooled ablation - validation only, and permanently so,
  // because the test split was spent before those arms ran - was presented as a
  // held-out result on the one chart a reader is most likely to screenshot.
  const splitPhrase =
    selectedRun?.split === 'test'
      ? 'on the held-out test split'
      : selectedRun?.split
        ? `on the ${selectedRun.split} split, which is not held out`
        : 'for this run'

  const contrasts: IntervalRow[] = chartable
    .filter((r) => r.metric.startsWith('auroc_delta_vs_'))
    .sort((a, b) => (a.stratum + a.arm).localeCompare(b.stratum + b.arm))
    .map((r) => ({
      label: `A − ${r.arm} (${r.stratum})`,
      value: r.value,
      low: r.ci_low,
      high: r.ci_high,
      underpowered: (r.note ?? '').includes('UNDERPOWERED'),
      note: r.note,
    }))

  return (
    <section>
      <h2>Research</h2>
      <p className="note">
        Metric values from ingested reports. A value shown as{' '}
        <strong>undefined</strong> was not measurable — most often an AUROC on a
        stratum with no incorrect answers — and is deliberately not zero.
      </p>

      {experiments.loading && <Loading what="runs" />}
      {experiments.error && <ErrorBox title="Could not load runs" message={experiments.error} />}

      {experiments.data && withMetrics.length > 0 && (
        <form style={{ maxWidth: 560 }}>
          <label htmlFor="run">Run</label>
          <select id="run" value={runId} onChange={(e) => setRunId(e.target.value)}>
            <option value="">all runs with metrics</option>
            {withMetrics.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {runLabel(run)}
              </option>
            ))}
            {/* A deep link can name a run with no metrics, or one that does not
                exist. Either stays representable, so the control shows what the
                URL says. Without an option to match, the browser displays the
                first one - "all runs with metrics" - above that run's empty
                result set, and choosing it fires no change, so the filter could
                not be cleared from the control at all. */}
            {selectedRun && selectedRun.results === 0 && (
              <option value={selectedRun.run_id}>{runLabel(selectedRun)}</option>
            )}
            {unknownRun && <option value={runId}>{runId} (not registered)</option>}
          </select>
          <p className="note" style={{ marginBottom: 0 }}>
            {/* Counted without the live-qa demonstration run, so this matches the
                dashboard's "Runs ingested", which excludes it too. */}
            Listed: the {withMetrics.length} of{' '}
            {experiments.data.filter((run) => run.split !== 'live').length} registered runs
            that carry metrics. The rest either recorded answers that were never
            analysed on their own — inspect those on{' '}
            <Link to="/verification">Verification</Link> — or are development and
            diagnostic runs that record no benchmark answers.
          </p>
        </form>
      )}

      {runId && detection.some((r) => r.value !== null) && (
        <>
          <h3>Detection, by configuration</h3>
          <IntervalChart
            rows={detection}
            reference={0.5}
            referenceLabel="chance"
            places={3}
            bounds={[0, 1]}
            caption={`Detection AUROC per arm ${splitPhrase}, with its 95% bootstrap interval. An arm with no detector is drawn as absent, never at 0.5 — entering a placeholder would read as 'this baseline detects nothing' where the truth is 'this baseline is not a detector'.`}
          />
        </>
      )}

      {contrasts.length > 0 && (
        <>
          <h3>The ablation: what each component contributes</h3>
          <IntervalChart
            rows={contrasts}
            reference={0}
            referenceLabel="no effect"
            places={3}
            caption="AUROC(A) − AUROC(arm), paired over the same questions. A point right of the line means removing that component COST the detector. The interval is the finding, not the point: one that crosses the line is consistent with no effect. Contrasts marked underpowered rest on too few errors to separate two arms whatever their p-value — an AUROC ranks errors, so the error count is the real sample size."
          />
        </>
      )}

      {results.loading && <Loading what="results" />}
      {results.error && <ErrorBox title="Could not load results" message={results.error} />}
      {/* For a selected run, the right explanation depends on the run list.
          Until that list has loaded - or when it failed, which the error box
          above already says - nothing is shown here. Falling through instead
          flashed the generic "run analyse_campaign.py" hint before the right
          panel whenever the results request won the race, and left that hint up
          for good if the run list failed, on runs whose metrics were already
          ingested in a pooled report. */}
      {results.data &&
        results.data.length === 0 &&
        !(runId && (experiments.loading || experiments.error)) &&
        (unknownRun ? (
          <div className="panel">
            <p className="note">
              <strong>No run named {runId} is registered.</strong> The link may be
              mistyped, or come from a different database.
            </p>
            <button type="button" onClick={() => setRunId('')}>
              Show all runs with metrics
            </button>
          </div>
        ) : selectedRun && pooledReport ? (
          <div className="panel">
            <p className="note" style={{ margin: 0 }}>
              <strong>This run has no metrics of its own.</strong> Its{' '}
              {selectedRun.answers} answers were analysed together with another run, in
              the pooled report{' '}
              <Link to={`/research?run=${encodeURIComponent(pooledReport.run_id)}`}>
                {pooledReport.run_id}
              </Link>
              .
            </p>
          </div>
        ) : selectedRun && selectedRun.answers > 0 ? (
          <div className="panel">
            <p className="note" style={{ margin: 0 }}>
              <strong>No metrics were computed for this run.</strong> It recorded{' '}
              {selectedRun.answers} answers, but no evaluation report for it has been
              ingested — typically a smoke, void or partial run. Its answers can be
              inspected one by one on{' '}
              <Link to={`/verification?run=${encodeURIComponent(selectedRun.run_id)}`}>
                Verification
              </Link>
              .
            </p>
          </div>
        ) : selectedRun ? (
          <div className="panel">
            {/* "No evaluation metrics", not "nothing to measure": several of these
                runs did measure the pipeline - channel reliability, question
                parsing, token budgets - and keep those figures in their own
                metrics.json. What they lack is benchmark answers to evaluate. */}
            <p className="note" style={{ margin: 0 }}>
              <strong>No evaluation metrics for this run.</strong> It records no
              benchmark answers — a development or diagnostic run. Its own
              measurements of the pipeline, where it took any, are in{' '}
              <code>experiments/runs/{selectedRun.run_id}/</code>.
            </p>
          </div>
        ) : !runId ? (
          <Empty
            what="results"
            hint="Run scripts/analyse_campaign.py, then ingest_to_database.py."
          />
        ) : null)}

      {[...byArm.entries()].map(([arm, rows]) => (
        <div key={arm}>
          <h3>
            Arm <span className="badge arm">{arm}</span>
          </h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {/* The default view is every run, and without this column
                      rows from eight campaigns stacked under one arm heading
                      with no way to tell them apart - three identical-looking
                      rows carrying three different values. */}
                  {!runId && <th>Run</th>}
                  <th>Metric</th>
                  <th>Stratum</th>
                  <th className="num">Value</th>
                  <th className="num">95% CI</th>
                  <th className="num">n</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {(rows ?? []).map((row) => (
                  <tr key={`${row.run_id}-${row.metric}-${row.stratum}`}>
                    {!runId && (
                      <td>
                        <code>{row.run_id}</code>
                      </td>
                    )}
                    <td>{row.metric}</td>
                    <td>{row.stratum}</td>
                    <td className="num">
                      {row.value === null ? (
                        <span className="undefined-value">undefined</span>
                      ) : (
                        formatMetric(row.value)
                      )}
                    </td>
                    <td className="num">
                      {row.ci_low !== null && row.ci_high !== null
                        ? `[${formatMetric(row.ci_low)}, ${formatMetric(row.ci_high)}]`
                        : '—'}
                    </td>
                    <td className="num">{row.n ?? '—'}</td>
                    <td>
                      <Note text={row.note} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      <h3>Reading these numbers</h3>
      <div className="panel">
        <ul className="reasons">
          <li>
            Baselines B1–B4 have no detector and are absent from the detection
            rows by design, not by omission. Entering them at AUROC 0.5 would
            present a placeholder as a measurement.
          </li>
          <li>
            Detection is reported stratified by error provenance as well as
            pooled. Both channels read the same evidence, so agreement is
            expected to be near-blind to retrieval-caused error, and the pooled
            figure averages across a regime where the method cannot work.
          </li>
          <li>
            Absolute accuracy is a free-tier-model figure and must never be set
            beside published frontier-model results as though the setups matched.
          </li>
        </ul>
      </div>
    </section>
  )
}
