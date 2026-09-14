/**
 * Typed client for the FinVerify-AI API.
 *
 * Two conventions carried from the backend rather than invented here, because
 * the UI is where they get quietly broken:
 *
 * 1. `risk_score` is `number | null`. An arm with no detector has no score, and
 *    rendering `?? 0.5` would put a placeholder on screen where the truth is
 *    "this configuration does not measure that". Every component that displays
 *    it must handle null.
 * 2. A figure travels with its unit. `answer` is the bare magnitude and
 *    `answer_text` is the round-trippable form ("INR 3956 crore"). The UI shows
 *    `answer_text`: a bare 3956 next to a gold of 3,956 crore looks like a match
 *    and is a hundredfold error.
 */

export interface DocumentOut {
  document_id: string
  filename: string
  company: string | null
  fiscal_year: string | null
  page_count: number | null
  sha256: string
  source_url: string | null
  retrieved_on: string | null
}

export interface EvidenceOut {
  ref: string
  citation: string
  page: number | null
  chunk_id: string | null
  text: string | null
  /** The filing this evidence is from; a page number alone is ambiguous. */
  document_id: string | null
  document: string | null
}

/**
 * What a configuration switches on. Served by the API rather than restated
 * here, because the arm table IS the ablation and a second copy in the client
 * would drift from it.
 */
export interface ArmOut {
  name: string
  description: string
  uses_natural: boolean
  uses_program: boolean
  uses_deterministic: boolean
  uses_consistency: boolean
  uses_arbiter: boolean
  same_model_both_channels: boolean
  retrieval: string | null
  self_consistency_samples: number | null
  /** What this arm removes relative to arm A; null for arm A itself. */
  removes: string | null
}

export interface ChannelOut {
  name: string
  available: boolean
  applicable: boolean
  value: string | null
  canonical: string | null
  failure_reason: string | null
}

export interface VerificationOut {
  triggered: boolean
  available: boolean
  resolution: string | null
  resolved: boolean
  value: string | null
  reasoning: string | null
}

export interface Explanation {
  answer: string | null
  answer_source: string | null
  risk_score: number | null
  band: string
  citations: string[]
  channel_statements: string[]
  reasons: string[]
  what_would_change_it: string[]
  caveats: string[]
}

export interface AnswerOut {
  question: string
  arm: string
  /** Null for an answer that could not be stored, which cannot be reopened. */
  answer_id: number | null
  question_id: string | null
  answer: string | null
  answer_text: string | null
  canonical: string | null
  answer_source: string | null
  abstained: boolean
  verdict: string | null
  agreed: boolean | null
  risk_score: number | null
  band: string
  calibrated: boolean
  /**
   * Lets the UI tell "this arm has no arbiter" from "the arbiter did not
   * fire". Without it the verification screen asserted the second for arms
   * that structurally have neither an arbiter nor a consistency engine.
   */
  arm_config: ArmOut | null
  channels: ChannelOut[]
  evidence: EvidenceOut[]
  verification: VerificationOut | null
  explanation: Explanation | null
  latency_seconds: number | null
  quota_note: string | null
}

export interface ExperimentOut {
  run_id: string
  split: string | null
  natural_model: string | null
  program_model: string | null
  verifier_model: string | null
  independence: string | null
  answers: number
  /** Metric rows. The metrics screen picks runs by this, not by `answers`. */
  results: number
  source_path: string | null
}

export interface EvaluationResultOut {
  run_id: string
  arm: string
  metric: string
  stratum: string
  value: number | null
  ci_low: number | null
  ci_high: number | null
  n: number | null
  note: string | null
}

export interface HealthOut {
  status: string
  database: boolean
  vector_index: boolean
  live_questions_enabled: boolean
  /**
   * Which build is answering. Every other field here describes an EXTERNAL
   * service and can be green while the process itself runs weeks-old code, so
   * this is the one that catches a stale container - which is exactly why it
   * has to be displayed rather than merely sent.
   */
  build_ref: string
  note: string
}

export interface AnswerSummaryOut {
  answer_id: number
  run_id: string
  arm: string
  question_id: string
  question: string
  answer_text: string | null
  abstained: boolean
  verdict: string | null
  agreed: boolean | null
  risk_score: number | null
  /** Derived server-side by `confidence.band_for`; never recomputed here. */
  band: string
  correct: boolean | null
  latency_seconds: number | null
}

export interface CorpusStatsOut {
  documents: number
  pages: number
  sections: number
  tables: number
  financial_facts: number
  questions_total: number
  questions_validated: number
  questions_rejected: number
  questions_pending: number
  evidence_spans: number
  runs: number
  answers: number
  answers_graded: number
  arms: string[]
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const BASE = '/api'

// Every request has a deadline. With PostgreSQL stopped the API held requests
// open, and a fetch with no deadline waits as long as the server does - every
// screen sat on "Loading" indefinitely with no error, which is the one failure
// state a user cannot tell apart from a slow page.
const READ_TIMEOUT_MS = 30_000
// A live question measured about three minutes, and 691 s when the program
// channel's provider kept timing out on read. nginx
// allows 900 s, and this deadline must outlast it, so a slow question ends in the
// proxy's 504 - explained below - rather than in a client abort.
const LIVE_QUESTION_TIMEOUT_MS = 920_000
// The pipeline does not stop when the proxy stops waiting: a question that
// outlasted the gateway still finished and was saved (at 691 s, 2026-09-13).
// Before this, that case said "the API is not responding" about an API that was
// busy answering.
const STILL_RUNNING =
  'The proxy stopped waiting before the pipeline finished, but the question is probably still running: the API completes it and saves the answer even after this page stops waiting. Look for it on Verification under the live-qa run in a few minutes.'
const UPLOAD_TIMEOUT_MS = 180_000

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = READ_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal })
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ApiError(
        path === '/questions/ask'
          ? STILL_RUNNING
          : `The API did not respond within ${Math.round(timeoutMs / 1000)} s. It is usually waiting on a dependency - check that the finverify-postgres and finverify-qdrant containers are running.`,
        504,
      )
    }
    throw error
  } finally {
    clearTimeout(timer)
  }
  if (!response.ok) {
    // The backend puts its reason in `detail`, and those reasons are the
    // interesting part - "live QA is disabled because it spends campaign quota"
    // is something the user needs to read, not a generic 503.
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') {
        detail = body.detail
      } else if (Array.isArray(body?.detail)) {
        // FastAPI reports REQUEST VALIDATION failures as an array of
        // ValidationError objects, not a string. Reading only the string form
        // discarded the reason for every 422 and left the user with a bare
        // status line - which is the one class of error where the server has
        // already said exactly what is wrong and where.
        const reasons = body.detail
          .map((item: { loc?: unknown[]; msg?: string }) => {
            const field = Array.isArray(item.loc)
              ? item.loc.filter((p) => p !== 'body' && p !== 'query' && p !== 'path').join('.')
              : ''
            return field ? `${field}: ${item.msg ?? 'invalid'}` : (item.msg ?? 'invalid')
          })
          .filter(Boolean)
        if (reasons.length) detail = reasons.join('; ')
      }
    } catch {
      /* a non-JSON error body is still an error; keep the status line */
    }
    // A gateway status with no JSON body is nginx answering on the API's
    // behalf, which means the API process is not there at all. "502 Bad
    // Gateway" is accurate and tells a reader nothing they can act on, so the
    // one case where the cause is knowable says what it is.
    if (response.status === 504 && path === '/questions/ask') {
      detail = STILL_RUNNING
    } else if ([502, 503, 504].includes(response.status) && detail.startsWith(String(response.status))) {
      // Names both processes that can be behind /api. In the live-question demo
      // the API is a host process started by RUN_LIVE_DEMO.bat, and pointing
      // only at the container sent a presenter to restart the wrong thing.
      detail = `${detail} — the API is not responding. It runs separately from this page: check the finverify-api container, or the live API window opened by RUN_LIVE_DEMO.bat.`
    }
    // 413 is answered by nginx, not the API, so it arrives as an HTML page with
    // no `detail` to read - the user got a bare "413 Request Entity Too Large".
    // It is also the ONLY upload failure the user both causes and can fix, so
    // it is the one that most needs to say the limit. The figure is
    // `client_max_body_size` in docker/nginx.conf; change both together.
    if (response.status === 413) {
      detail =
        'The file is larger than the 64 MB upload limit, so the proxy refused it ' +
        'before the API saw it. Annual reports are normally well under that. A ' +
        'filing that genuinely is larger belongs in configs/corpus.json, the ' +
        'version-controlled manifest the corpus is acquired from, rather than in ' +
        'an upload.'
    }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<HealthOut>('/health'),
  stats: () => request<CorpusStatsOut>('/stats'),
  // The server caps this at 50 unless asked otherwise, and the page renders
  // the result as "the registered corpus" with no note that it is a page - so
  // a 51st filing would simply not exist as far as the UI is concerned.
  documents: (limit = 500) => request<DocumentOut[]>(`/documents?limit=${limit}`),
  arms: () => request<ArmOut[]>('/arms'),
  document: (id: string) => request<DocumentOut>(`/documents/${encodeURIComponent(id)}`),
  evidence: (qid: string) => request<EvidenceOut[]>(`/evidence/${encodeURIComponent(qid)}`),
  answers: (
    params: { run_id?: string; arm?: string; question_id?: string; limit?: number } = {},
  ) => {
    const query = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== '')
        .map(([k, v]) => [k, String(v)]),
    ).toString()
    return request<AnswerSummaryOut[]>(`/answers${query ? `?${query}` : ''}`)
  },
  answer: (id: number) => request<AnswerOut>(`/answers/${id}`),
  verification: (id: number) => request<VerificationOut>(`/verification/${id}`),
  experiments: () => request<ExperimentOut[]>('/experiments'),
  results: (params: { run_id?: string; arm?: string; metric?: string } = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v) as [string, string][],
    ).toString()
    return request<EvaluationResultOut[]>(`/evaluation/results${query ? `?${query}` : ''}`)
  },
  ask: (body: {
    question: string
    arm?: string
    company?: string
    document_id?: string
    top_k?: number
  }) =>
    request<AnswerOut>(
      '/questions/ask',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      LIVE_QUESTION_TIMEOUT_MS,
    ),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{
      document_id: string
      sha256: string
      duplicate: boolean
      indexed: boolean
      next_step: string
    }>('/documents/upload', { method: 'POST', body: form }, UPLOAD_TIMEOUT_MS)
  },
}

/** Format a risk score for display, preserving the null case. */
export function formatRisk(score: number | null): string {
  return score === null ? 'not scored' : score.toFixed(3)
}

/** A metric value that is null was UNDEFINED, which is not zero. */
export function formatMetric(value: number | null, places = 3): string {
  return value === null ? 'undefined' : value.toFixed(places)
}

/**
 * Render an evidence anchor for a human.
 *
 * The API serves `Evidence.anchors` verbatim, and for a table cell that is a
 * JSON array serialised into a string - `["Total non-current assets","48382"]`.
 * Dumping it raw put escaped JSON on screen in the one place a reader is trying
 * to check a figure against its source. Parsed here rather than server-side so
 * the stored anchor stays byte-identical to what the retrieval scorer matches
 * against; only the presentation changes.
 */
export function formatAnchor(text: string | null): string | null {
  if (!text) return null
  const trimmed = text.trim()
  if (!trimmed.startsWith('[')) return trimmed
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (Array.isArray(parsed)) {
      return parsed.map((part) => String(part).trim()).filter(Boolean).join('  ·  ')
    }
  } catch {
    /* not JSON after all - show it as it came */
  }
  return trimmed
}
