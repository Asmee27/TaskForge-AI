import axios from "axios";

export const API_BASE =
  import.meta.env.VITE_API_BASE_URL ||
  "http://127.0.0.1:8000";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 180000
});

export async function listWorkspaces() {
  const response = await api.get(
    "/api/workspaces"
  );
  return response.data.workspaces || [];
}

export async function getAdminOverview() {
  const response = await api.get("/api/admin/overview");
  return response.data.data;
}

export async function createWorkspace({
  companyName,
  businessType,
  currency,
  timezone,
  files
}) {
  const form = new FormData();

  form.append("company_name", companyName);

  if (businessType) {
    form.append(
      "business_type",
      businessType
    );
  }

  if (currency) {
    form.append(
      "currency",
      currency
    );
  }

  if (timezone) {
    form.append(
      "timezone",
      timezone
    );
  }

  for (const file of files) {
    form.append(
      "files",
      file
    );
  }

  const response = await api.post(
    "/api/workspaces",
    form,
    {
      headers: {
        "Content-Type": "multipart/form-data"
      }
    }
  );

  return response.data.workspace;
}

export async function listWorkspaceDocuments(workspaceId) {
  const response = await api.get(
    `/api/workspaces/${workspaceId}/documents`
  );
  return response.data.documents || [];
}

export async function uploadWorkspaceDocument(workspaceId, file) {
  const form = new FormData();
  form.append("file", file);
  const response = await api.post(
    `/api/workspaces/${workspaceId}/documents`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } }
  );
  return response.data.document;
}

export async function retrieveWorkspaceDocuments(workspaceId, query, topK = 5) {
  const response = await api.post(
    `/api/workspaces/${workspaceId}/documents/retrieve`,
    { query, top_k: topK }
  );
  return response.data.passages || [];
}

export async function startWorkflow(payload) {
  const response = await api.post(
    "/api/workflows/start",
    payload,
    {
      headers: {
        "Content-Type": "application/json"
      }
    }
  );

  return response.data;
}

export function openTrace(
  runId,
  handlers = {}
) {
  const source = new EventSource(
    `${API_BASE}/api/workflows/${runId}/events`
  );

  source.addEventListener(
    "trace",
    (event) => {
      const data = JSON.parse(
        event.data
      );
      handlers.onTrace?.(data);
    }
  );

  source.addEventListener(
    "complete",
    (event) => {
      const data = JSON.parse(
        event.data
      );
      handlers.onComplete?.(data);
      source.close();
    }
  );

  source.onerror = (error) => {
    handlers.onError?.(error);
  };

  return () => source.close();
}

export async function getWorkflowResult(
  runId
) {
  const response = await api.get(
    `/api/workflows/${runId}/result`
  );
  return response.data;
}

export async function getWorkflowIntelligence(runId) {
  const response = await api.get(
    `/api/workflows/${runId}/intelligence`
  );
  return response.data;
}

export async function simulateWorkflow(runId, scenario) {
  const response = await api.post(
    `/api/workflows/${runId}/simulate`,
    { scenario }
  );
  return response.data;
}

export async function approveAction(
  requestId,
  actor = "dashboard-user"
) {
  const response = await api.post(
    `/api/actions/${requestId}/approve`,
    { actor },
    {
      headers: {
        "Content-Type": "application/json"
      }
    }
  );
  return response.data;
}

export async function rejectAction(
  requestId,
  reason,
  actor = "dashboard-user"
) {
  const response = await api.post(
    `/api/actions/${requestId}/reject`,
    {
      actor,
      reason
    },
    {
      headers: {
        "Content-Type": "application/json"
      }
    }
  );
  return response.data;
}

export async function executeAction(
  requestId
) {
  const response = await api.post(
    `/api/actions/${requestId}/execute`
  );
  return response.data;
}
