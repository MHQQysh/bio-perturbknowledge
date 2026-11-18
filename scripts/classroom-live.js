(() => {
  const byId = id => document.getElementById(id);
  const start = byId("live-start");
  const open = byId("live-open");
  const provider = byId("live-provider");
  const model = byId("live-model");
  const apiKey = byId("live-api-key");
  const status = byId("live-status");
  const trace = byId("live-trace");
  const notice = byId("live-notice");
  const dialog = byId("live-dialog");
  const body = byId("live-dialog-content");
  const receiptDialog = byId("receipt-dialog");
  const receiptBody = byId("receipt-dialog-content");
  const auditStudy = byId("audit-study");
  const auditRecord = byId("audit-record");
  const rows = new Map();
  const kinds = {skill: "Skill", tool: "Tool", search: "搜索", model: "模型"};
  const archivedReceipt = "reports/workflow_runs/classroom-demo-20261118/lalli-receipt.json";
  const jostFolder = "reports/workflow_runs/live-20261118-112a53e7";
  const studies = {
    lalli: {
      label: "Lalli", scope: "课堂演示的 3 个条件。完成运行后优先显示本轮核验结果。",
      records: {ADNP:[185,263,448,33756], ARID1B:[130,263,393,33756], CHD8:[279,263,542,33756]},
    },
    jost: {
      label: "Jost", scope: "3 条归档实算记录，使用原归档读取回执。",
      records: {GNB2L1:[173,779,952,33694], MTOR:[122,779,901,33694], DUT:[116,779,895,33694]},
    },
    replogle: {
      label: "Replogle", scope: "1 条退回案例，可查看联接结果及表达尺度缺证原因。",
      records: {TARDBP:[2,3,5,8749]},
    },
  };
  let source, currentRun, output = null, available = false, completed = false, activeDialog;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  function setStatus(label, state) {
    status.textContent = label;
    status.dataset.state = state;
  }

  function setNotice(text) {
    notice.hidden = !text;
    notice.textContent = text || "";
  }

  function links(target, files, gene) {
    for (const [label, path] of Object.entries(files || {})) {
      const url = new URL(path, location.href);
      if (!["https:", "http:", "file:"].includes(url.protocol)) continue;
      if (["核验回执", "矩阵核验", "退回回执"].includes(label) && url.pathname.endsWith(".json")) {
        const button = node("button", label + " ›", "live-button");
        button.type = "button";
        button.addEventListener("click", () => inspectReceipt(label, url, gene));
        target.append(button);
        continue;
      }
      const button = node("button", label + " ›", "live-button");
      button.type = "button";
      button.addEventListener("click", () => inspectFile(label, url));
      target.append(button);
    }
  }

  const format = value => typeof value === "number" ? value.toLocaleString("zh-CN") : "—";
  const dimensions = shape => Array.isArray(shape) ? shape.map(format).join(" × ") : "—";

  async function inspectFile(label, url) {
    byId("receipt-dialog-title").textContent = label;
    receiptBody.replaceChildren(node("p", "正在读取文件…"));
    if (!receiptDialog.open) receiptDialog.showModal();
    try {
      const response = await fetch(url);
      if (!response.ok) throw Error("HTTP " + response.status);
      const blob = await response.blob();
      receiptBody.replaceChildren();
      if (/\.(npz|pptx)$/i.test(url.pathname)) {
        receiptBody.append(node("p", "这是二进制文件，可下载后使用对应程序打开。"));
      } else {
        const pre = node("pre", await blob.text(), "receipt-raw");
        pre.style.cssText = "white-space:pre;overflow:auto;max-height:60vh;font:12px/1.6 Consolas,monospace";
        receiptBody.append(pre);
      }
      const save = node("a", "下载原文件", "live-button");
      const blobUrl = URL.createObjectURL(blob);
      save.href = blobUrl;
      save.download = url.pathname.split("/").at(-1);
      save.addEventListener("click", () => setTimeout(() => URL.revokeObjectURL(blobUrl), 1000));
      receiptBody.append(save);
    } catch (error) { receiptBody.replaceChildren(node("p", "文件读取失败：" + error.message)); }
  }

  function receiptSummary(target, data, gene) {
    if (Array.isArray(data.comparisons)) {
      const chosen = gene && data.comparisons.find(item => item.record_id.endsWith("_" + gene));
      target.append(node("p", `源矩阵 ${dimensions(data.source_matrix_shape)}。可唯一匹配作者标签的细胞 ${format(data.single_label_overlap)} 个。`));
      const wrapper = node("div", null, "scroll-table receipt-table");
      const table = node("table");
      const headings = node("tr");
      for (const label of ["比较记录", "扰动", "对照", "矩阵", "准入结论"]) {
        const cell = node("th", label);
        cell.scope = "col";
        headings.append(cell);
      }
      const thead = node("thead");
      thead.append(headings);
      const tbody = node("tbody");
      for (const item of data.comparisons) {
        const row = node("tr");
        if (item === chosen) row.className = "receipt-selected";
        const controls = item.decision === "admitted_for_descriptive_analysis" ? "可做描述性比较" : item.decision || "待确认";
        for (const value of [item.record_id.split("_").at(-1), format(item.n_perturbed), format(item.n_control), dimensions(item.shape), controls]) {
          row.append(node("td", value));
        }
        tbody.append(row);
      }
      table.append(thead, tbody);
      wrapper.append(table);
      target.append(wrapper);
      if (chosen?.checks) {
        const yes = value => value === true ? "通过" : "待核对";
        const checks = chosen.checks;
        target.append(node("p", `${gene} 核验：目标标签${yes(checks.target_annotation_checked)}，对照标签${yes(checks.control_annotation_checked)}，样本一致性${yes(checks.context_fields_checked?.sample)}。`, "receipt-note"));
      }
      if (data.paper_day_disagreement) {
        target.append(node("p", "时间口径：GEO 的 Run 2 样本标为第 8 天，论文在研究层面描述第 7 天。记录保留两处来源，计算按此 GEO 样本分组。", "receipt-note"));
      }
    } else if (data.result?.groups) {
      const result = data.result;
      target.append(node("p", data.exit_code === 0 ? "归档读取成功。" : "归档读取未通过。"),
        node("p", `${result.record_id}：扰动 ${format(result.groups.perturbed)} 个，对照 ${format(result.groups.control)} 个，读取矩阵 ${dimensions(result.shape)}。`));
    } else if (data.feedback) {
      target.append(node("p", "这条记录仅用于展示，表达尺度仍需补证。"),
        node("p", `待核查字段：${data.feedback.field}。需要与该数据版本对应的处理说明，确认矩阵 X 采用了什么表达尺度。`));
    }
  }

  async function inspectReceipt(label, url, gene) {
    byId("receipt-dialog-title").textContent = gene ? `${gene} · ${label}` : label;
    const summary = node("div");
    const files = node("div", null, "live-actions");
    links(files, {"单独打开原始 JSON":url.href});
    receiptBody.replaceChildren(summary, files);
    if (!receiptDialog.open) receiptDialog.showModal();
    receiptDialog.scrollTop = 0;
    if (url.protocol === "file:") {
      summary.append(node("p", "本地文件模式可单独打开原始回执。在实时运行台中可直接预览。"));
      return;
    }
    summary.append(node("p", "正在读取回执…"));
    try {
      if (url.origin !== location.origin) throw new Error("回执需要在当前运行台读取");
      const response = await fetch(url);
      if (!response.ok) throw new Error("找不到回执文件");
      const data = await response.json();
      if (!summary.isConnected) return;
      summary.replaceChildren();
      receiptSummary(summary, data, gene);
      const raw = node("details", null, "receipt-raw");
      raw.append(node("summary", "展开原始 JSON"), node("pre", JSON.stringify(data, null, 2)));
      summary.append(raw);
    } catch (error) {
      summary.replaceChildren(node("p", `${error.message}。仍可单独打开原始回执。`));
    }
  }

  function showDialog(title, kind) {
    byId("live-dialog-title").textContent = title;
    byId("live-dialog-kind").textContent = kind;
    body.replaceChildren();
    if (!dialog.open) dialog.showModal();
  }

  function showPayload(label, value) {
    body.append(node("h4", label), node("pre", JSON.stringify(value, null, 2)));
  }

  function inspectCall(id) {
    const entry = rows.get(id);
    if (!entry) return;
    activeDialog = id;
    const result = entry.result;
    showDialog(entry.event.title, kinds[entry.kind] + (result ? (result.status === "ok" ? " · 已完成" : " · 失败") : " · 执行中"));
    showPayload("调用输入", entry.event.input);
    if (result) {
      showPayload("工具回执", result.output);
      if (result.output.files) {
        const fileLinks = node("div", null, "live-actions");
        links(fileLinks, result.output.files);
        body.append(fileLinks);
      }
    } else {
      body.append(node("p", "等待此步骤返回。"));
    }
  }

  function refreshRecordOptions(preferred) {
    const study = studies[auditStudy.value];
    auditRecord.replaceChildren(...Object.keys(study.records).map(gene => {
      const option = node("option", gene);
      option.value = gene;
      return option;
    }));
    if (preferred && Object.hasOwn(study.records, preferred)) auditRecord.value = preferred;
    byId("audit-scope").textContent = study.scope;
  }

  function inspectRecord(studyId, gene) {
    const study = studies[studyId];
    if (!Object.hasOwn(study.records, gene)) return;
    auditStudy.value = studyId;
    refreshRecordOptions(gene);
    activeDialog = null;
    const lalli = studyId === "lalli";
    const jost = studyId === "jost";
    const recent = lalli ? output?.records.find(item => item.record_id.endsWith("_" + gene)) : null;
    const [fallbackN, fallbackControls, fallbackRows, fallbackGenes] = study.records[gene];
    showDialog(study.label + " · " + gene,
      lalli || jost ? (recent ? "本轮核验 · 描述性比较" : "归档记录 · 描述性比较") : "归档记录 · 仅用于展示");
    const details = node("dl");
    const [n, controls] = recent ? [recent.n_perturbed, recent.n_control] : [fallbackN, fallbackControls];
    const shape = recent?.shape || [fallbackRows, fallbackGenes];
    const answers = lalli ? [
      "论文 Fig. 2 说明研究设计。GEO 样本 GSM4219576 定位 Run 2，标明计数文件和作者标签。",
      `作者标签匹配到同一样本。扰动 ${n} 个，非靶向对照 ${controls} 个，两组不重叠。读取矩阵 ${dimensions(shape)}。`,
      "Input 保存目标、背景和对照表达基线。Target 保存逐基因表达均值差。三个条件共享对照，属于同一个数据分组。"
    ] : jost ? [
      "Jost 论文 Figure 6 说明 Perturb-seq 研究设计。GEO 样本 GSM3842207 定位 K562 样本和细胞身份文件。",
      `按归档细胞身份表选择同一 GEM group。扰动 ${n} 个，非靶向对照 ${controls} 个。读取矩阵 ${dimensions(shape)}。`,
      "归档数据已计算逐基因响应。Input 使用实验前字段和对照基线，Target 保存表达变化。三个条件共享对照。"
    ] : [
      "原论文与发布矩阵能定位 RPE1 展示子集。当前材料缺发布矩阵的表达尺度说明。",
      "同一个 GEM group 内 2 个 TARDBP 扰动细胞与 3 个非靶向对照。读取矩阵 5 × 8,749。",
      "这条记录可展示数据联接。补足表达尺度证据后再决定是否计算和导出。"
    ];
    ["证据在哪", "细胞怎么配", "下游怎么用"].forEach((label, index) => {
      details.append(node("dt", label), node("dd", answers[index]));
    });
    const paths = node("div", null, "live-actions");
    links(paths, lalli ? {
      "论文 Fig. 2":"https://pmc.ncbi.nlm.nih.gov/articles/PMC7545139/",
      "GEO 样本":"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM4219576",
      "核验回执":recent ? output.files["核验回执"] : archivedReceipt,
    } : jost ? {
      "论文 Figure 6":"https://pmc.ncbi.nlm.nih.gov/articles/PMC7065968/",
      "GEO 样本":"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM3842207",
      "核验回执":`${jostFolder}/jost2020_g1_${Object.keys(study.records).indexOf(gene)+1}-reader.json`,
    } : {
      "论文":"https://pubmed.ncbi.nlm.nih.gov/35688146/",
      "公开数据":"https://doi.org/10.25452/figshare.plus.20029387",
      "退回回执":`${jostFolder}/decision.json`,
    }, gene);
    body.append(details, paths);
  }

  function addCall(event, kind) {
    if (rows.has(event.id)) return;
    const follow = trace.scrollHeight - trace.scrollTop - trace.clientHeight < 55;
    trace.querySelector(".live-empty")?.remove();
    const item = node("li", null, "trace-item");
    item.dataset.kind = kind;
    const button = node("button", null, "trace-row");
    button.type = "button";
    button.dataset.kind = kind;
    button.dataset.status = "running";
    const dot = node("span", "○", "trace-state");
    dot.setAttribute("aria-hidden", "true");
    const meta = node("span", "执行中", "trace-meta");
    button.append(dot, node("span", kinds[kind], "trace-kind"), node("span", event.title, "trace-title"),
      meta, node("span", "›", "trace-arrow"));
    button.addEventListener("click", () => inspectCall(event.id));
    item.append(button);
    trace.append(item);
    rows.set(event.id, {event, kind, button, dot, meta});
    if (follow) trace.scrollTop = trace.scrollHeight;
  }

  function finishCall(event) {
    const entry = rows.get(event.call);
    if (!entry) return;
    entry.result = event;
    entry.button.dataset.status = event.status;
    entry.dot.textContent = event.status === "ok" ? "✓" : "!";
    const data = event.output;
    let signal = "";
    if (Number.isInteger(data.hits)) signal = `命中 ${data.hits} 条 · `;
    else if (Number.isInteger(data.records_received)) signal = `取回 ${data.records_received} 篇 · `;
    else if (data.source === "live_geo_metadata") signal = "GEO 已定位 · ";
    else if (data.source === "cached_source_manifest") signal = "回退缓存 · ";
    else if (Number.isInteger(data.candidates)) signal = `${data.candidates} 条候选 · `;
    else if (Number.isInteger(data.admitted)) signal = `${data.admitted} 条通过 · `;
    else if (typeof data.eligible === "boolean") signal = data.eligible ? "初审通过 · " : "证据不足 · ";
    entry.meta.textContent = event.status === "ok" ? signal + event.elapsed.toFixed(2) + " s" : "失败";
    if (dialog.open && activeDialog === event.call) inspectCall(event.call);
  }

  function renderOutput(event) {
    output = event;
    byId("live-result").hidden = false;
    byId("live-result-text").textContent =
      `这篇 Lalli 论文的 ${event.records.length} 个比较条件通过核验。Input / Target 均为 ${dimensions(event.shape)}，一行对应一个比较条件，一列对应一个基因。`;
    const results = byId("live-result-records");
    for (const record of event.records) {
      const gene = record.record_id.split("_").at(-1);
      const button = node("button", gene + " · 查看记录 ↗", "live-button");
      button.type = "button";
      button.addEventListener("click", () => inspectRecord("lalli", gene));
      results.append(button);
    }
    const groups = byId("live-result-files");
    const files = {...event.files, "事件日志": event.notebook};
    const sections = [
      ["要交的数据", [
        ["结构化记录", "每个条件的论文证据、样本、两组细胞和表达矩阵。"],
        ["模型数组", "供程序读取的 Input / Target。"]
      ]],
      ["验收依据", [
        ["核验回执", "逐条确认细胞数、样本和矩阵尺寸。"],
        ["导出校验", "核对同轮文件指纹、基因顺序和逐基因复算。"]
      ]],
      ["过程留档", [
        ["候选交接", "Skill 1 把待核验的证据和细胞分组交给 Skill 2。"],
        ["事件日志", "保存本轮调用、回执和完成状态。"]
      ]],
    ];
    for (const [title, items] of sections) {
      const group = node("div", null, "live-artifact-group");
      group.append(node("h4", title));
      for (const [label, description] of items) {
        if (!files[label]) continue;
        const item = node("div", null, "live-artifact");
        links(item, {[label]:files[label]});
        item.append(node("p", description));
        group.append(item);
      }
      groups.append(group);
    }
  }

  function receive(event) {
    if (event.type === "run/start") {
      byId("live-run-provider").textContent = event.provider === "deepseek" ? "DeepSeek / " + event.model : "课堂固定流程";
      setStatus("运行中", "running");
    } else if (event.type === "run/result" || event.type === "run/error") {
      completed = true;
      source.close();
      start.disabled = !available;
      start.textContent = "↻ 重新运行";
      setStatus(event.type === "run/result" ? "已完成" : "运行失败", event.type === "run/result" ? "done" : "failed");
      if (event.type === "run/result") renderOutput(event);
      else setNotice(event.error);
    } else {
      const [kind, operation] = event.type.split("/");
      if (operation === "call") addCall(event, kind);
      if (operation === "result") finishCall(event);
    }
    const finished = [...rows.values()].filter(item => item.result).length;
    byId("live-tally").textContent = rows.size ? `${finished} / ${rows.size} 步已返回` : "";
  }

  function connect(runId) {
    if (runId === currentRun && !completed) return;
    source?.close();
    currentRun = runId;
    completed = false;
    output = null;
    rows.clear();
    trace.replaceChildren(node("li", "正在接收本轮轨迹…", "live-empty"));
    byId("live-run-id").textContent = runId;
    byId("live-run-provider").textContent = "";
    byId("live-tally").textContent = "";
    byId("live-result").hidden = true;
    byId("live-result-records").replaceChildren();
    byId("live-result-files").replaceChildren();
    setNotice("");
    start.disabled = true;
    setStatus("接收轨迹", "running");
    source = new EventSource("/api/runs/" + encodeURIComponent(runId) + "/events");
    source.addEventListener("open", () => {
      if (!completed) { setNotice(""); setStatus("运行中", "running"); }
    });
    source.addEventListener("message", message => receive(JSON.parse(message.data)));
    source.addEventListener("error", () => {
      if (!completed) setNotice("轨迹连接暂时中断，浏览器会重连并接收后续回执。");
    });
  }

  start.addEventListener("click", async () => {
    const useDeepSeek = provider.value === "deepseek";
    const key = useDeepSeek ? apiKey.value.trim() : "";
    if (useDeepSeek && !key) {
      setNotice("请先输入你自己的 DeepSeek API Key。");
      apiKey.focus();
      return;
    }
    start.disabled = true;
    setNotice("");
    setStatus("启动中", "running");
    try {
      const settings = useDeepSeek ? {provider: "deepseek", model: model.value, api_key: key} : {provider: "local"};
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(settings),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "启动失败");
      if (useDeepSeek) apiKey.value = "";
      connect(result.run_id);
    } catch (error) {
      setStatus("启动失败", "failed");
      setNotice(error.message);
      start.disabled = false;
    }
  });

  byId("live-search-button").addEventListener("click", async () => {
    const query = byId("live-search-query").value.trim();
    const target = byId("live-search-results");
    const message = byId("live-search-status");
    if (!query) { message.textContent = "请输入论文主题或作者。"; return; }
    target.replaceChildren();
    message.textContent = "正在联网检索论文和公开来源……";
    try {
      const response = await fetch("/api/research-search", {
        method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({query}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "检索失败");
      message.textContent = `找到 ${data.results.length} 项候选，来源：${data.provider}。检索记录已保存到本地，候选仍需审核。`;
      for (const item of data.results) {
        const button = node("button", item.title || "未提供标题", "live-button");
        button.type = "button";
        button.onclick = () => {
          byId("live-dialog-kind").textContent = "联网检索结果";
          byId("live-dialog-title").textContent = item.title || "未提供标题";
          body.replaceChildren(node("p", "DOI：" + (item.doi || "未提供")));
          for (const location of item.locations || []) {
            const text = [location.source_name, location.landing_page_url, location.pdf_url].filter(Boolean).join(" · ");
            if (text) body.append(node("p", text));
          }
          if (!dialog.open) dialog.showModal();
        };
        target.append(button);
      }
    } catch (error) { message.textContent = "检索失败：" + error.message; }
  });

  provider.addEventListener("change", () => {
    byId("live-provider-fields").hidden = provider.value !== "deepseek";
    setNotice("");
  });

  auditStudy.addEventListener("change", () => refreshRecordOptions());
  byId("audit-open").addEventListener("click", () => inspectRecord(auditStudy.value, auditRecord.value));
  refreshRecordOptions();
  byId("live-draw").addEventListener("click", () => {
    const choices = Object.entries(studies).flatMap(([id, study]) => Object.keys(study.records).map(gene => [id, gene]));
    const otherRecords = choices.filter(([id, gene]) => id !== auditStudy.value || gene !== auditRecord.value);
    const [study, gene] = otherRecords[Math.floor(Math.random()*otherRecords.length)];
    inspectRecord(study, gene);
  });
  for (const modal of [dialog, receiptDialog]) {
    modal.addEventListener("click", event => {
      if (event.target !== modal) return;
      const bounds = modal.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) modal.close();
    });
  }

  async function startup() {
    try {
      if (location.protocol === "file:") throw new Error("静态文件模式");
      const response = await fetch("/api/status");
      if (!response.ok) throw new Error("运行台未连接");
      const state = await response.json();
      if (state.service !== "ece449-classroom") throw new Error("运行台未连接");
      if (!Array.isArray(state.deepseek_models)) throw new Error("运行台需要重新启动");
      for (const option of model.options) option.disabled = !state.deepseek_models.includes(option.value);
      available = true;
      start.disabled = false;
      setStatus("就绪", "idle");
      if (state.run_id) connect(state.run_id);
    } catch (error) {
      start.hidden = true;
      open.hidden = false;
      byId("live-settings").hidden = true;
      setStatus("归档视图", "idle");
      setNotice(error.message === "运行台需要重新启动"
        ? "请用项目里的「启动课堂运行台」重新启动服务，再刷新本页。"
        : "在本机运行台中启动实时流程。此页可以查看已有记录的详情。");
    }
  }
  startup();
})();
