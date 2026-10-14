# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: frontend/e2e/web-platform.spec.ts >> mantiene visibles los KPI y gráficos del dashboard en una pantalla móvil
- Location: frontend/e2e/web-platform.spec.ts:1007:1

# Error details

```
Error: page.goto: Protocol error (Page.navigate): Cannot navigate to invalid URL
Call log:
  - navigating to "/", waiting until "load"

```

# Test source

```ts
  910  |             provenance: { id: "evidence-1", source: "trello", project_id: "hormigestio", timestamp: summary.captured_at },
  911  |             category: "task",
  912  |             summary: "Prueba observada en el tablero",
  913  |           },
  914  |         ],
  915  |         history: [
  916  |           {
  917  |             id: "change-1",
  918  |             task_id: "task-1",
  919  |             kind: "field_changed",
  920  |             field: "state",
  921  |             before: "pending",
  922  |             after: "en_curso",
  923  |             previous_evidence_ids: [],
  924  |             current_evidence_ids: ["evidence-1"],
  925  |             actor: null,
  926  |           },
  927  |         ],
  928  |       });
  929  |       return;
  930  |     }
  931  | 
  932  |     const taskDetailMatch = path.match(/\/projects\/hormigestio\/tasks\/([^/]+)$/);
  933  |     if (taskDetailMatch) {
  934  |       const task = allTasks.find((item) => item.id === taskDetailMatch[1]);
  935  |       if (task) {
  936  |         await json(route, task);
  937  |         return;
  938  |       }
  939  |     }
  940  | 
  941  |     if (path.endsWith("/projects/hormigestio/tasks")) {
  942  |       if (url.searchParams.get("critical_only") === "true" && options.criticalStatus) {
  943  |         await json(route, { error: { code: "temporary_unavailable", message: "El filtro no está disponible." } }, options.criticalStatus);
  944  |         return;
  945  |       }
  946  |       const pageNumber = Number(url.searchParams.get("page") ?? 1);
  947  |       const requestedPageSize = Number(url.searchParams.get("page_size") ?? 25);
  948  |       let filtered = [...allTasks];
  949  |       const q = url.searchParams.get("q")?.toLocaleLowerCase("es-EC");
  950  |       if (q) filtered = filtered.filter((task) => task.title.toLocaleLowerCase("es-EC").includes(q));
  951  |       const state = url.searchParams.get("state");
  952  |       if (state) filtered = filtered.filter((task) => task.state === state);
  953  |       const assigneeId = url.searchParams.get("assignee_id");
  954  |       if (assigneeId) filtered = filtered.filter((task) => task.assignee_ids.includes(assigneeId));
  955  |       const listName = url.searchParams.get("list_name");
  956  |       if (listName) filtered = filtered.filter((task) => task.list_name === listName);
  957  |       if (url.searchParams.get("critical_only") === "true") filtered = filtered.filter((task) => task.is_critical);
  958  |       if (url.searchParams.get("overdue_only") === "true") filtered = filtered.filter((task) => task.is_overdue);
  959  |       if (url.searchParams.get("due_soon_only") === "true") filtered = filtered.filter((task) => task.is_due_soon);
  960  |       const sort = url.searchParams.get("sort");
  961  |       const direction = url.searchParams.get("direction") === "desc" ? -1 : 1;
  962  |       if (sort === "title") filtered.sort((left, right) => left.title.localeCompare(right.title) * direction);
  963  |       if (sort === "due_date") filtered.sort((left, right) => (left.due_date ?? "").localeCompare(right.due_date ?? "") * direction);
  964  |       const offset = (pageNumber - 1) * requestedPageSize;
  965  |       await json(route, {
  966  |         items: filtered.slice(offset, offset + requestedPageSize),
  967  |         total: filtered.length,
  968  |         page: pageNumber,
  969  |         page_size: requestedPageSize,
  970  |       });
  971  |       return;
  972  |     }
  973  | 
  974  |     await json(route, { error: { code: "not_found", message: `Sin fixture para ${path}` } }, 404);
  975  |   });
  976  |   return {
  977  |     analysisWrites,
  978  |     governanceRequests,
  979  |     reportDecisionRequests,
  980  |     weeklyCycleRequests,
  981  |     moduleReads,
  982  |     downloadRequests,
  983  |     get jobPollRequests() { return jobPollRequests; },
  984  |     get analysisJobListReads() { return analysisJobListReads; },
  985  |   };
  986  | }
  987  | 
  988  | test("el dashboard enlaza estados y señales desde conteos del backend sin sumar categorías solapadas", async ({ page }) => {
  989  |   await installApi(page);
  990  |   await page.goto("/");
  991  | 
  992  |   await expect(page.getByRole("link", { name: /Pendientes/ }).getByText("0", { exact: true })).toBeVisible();
  993  |   await expect(page.getByRole("link", { name: /En curso/ }).getByText("1", { exact: true })).toBeVisible();
  994  |   await expect(page.getByRole("link", { name: /Próximas a vencer/ }).getByText("1", { exact: true })).toBeVisible();
  995  |   await expect(page.getByRole("link", { name: /Actividades críticas/ }).getByText("1", { exact: true })).toBeVisible();
  996  |   await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
  997  |   await expect(page.getByRole("img", { name: "Señales de actividad crítica por categoría" })).toBeVisible();
  998  |   await expect(page.getByText("Las categorías de alerta se solapan; no deben sumarse como un total.")).toBeVisible();
  999  |   await expect(page.getByRole("link", { name: /Actividades críticas/ })).toHaveAttribute("href", "/tasks?critical_only=true");
  1000 | 
  1001 |   await page.getByRole("link", { name: /Pendientes/ }).click();
  1002 |   await expect(page).toHaveURL(/state=pending/);
  1003 |   const selectedState = await page.locator('select[aria-label="Estado"]').evaluate((element) => (element as HTMLSelectElement).value);
  1004 |   expect(selectedState).toBe("pending");
  1005 | });
  1006 | 
  1007 | test("mantiene visibles los KPI y gráficos del dashboard en una pantalla móvil", async ({ page }) => {
  1008 |   await installApi(page);
  1009 |   await page.setViewportSize({ width: 390, height: 844 });
> 1010 |   await page.goto("/");
       |              ^ Error: page.goto: Protocol error (Page.navigate): Cannot navigate to invalid URL
  1011 | 
  1012 |   await expect(page.getByRole("link", { name: /Actividades críticas/ })).toBeVisible();
  1013 |   await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
  1014 |   await expect(page.getByRole("img", { name: "Señales de actividad crítica por categoría" })).toBeVisible();
  1015 |   const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  1016 |   expect(hasHorizontalOverflow).toBe(false);
  1017 | });
  1018 | 
  1019 | test("los centros de análisis y calidad conservan el layout móvil sin desbordamiento", async ({ page }) => {
  1020 |   await installApi(page);
  1021 |   await page.setViewportSize({ width: 390, height: 844 });
  1022 |   await page.goto("/analysis?run_id=run-current");
  1023 |   await expect(page.getByRole("heading", { name: "Ejecución run-current" })).toBeVisible();
  1024 |   let overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  1025 |   expect(overflow).toBe(false);
  1026 | 
  1027 |   await page.goto("/quality");
  1028 |   await expect(page.getByRole("heading", { name: "Centro de calidad de código" })).toBeVisible();
  1029 |   await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  1030 |   await expect(page.getByRole("dialog", { name: /Hallazgo candidato/ })).toBeVisible();
  1031 |   overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  1032 |   expect(overflow).toBe(false);
  1033 | });
  1034 | 
  1035 | test("no convierte conteos de estado ausentes en ceros locales", async ({ page }) => {
  1036 |   await installApi(page, { summaryPayload: { ...summary, state_counts: { en_curso: 1 } } });
  1037 |   await page.goto("/");
  1038 | 
  1039 |   await expect(page.getByRole("link", { name: /Pendientes/ }).getByText("Sin dato")).toBeVisible();
  1040 |   await expect(page.getByRole("link", { name: /En curso/ }).getByText("1", { exact: true })).toBeVisible();
  1041 |   await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
  1042 | });
  1043 | 
  1044 | test("no dibuja tendencias temporales a partir de una sola captura", async ({ page }) => {
  1045 |   await installApi(page, { historyPayload: { ...history, items: [history.items[0]], total: 1 } });
  1046 |   await page.goto("/");
  1047 | 
  1048 |   const historySection = page.locator(".history-section");
  1049 |   await expect(historySection.getByRole("heading", { name: "Una sola captura disponible" })).toBeVisible();
  1050 |   await expect(historySection.getByRole("img")).toHaveCount(0);
  1051 | });
  1052 | 
  1053 | test("un error en la consulta crítica permanece desconocido y no se presenta como cero", async ({ page }) => {
  1054 |   await installApi(page, { criticalStatus: 503 });
  1055 |   await page.goto("/");
  1056 | 
  1057 |   const criticalMetric = page.getByRole("link", { name: /Actividades críticas/ });
  1058 |   await expect(criticalMetric.getByText("Sin dato")).toBeVisible();
  1059 |   await expect(criticalMetric.getByText("0", { exact: true })).toHaveCount(0);
  1060 |   await expect(page.getByRole("heading", { name: "No se pudieron cargar las tareas críticas" })).toBeVisible();
  1061 | });
  1062 | 
  1063 | test("el dashboard presenta políticas e historial de ejecuciones solo con registros reales", async ({ page }) => {
  1064 |   await installApi(page);
  1065 |   await page.goto("/");
  1066 | 
  1067 |   await expect(page.getByRole("heading", { name: "Informe y políticas de calidad" })).toBeVisible();
  1068 |   await expect(page.getByText("Revisar los hallazgos con evidencia insuficiente.", { exact: true })).toBeVisible();
  1069 |   const policyPanel = page.locator(".executive-policies-card");
  1070 |   await expect(policyPanel.getByText("Q1", { exact: true })).toBeVisible();
  1071 |   await expect(policyPanel.getByText("Q2", { exact: true })).toBeVisible();
  1072 |   await expect(policyPanel.getByText("Q3", { exact: true })).toHaveCount(0);
  1073 |   await expect(policyPanel.getByText("Desconocido", { exact: true })).toBeVisible();
  1074 |   await expect(policyPanel.getByText("Observado: Sin dato", { exact: true })).toBeVisible();
  1075 |   await expect(page.locator(".executive-report-card").getByText(/Fecha de corte:/)).toBeVisible();
  1076 |   await expect(page.getByRole("img", { name: "Historial de análisis por estado" })).toBeVisible();
  1077 | });
  1078 | 
  1079 | test("el centro de IA consulta el historial sin iniciar trabajos al abrirlo", async ({ page }) => {
  1080 |   const apiObserver = await installApi(page);
  1081 |   const { analysisWrites } = apiObserver;
  1082 |   await page.goto("/analysis");
  1083 | 
  1084 |   await expect(page.getByRole("heading", { name: "Centro de análisis de IA" })).toBeVisible();
  1085 |   await expect(page.getByText("Historial de ejecuciones", { exact: true })).toBeVisible();
  1086 |   await expect(page.getByText("run-current", { exact: true })).toBeVisible();
  1087 |   await expect(page.getByRole("button", { name: "Preparar análisis" })).toBeDisabled();
  1088 |   await expect(page.getByRole("heading", { name: "Historial de trabajos protegido" })).toBeVisible();
  1089 |   expect(analysisWrites).toHaveLength(0);
  1090 |   expect(apiObserver.analysisJobListReads).toBe(0);
  1091 | });
  1092 | 
  1093 | test("una etapa desconocida no se presenta como omitida y una omisión requiere registro explícito", async ({ page }) => {
  1094 |   await installApi(page);
  1095 |   await page.goto("/analysis?run_id=run-current");
  1096 | 
  1097 |   await expect(page.getByText("No registrado; estado desconocido", { exact: true })).toBeVisible();
  1098 |   await expect(page.getByText("Omitida explícitamente", { exact: true })).toBeVisible();
  1099 |   await expect(page.getByText("Fuentes almacenadas consultadas", { exact: true })).toBeVisible();
  1100 |   await expect(page.getByText("local · fixture-monitor", { exact: true })).toBeVisible();
  1101 | });
  1102 | 
  1103 | test("la calidad conserva hallazgos candidatos, verificaciones y fragmentos como texto", async ({ page }) => {
  1104 |   await installApi(page);
  1105 |   await page.goto("/quality");
  1106 | 
  1107 |   await expect(page.getByRole("heading", { name: "Centro de calidad de código" })).toBeVisible();
  1108 |   await expect(page.getByText("Hallazgo candidato", { exact: true })).toBeVisible();
  1109 |   await expect(page.locator(".finding-list").getByText("Evidencia insuficiente", { exact: true })).toBeVisible();
  1110 |   await expect(page.getByText(/CI.*no disponible/i)).toBeVisible();
```