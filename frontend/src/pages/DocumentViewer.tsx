import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { ErrorBox, Loading } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * Screen 3 of spec §29.
 *
 * Shows the document's identity and provenance rather than rendering the PDF.
 * The bytes are deliberately not redistributed (`documents/raw/` is gitignored -
 * these are third-party annual reports whose licence is the publisher's), so the
 * viewer links to the original source instead of serving a copy. What it can
 * show honestly is the chain a reader needs to fetch the same file and confirm
 * it is the same file.
 */
export default function DocumentViewer() {
  const { id = '' } = useParams()
  const document = useAsync(() => api.document(id), [id])

  return (
    <section>
      <h2>Document</h2>
      <p className="note">
        <Link to="/documents">← all documents</Link>
      </p>

      {document.loading && <Loading what="the document" />}
      {document.error && <ErrorBox title="Could not load document" message={document.error} />}

      {document.data && (
        <>
          <div className="grid">
            <div className="panel stat">
              <div className="label">Company</div>
              <div className="value text">{document.data.company ?? 'unknown'}</div>
            </div>
            <div className="panel stat">
              <div className="label">Fiscal year</div>
              <div className="value text">{document.data.fiscal_year ?? 'unknown'}</div>
            </div>
            <div className="panel stat">
              <div className="label">Pages</div>
              <div className="value">{document.data.page_count ?? '—'}</div>
            </div>
          </div>

          <h3>Provenance</h3>
          <div className="panel">
            <table>
              <tbody>
                <tr>
                  <th>Document id</th>
                  <td>
                    <code>{document.data.document_id}</code>
                  </td>
                </tr>
                <tr>
                  <th>File</th>
                  <td>{document.data.filename}</td>
                </tr>
                <tr>
                  <th>SHA-256</th>
                  <td>
                    <code style={{ wordBreak: 'break-all' }}>{document.data.sha256}</code>
                  </td>
                </tr>
                <tr>
                  <th>Source</th>
                  <td>
                    {document.data.source_url ? (
                      <a
                        href={document.data.source_url}
                        rel="noreferrer noopener"
                        target="_blank"
                        style={{ overflowWrap: 'anywhere' }}
                      >
                        {document.data.source_url}
                      </a>
                    ) : (
                      'not recorded'
                    )}
                  </td>
                </tr>
                <tr>
                  <th>Retrieved</th>
                  <td>{document.data.retrieved_on ?? 'not recorded'}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="note">
            The PDF itself is not served. These are third-party annual reports and
            the repository does not redistribute them; the hash above is what lets
            a reader confirm the file they fetch from the source is the file this
            evaluation used.
          </p>
        </>
      )}
    </section>
  )
}
