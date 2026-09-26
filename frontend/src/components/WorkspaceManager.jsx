import {
  Building2,
  Database,
  FileSpreadsheet,
  Plus,
  UploadCloud,
  X
} from "lucide-react";
import { useRef, useState } from "react";

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

export default function WorkspaceManager({
  workspaces,
  selectedWorkspace,
  onSelect,
  onCreate,
  creating
}) {
  const [showCreate, setShowCreate] =
    useState(false);

  const [companyName, setCompanyName] =
    useState("");

  const [businessType, setBusinessType] =
    useState("");

  const [currency, setCurrency] =
    useState("");

  const [timezone, setTimezone] =
    useState("");

  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);

  async function submit(event) {
    event.preventDefault();

    if (
      !companyName.trim() ||
      !files.length
    ) {
      return;
    }

    const workspace = await onCreate({
      companyName: companyName.trim(),
      businessType:
        businessType.trim(),
      currency:
        currency.trim(),
      timezone:
        timezone.trim(),
      files
    });

    if (workspace) {
      setCompanyName("");
      setBusinessType("");
      setCurrency("");
      setTimezone("");
      setFiles([]);
      setShowCreate(false);
    }
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
          Data Workspace
        </div>

        <button
          type="button"
          onClick={() =>
            setShowCreate(
              (value) => !value
            )
          }
          className="rounded-lg border border-slate-700 p-1.5 text-slate-400 transition hover:border-indigo-500/50 hover:text-indigo-300"
          title="Create workspace"
        >
          {showCreate ? (
            <X size={15} />
          ) : (
            <Plus size={15} />
          )}
        </button>
      </div>

      {!showCreate ? (
        <>
          <select
            value={
              selectedWorkspace?.workspace_id ||
              ""
            }
            onChange={(event) =>
              onSelect(
                event.target.value
              )
            }
            className="mt-4 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none focus:border-indigo-500"
          >
            {workspaces.map(
              (workspace) => (
                <option
                  key={
                    workspace.workspace_id
                  }
                  value={
                    workspace.workspace_id
                  }
                >
                  {workspace.company_name ||
                    workspace.workspace_id}
                </option>
              )
            )}
          </select>

          {selectedWorkspace ? (
            <div className="mt-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-indigo-500/30 bg-indigo-500/10 text-indigo-300">
                  {selectedWorkspace.source ===
                  "uploaded" ? (
                    <Building2
                      size={18}
                    />
                  ) : (
                    <Database
                      size={18}
                    />
                  )}
                </div>

                <div className="min-w-0">
                  <div className="truncate font-semibold text-white">
                    {selectedWorkspace.company_name ||
                      selectedWorkspace.workspace_id}
                  </div>

                  <div className="truncate text-xs text-slate-500">
                    {selectedWorkspace.source ===
                    "uploaded"
                      ? "Uploaded business workspace"
                      : "Built-in demo workspace"}
                  </div>
                </div>
              </div>

              <div className="mt-4 space-y-2 text-xs">
                <div className="flex justify-between gap-3">
                  <span className="text-slate-500">
                    Business
                  </span>
                  <span className="truncate text-right text-slate-300">
                    {selectedWorkspace.business_type ||
                      "Unknown"}
                  </span>
                </div>

                <div className="flex justify-between gap-3">
                  <span className="text-slate-500">
                    Currency
                  </span>
                  <span className="text-slate-300">
                    {selectedWorkspace.currency ||
                      "Unknown"}
                  </span>
                </div>

                {selectedWorkspace.summary ? (
                  <>
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">
                        Tables
                      </span>
                      <span className="text-slate-300">
                        {formatNumber(
                          selectedWorkspace
                            .summary
                            .tables_created
                        )}
                      </span>
                    </div>

                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">
                        Rows
                      </span>
                      <span className="text-slate-300">
                        {formatNumber(
                          selectedWorkspace
                            .summary.total_rows
                        )}
                      </span>
                    </div>
                  </>
                ) : null}
              </div>

              {selectedWorkspace.datasets?.length ? (
                <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Detected tables
                  </div>

                  <div className="space-y-1.5">
                    {selectedWorkspace.datasets
                      .slice(0, 4)
                      .map(
                        (dataset) => (
                          <div
                            key={
                              dataset.table_name
                            }
                            className="flex items-center justify-between gap-2 text-xs"
                          >
                            <span className="truncate text-slate-300">
                              {
                                dataset.table_name
                              }
                            </span>
                            <span className="text-slate-600">
                              {formatNumber(
                                dataset.row_count
                              )}
                            </span>
                          </div>
                        )
                      )}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      ) : (
        <form
          onSubmit={submit}
          className="mt-4 space-y-3"
        >
          <div>
            <label className="text-xs text-slate-500">
              Company name *
            </label>
            <input
              value={companyName}
              onChange={(event) =>
                setCompanyName(
                  event.target.value
                )
              }
              placeholder="Acme Retail"
              className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-indigo-500"
            />
          </div>

          <div>
            <label className="text-xs text-slate-500">
              Business type
            </label>
            <input
              value={businessType}
              onChange={(event) =>
                setBusinessType(
                  event.target.value
                )
              }
              placeholder="retail / logistics / SaaS"
              className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-indigo-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-slate-500">
                Currency
              </label>
              <input
                value={currency}
                onChange={(event) =>
                  setCurrency(
                    event.target.value
                  )
                }
                placeholder="ISO 4217 code (optional)"
                className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm uppercase text-white outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="text-xs text-slate-500">
                Timezone
              </label>
              <input
                value={timezone}
                onChange={(event) =>
                  setTimezone(
                    event.target.value
                  )
                }
                placeholder="Asia/Kolkata"
                className="mt-1 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <button
            type="button"
            onClick={() =>
              fileRef.current?.click()
            }
            className="flex w-full flex-col items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/70 px-3 py-4 text-center transition hover:border-indigo-500/50"
          >
            <UploadCloud
              size={21}
              className="text-indigo-300"
            />
            <span className="mt-2 text-xs font-medium text-slate-300">
              Upload CSV / XLSX
            </span>
            <span className="mt-1 text-[10px] text-slate-600">
              Multiple files supported
            </span>
          </button>

          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".csv,.xlsx,.xls"
            className="hidden"
            onChange={(event) =>
              setFiles(
                Array.from(
                  event.target.files || []
                )
              )
            }
          />

          {files.length ? (
            <div className="space-y-1.5">
              {files.map((file) => (
                <div
                  key={`${file.name}-${file.size}`}
                  className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-2.5 py-2 text-xs text-slate-400"
                >
                  <FileSpreadsheet
                    size={14}
                    className="shrink-0 text-cyan-300"
                  />
                  <span className="truncate">
                    {file.name}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={
              creating ||
              !companyName.trim() ||
              !files.length
            }
            className="w-full rounded-xl bg-indigo-500 px-3 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            {creating
              ? "Building workspace..."
              : "Create Data Workspace"}
          </button>
        </form>
      )}
    </div>
  );
}
