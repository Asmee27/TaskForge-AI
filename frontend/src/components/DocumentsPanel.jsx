import { FileText, Search, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  listWorkspaceDocuments,
  retrieveWorkspaceDocuments,
  uploadWorkspaceDocument
} from "../api/client";

export default function DocumentsPanel({ workspace }) {
  const [documents, setDocuments] = useState([]);
  const [query, setQuery] = useState("");
  const [passages, setPassages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef(null);

  useEffect(() => {
    let active = true;
    setPassages([]);
    setError("");
    if (!workspace?.workspace_id) {
      setDocuments([]);
      return () => {
        active = false;
      };
    }

    listWorkspaceDocuments(workspace.workspace_id)
      .then((items) => {
        if (active) setDocuments(items);
      })
      .catch((err) => {
        if (active) setError(err.response?.data?.detail?.message || err.message);
      });

    return () => {
      active = false;
    };
  }, [workspace?.workspace_id]);

  async function upload(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !workspace?.workspace_id) return;
    setBusy(true);
    setError("");
    try {
      const document = await uploadWorkspaceDocument(workspace.workspace_id, file);
      setDocuments((current) => [document, ...current]);
    } catch (err) {
      setError(err.response?.data?.detail?.message || err.message || "Document upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function search(event) {
    event.preventDefault();
    if (!query.trim() || !workspace?.workspace_id) return;
    setBusy(true);
    setError("");
    try {
      const results = await retrieveWorkspaceDocuments(
        workspace.workspace_id,
        query.trim()
      );
      setPassages(results);
    } catch (err) {
      setError(err.response?.data?.detail?.message || err.message || "Document retrieval failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
          <FileText size={15} className="text-cyan-300" />
          Knowledge
        </div>
        <button
          type="button"
          title="Upload PDF"
          disabled={!workspace || busy}
          onClick={() => fileRef.current?.click()}
          className="rounded-lg border border-slate-700 p-1.5 text-slate-400 transition hover:border-cyan-500/50 hover:text-cyan-300 disabled:opacity-50"
        >
          <UploadCloud size={15} />
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          onChange={upload}
        />
      </div>

      <div className="mt-3 text-xs text-slate-500">
        {workspace ? `${documents.length} document${documents.length === 1 ? "" : "s"}` : "Select a workspace"}
      </div>

      {documents.length ? (
        <div className="mt-3 space-y-1.5">
          {documents.slice(0, 5).map((document) => (
            <div key={document.document_id} className="flex items-center gap-2 text-xs text-slate-300">
              <FileText size={13} className="shrink-0 text-slate-500" />
              <span className="truncate">{document.filename}</span>
              <span className="shrink-0 text-slate-600">{document.chunk_count} chunks</span>
            </div>
          ))}
        </div>
      ) : null}

      <form onSubmit={search} className="mt-4 flex gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={!workspace || busy}
          placeholder="Search workspace knowledge"
          className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-500 disabled:opacity-50"
        />
        <button
          type="submit"
          title="Search documents"
          disabled={!query.trim() || busy || !workspace}
          className="rounded-lg border border-slate-700 p-2 text-slate-400 hover:border-cyan-500/50 hover:text-cyan-300 disabled:opacity-50"
        >
          <Search size={14} />
        </button>
      </form>

      {error ? <div className="mt-3 text-xs leading-5 text-rose-300">{error}</div> : null}

      {passages.length ? (
        <div className="mt-3 space-y-2">
          {passages.map((passage) => (
            <div key={`${passage.document_id}-${passage.page}-${passage.chunk}`} className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5 text-xs">
              <div className="font-medium text-cyan-200">{passage.citation}</div>
              <div className="mt-1 line-clamp-4 leading-5 text-slate-400">{passage.text}</div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
