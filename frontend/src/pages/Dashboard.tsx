import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { ErrorBox, Loading } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * Screen 1 of spec §29.
 *
 * Shows what is REACHABLE rather than what is configured, because the backend's
 * /health endpoint reports it that way and re-deriving it here would let the two
 * disagree. The live-QA switch is surfaced prominently: a user who does not know
 * it is off will read "asking is disabled" as a bug rather than as the deliberate
 * guard on the campaign's free-tier quota.
 *
 * Every figure below is a row count from /stats, computed per request. There is
 * deliberately no tile for a metric nobody has computed - accuracy, detection
 * AUROC and hallucination rate are absent from this screen until a campaign
 * produces them, and the campaign panel says so in words rather than showing a
 * zero that reads like a measurement.
 */

function pct(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : '—'
}

export default function Dashboard() {
  const health = useAsync(() => api.health(), [])
  const stats = useAsync(() => api.stats(), [])
  // Research runs only. Live questions are stored under their own run (D51) so
  // Verification can reopen them, but /stats excludes that run - and a runs table
  // listing it beside a tile that does not would contradict itself by one.
  const experiments = useAsync(
    () => api.experiments().then((runs) => runs.filter((run) => run.split !== 'live')),
    [],
  )

  // Runs with something to show. A registered run with neither answers nor
  // metrics is a development or diagnostic run - the slice_, channels_, qu-llm_
  // and budget_ series - whose records are not benchmark answers, so ingest
  // registers the run and then skips every row. This table listed all of them,
  // each linked to an empty Research page, and said they "produced no rows"
  // when they recorded diagnostics; that is how a reader reached a run picker
  // full of "(0 answers)".
  const runsWithData = experiments.data?.filter((r) => r.answers > 0 || r.results > 0) ?? []
  const developmentRuns =
    experiments.data?.filter((r) => r.answers === 0 && r.results === 0) ?? []
  const developmentSeries = [...new Set(developmentRuns.map((r) => `${r.run_id.split('_')[0]}_`))]
  const results = useAsync(() => api.results(), [])
  // A metric row whose value is null was UNDEFINED, not zero, and does not count
  // as something having been measured.
  //
  // Counted DISTINCT by (arm, stratum). `/evaluation/results` returns one row
  // per (run, arm, metric, stratum), so counting rows reported the same
  // arm-stratum once per campaign that measured it - 70 where 36 exist. A
  // number on a dashboard that is nearly double the truth is the failure this
  // project's own rules exist to prevent.
  const measured = new Set(
    (results.data ?? [])
      .filter((r) => r.metric === 'auroc' && r.value !== null)
      .map((r) => `${r.arm}|${r.stratum}`),
  )

  return (
    <section>
      <h2>Dashboard</h2>
      <p className="note">
        Every field below was checked by making the call, not by reading a config
        value.
      </p>

      {health.loading && <Loading what="health" />}
      {health.error && <ErrorBox title="The API is unreachable" message={health.error} />}

      {health.data && (
        <div className="grid">
          <div className="panel stat">
            <div className="label">Database</div>
            <div className="value text">{health.data.database ? 'reachable' : 'unreachable'}</div>
          </div>
          <div className="panel stat">
            <div className="label">Vector index</div>
            {/* "usable", not "populated": /health requires that the collection
                was built by the embedding model now configured to query it. A
                false here can mean unreachable, misconfigured, or built by a
                different model - one word for three causes sent a reader
                looking for the wrong problem. */}
            <div className="value text">
              {health.data.vector_index ? 'usable' : 'not usable'}
            </div>
          </div>
          <div className="panel stat">
            <div className="label">Live question answering</div>
            <div className="value text">
              {health.data.live_questions_enabled ? 'enabled' : 'disabled'}
            </div>
          </div>
          <div className="panel stat">
            <div className="label">Serving build</div>
            {/* Every other field describes an EXTERNAL service and can be green
                while this process runs weeks-old code. The API sent this all
                along and nothing displayed it. */}
            <div className="value text">
              <code>{health.data.build_ref}</code>
            </div>
          </div>
        </div>
      )}

      {health.data && !health.data.vector_index && (
        <div className="panel">
          <strong>The vector index is not usable, so retrieval cannot run.</strong>
          <p className="note" style={{ marginBottom: 0 }}>
            {health.data.note}
          </p>
        </div>
      )}

      {health.data && !health.data.live_questions_enabled && (
        <div className="panel">
          <strong>Asking questions is switched off.</strong>
          <p className="note" style={{ marginBottom: 0 }}>
            Each question runs both reasoning channels and can trigger the
            arbiter, spending free-tier quota the evaluation campaign depends on.
            Set <code>FINVERIFY_ENABLE_LIVE_QA=1</code> on the API process to
            enable it deliberately.
          </p>
        </div>
      )}

      <h3>Corpus</h3>
      {stats.loading && <Loading what="corpus statistics" />}
      {stats.error && <ErrorBox title="Could not load statistics" message={stats.error} />}
      {stats.data && (
        <>
          <div className="grid">
            <div className="panel stat">
              <div className="label">Filings</div>
              <div className="value">{stats.data.documents}</div>
            </div>
            <div className="panel stat">
              <div className="label">Pages</div>
              <div className="value">{stats.data.pages.toLocaleString()}</div>
            </div>
            <div className="panel stat">
              <div className="label">Tables</div>
              <div className="value">{stats.data.tables.toLocaleString()}</div>
            </div>
            <div className="panel stat">
              <div className="label">Extracted figures</div>
              <div className="value">{stats.data.financial_facts.toLocaleString()}</div>
            </div>
          </div>

          <h3>Gold answers</h3>
          <div className="grid">
            <div className="panel stat">
              <div className="label">Validated</div>
              <div className="value">{stats.data.questions_validated}</div>
              <div className="sub-value">
                {/* Judged = validated + rejected. The pending rows have NOT been
                    judged, and calling all 192 "judged" contradicted the tile
                    two columns over that reports 48 awaiting review. */}
                {pct(
                  stats.data.questions_validated,
                  stats.data.questions_validated + stats.data.questions_rejected,
                )}{' '}
                of {stats.data.questions_validated + stats.data.questions_rejected} judged
              </div>
            </div>
            <div className="panel stat">
              <div className="label">Rejected</div>
              <div className="value">{stats.data.questions_rejected}</div>
              <div className="sub-value">
                held out of every score
              </div>
            </div>
            <div className="panel stat">
              <div className="label">Awaiting review</div>
              <div className="value">{stats.data.questions_pending}</div>
              <div className="sub-value">
                not usable as gold
              </div>
            </div>
            <div className="panel stat">
              <div className="label">Evidence spans</div>
              <div className="value">{stats.data.evidence_spans}</div>
            </div>
          </div>
          <p className="note">
            Only <strong>validated</strong> rows may be scored against. A rejected
            or pending candidate is not gold, and counting all{' '}
            {stats.data.questions_total} candidate questions as a benchmark size
            would overstate the evidence base by{' '}
            {stats.data.questions_total - stats.data.questions_validated} rows.
          </p>

          <h3>Campaign</h3>
          <div className="grid">
            <div className="panel stat">
              <div className="label">Rows measured</div>
              <div className="value">{stats.data.answers_graded.toLocaleString()}</div>
              <div className="sub-value">
                {stats.data.answers.toLocaleString()} produced, across{' '}
                {stats.data.arms.length || '—'} arm
                {stats.data.arms.length === 1 ? '' : 's'}
              </div>
            </div>
            <div className="panel stat">
              <div className="label">Arms recorded</div>
              {/* A list of arm names is not a reading and must not be set as
                  one. Each is the label the ablation is read against, so each
                  gets the badge it carries everywhere else in the UI. */}
              <div className="value text badges">
                {stats.data.arms.length
                  ? stats.data.arms.map((name) => (
                      <span className="badge arm" key={name}>
                        {name}
                      </span>
                    ))
                  : '—'}
              </div>
            </div>
            <div className="panel stat">
              <div className="label">Runs ingested</div>
              <div className="value">{stats.data.runs}</div>
              {experiments.data && (
                <div className="sub-value">{runsWithData.length} with answers or metrics</div>
              )}
            </div>
          </div>
        </>
      )}

      <h3>Accuracy, detection and hallucination rate</h3>
      {/*
        This panel used to read "Not yet measured" unconditionally, which was
        true when it was written and false once the campaigns finished - it went
        on calling 1,646 graded rows "the run in progress" and too few to compute
        an interval from. It now reports which of the two states it is actually
        in. No figure is quoted here: the per-arm table with its intervals lives
        on Research, and a headline number copied onto a second screen is a
        number that will disagree with the first one eventually.
      */}
      {results.loading ? (
        <Loading what="evaluation results" />
      ) : results.error ? (
        <ErrorBox title="Could not load evaluation results" message={results.error} />
      ) : measured.size === 0 ? (
        <div className="panel">
          <strong>Not yet measured.</strong>
          <p className="note" style={{ marginBottom: 0 }}>
            These require a completed campaign, and no evaluation report has been
            ingested. Per-arm results appear on <Link to="/research">Research</Link>{' '}
            once <code>scripts/analyse_campaign.py</code> has run and its report
            has been ingested.
          </p>
        </div>
      ) : (
        <div className="panel">
          <strong>
            Measured: {measured.size} distinct arm-strata carry a detection
            AUROC with a confidence interval.
          </strong>
          <p className="note">
            The per-arm table, its intervals and the single-field ablation
            contrasts are on <Link to="/research">Research</Link>. Two caveats
            travel with every one of those numbers and are not optional
            footnotes:
          </p>
          <ul className="reasons">
            <li>
              The risk score takes a handful of distinct values, and the
              always-wrong bucket is the system's own <strong>abstention</strong>,
              which the grader counts as an error. Most of what the pooled AUROC
              "detects" is therefore a refusal, not a hallucination. The
              committed-answers figure is the one the research question asks
              about, and it is far weaker.
            </li>
            <li>
              The completed single-field ablation is <strong>validation only</strong>.
              The test split was spent before those arms ran, so they can never be
              held out — and every earlier held-out claim in this project weakened
              on test.
            </li>
          </ul>
        </div>
      )}

      <h3>Runs</h3>
      {experiments.loading && <Loading what="experiments" />}
      {experiments.error && (
        <ErrorBox title="Could not load experiments" message={experiments.error} />
      )}
      {experiments.data && experiments.data.length === 0 && (
        <div className="panel">
          <p className="note" style={{ margin: 0 }}>
            No runs ingested. Run a campaign, then{' '}
            <code>scripts/ingest_to_database.py --all</code>. Run artifacts on
            disk remain the source of truth either way.
          </p>
        </div>
      )}
      {experiments.data && experiments.data.length > 0 && (
        <>
          <p className="note">
            {runsWithData.length} of {experiments.data.length} registered runs carry
            answers or metrics, and are listed.
            {developmentRuns.length > 0 && (
              <>
                {' '}
                {/* "Evaluation metrics", not "metrics": several of these runs
                    measured the pipeline and keep those figures in metrics.json.
                    What they lack is benchmark answers to evaluate. */}
                The other {developmentRuns.length} carry neither benchmark answers nor
                evaluation metrics — the {developmentSeries.join(', ')} series of
                development and diagnostic runs. Their own measurements of the pipeline,
                where they took any, remain in <code>experiments/runs/</code>.
              </>
            )}
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Split</th>
                  <th className="num">Answers</th>
                  <th className="num">Metrics</th>
                  <th>Independence</th>
                </tr>
              </thead>
              <tbody>
                {[...runsWithData]
                  .sort((a, b) => b.answers - a.answers || b.results - a.results)
                  .map((run) => (
                    <tr key={run.run_id}>
                      <td>
                        {/* To the screen that has something for this run: its
                            metrics if it has any, otherwise its answers. */}
                        <Link
                          to={
                            run.results > 0
                              ? `/research?run=${encodeURIComponent(run.run_id)}`
                              : `/verification?run=${encodeURIComponent(run.run_id)}`
                          }
                        >
                          {run.run_id}
                        </Link>
                      </td>
                      <td>{run.split ?? '—'}</td>
                      <td className="num">{run.answers}</td>
                      <td className="num">{run.results}</td>
                      <td>{run.independence ?? 'not recorded'}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
