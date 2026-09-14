import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { Empty, ErrorBox, Loading } from '../lib/Status'
import { useAsync } from '../lib/useAsync'

/**
 * Screens 2 and part of 3 of spec §29: the corpus, and adding to it.
 *
 * The upload result deliberately does NOT say "done". The API returns 202
 * because the file is registered but not extracted, chunked, embedded or
 * indexed - which on this hardware is minutes to hours - and a green tick here
 * would tell the user the document is searchable when it is not.
 */
export default function DocumentsPage() {
  const documents = useAsync(() => api.documents(), [])
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)

  async function onUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input = event.currentTarget.elements.namedItem('file') as HTMLInputElement
    const file = input.files?.[0]
    if (!file) {
      // Previously a bare `return`: pressing Register with no file selected did
      // nothing at all, with no message, which is indistinguishable from a
      // broken button.
      setUploadError('Choose a PDF first — no file is selected.')
      setResult(null)
      return
    }
    setUploading(true)
    setResult(null)
    setUploadError(null)
    try {
      const body = await api.upload(file)
      setResult(
        body.duplicate
          ? `Already registered as ${body.document_id} — identity is the file's bytes, not its name.`
          : `Registered as ${body.document_id}. ${body.next_step}`,
      )
      documents.reload()
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }

  return (
    <section>
      <h2>Documents</h2>
      <p className="note">
        Provenance travels with every document: source URL, retrieval date and
        SHA-256, so a reader can obtain the same bytes.
      </p>

      <form onSubmit={onUpload}>
        <label htmlFor="file">Add an annual report (PDF)</label>
        <input id="file" name="file" type="file" accept="application/pdf" />
        <button type="submit" disabled={uploading}>
          {uploading ? 'Uploading…' : 'Register document'}
        </button>
      </form>
      {result && (
        <div className="panel" style={{ marginTop: 12 }}>
          <p className="note" style={{ margin: 0 }}>{result}</p>
        </div>
      )}
      {uploadError && <ErrorBox title="Upload refused" message={uploadError} />}

      <h3>Registered corpus</h3>
      {documents.loading && <Loading what="documents" />}
      {documents.error && <ErrorBox title="Could not load documents" message={documents.error} />}
      {documents.data && documents.data.length === 0 && (
        <Empty
          what="documents"
          hint="Run scripts/ingest_to_database.py --registry to load the acquired corpus."
        />
      )}
      {documents.data && documents.data.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Year</th>
                <th>File</th>
                <th className="num">Pages</th>
                <th>SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {documents.data.map((doc) => (
                <tr key={doc.document_id}>
                  <td>
                    <Link to={`/documents/${doc.document_id}`}>
                      {doc.company ?? doc.document_id}
                    </Link>
                  </td>
                  <td>{doc.fiscal_year ?? '—'}</td>
                  <td>{doc.filename}</td>
                  <td className="num">{doc.page_count ?? '—'}</td>
                  <td>
                    <code>{doc.sha256.slice(0, 12)}…</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
