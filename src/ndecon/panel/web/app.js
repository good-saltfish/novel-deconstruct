/* ndecon 面板前端：无框架、无外部 CDN，纯 fetch + DOM */
"use strict";

const state = { projects: [], currentId: null, references: [], providers: [], llm: null };

const $ = (sel) => document.querySelector(sel);
const detail = $("#detail");

async function api(method, path, payload) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (payload !== undefined) opt.body = JSON.stringify(payload);
  const resp = await fetch(path, opt);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function toast(msg, isErr) {
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

async function refresh() {
  state.projects = await api("GET", "/api/projects");
  state.references = state.projects.filter((p) => p.kind === "reference");
  renderLists();
  if (state.currentId) {
    const full = await api("GET", `/api/projects/${state.currentId}`);
    renderDetail(full);
  }
}

function renderLists() {
  const renderItem = (p) => {
    const li = document.createElement("li");
    if (p.id === state.currentId) li.className = "active";
    li.textContent = p.title;
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = `${p.kind === "reference" ? "参考书" : "长篇"} · ${p.created_at.replace("T", " ")}`;
    li.appendChild(sub);
    li.onclick = () => { state.currentId = p.id; refresh(); };
    return li;
  };
  const refUl = $("#ref-list");
  const creUl = $("#creation-list");
  refUl.innerHTML = "";
  creUl.innerHTML = "";
  state.projects
    .filter((p) => p.kind === "reference")
    .forEach((p) => refUl.appendChild(renderItem(p)));
  state.projects
    .filter((p) => p.kind === "creation")
    .forEach((p) => creUl.appendChild(renderItem(p)));
}

/* ---------- 模型供应商设置（#22） ---------- */

async function refreshLlmStatus() {
  try {
    const [providers, settings] = await Promise.all([
      api("GET", "/api/llm/providers"),
      api("GET", "/api/llm/settings"),
    ]);
    state.providers = providers;
    state.llm = settings;
    const el = $("#llm-status");
    if (settings.configured) {
      el.textContent = `模型：${settings.provider_name} · ${settings.model}`;
      el.className = "llm-status ok";
    } else {
      el.textContent = "模型：未配置（离线 Fake）";
      el.className = "llm-status";
    }
  } catch (err) {
    // 老版本后端时静默
  }
}

$("#btn-llm").onclick = () => {
  const options = state.providers
    .map((p) => `<option value="${p.id}">${p.name}</option>`)
    .join("");
  openModal(
    "模型设置（OpenAI 兼容；key 只保存在本次面板进程内存，不落盘）",
    `<label class="field">供应商
       <select id="llm-provider">${options}</select></label>
     <label class="field">API Key <input id="llm-key" type="password" autocomplete="off"
       placeholder="sk-...（本地 Ollama 可留空；已保存后留空表示不修改）" /></label>
     <label class="field">模型名 <input id="llm-model" type="text" list="llm-model-list" placeholder="模型 ID" />
       <datalist id="llm-model-list"></datalist></label>
     <label class="field">Base URL <input id="llm-base" type="text" placeholder="https://..." /></label>
     <div id="llm-hint" style="font-size:12px;color:var(--muted)"></div>
     <button type="button" class="btn small" id="llm-test-btn">测试连接（不保存）</button>
     <div id="llm-test-result" class="llm-test-result"></div>
     <div style="font-size:12px;color:var(--muted);margin-top:8px">
       面板仅监听 127.0.0.1；重启面板后配置清空需重新填写。</div>`,
    async () => {
      const provider = $("#llm-provider").value;
      const payload = {
        provider,
        api_key: $("#llm-key").value.trim(),
        model: $("#llm-model").value.trim(),
        base_url: $("#llm-base").value.trim(),
      };
      // 已保存配置且本次未改 key：后端从会话保留旧 key（需要把当前 provider 先同步过去）
      await api("PUT", "/api/llm/settings", payload);
      await refreshLlmStatus();
      toast("模型配置已保存（仅本次进程）");
    }
  );

  // 弹窗打开后填充交互
  const selectEl = $("#llm-provider");
  const keyEl = $("#llm-key");
  const modelEl = $("#llm-model");
  const baseEl = $("#llm-base");
  const hintEl = $("#llm-hint");
  const listEl = $("#llm-model-list");

  const applyPreset = (preserveKey) => {
    const p = state.providers.find((x) => x.id === selectEl.value);
    if (!p) return;
    baseEl.value = p.base_url || "";
    modelEl.value = p.default_model || modelEl.value || "";
    listEl.innerHTML = p.models.map((m) => `<option value="${m}">`).join("");
    hintEl.textContent = p.key_hint || "";
    baseEl.readOnly = p.id !== "custom";
    if (!preserveKey) keyEl.value = "";
    keyEl.placeholder = p.key_optional
      ? "本地服务可留空"
      : state.llm?.has_key && state.llm?.provider === p.id
        ? "已保存（留空保持不变）"
        : "sk-...";
  };
  selectEl.onchange = () => applyPreset(false);

  // 回显当前配置
  if (state.llm?.provider) {
    selectEl.value = state.llm.provider;
    applyPreset(true);
    modelEl.value = state.llm.model;
    baseEl.value = state.llm.base_url;
  } else {
    applyPreset(true);
  }

  $("#llm-test-btn").onclick = async () => {
    const resultEl = $("#llm-test-result");
    resultEl.className = "llm-test-result";
    resultEl.textContent = "正在测试…";
    try {
      // 测试用当前表单值；key 留空且供应商未变则让后端回退到已保存 key（先 PUT 再空 body 测）
      const payload = {
        provider: selectEl.value,
        api_key: keyEl.value.trim(),
        model: modelEl.value.trim(),
        base_url: baseEl.value.trim(),
      };
      if (!payload.api_key && state.llm?.has_key && state.llm.provider === selectEl.value
          && payload.model === state.llm.model && payload.base_url === state.llm.base_url) {
        const r = await api("POST", "/api/llm/test", {});
        resultEl.className = "llm-test-result ok";
        resultEl.textContent = r.detail;
      } else {
        const r = await api("POST", "/api/llm/test", payload);
        resultEl.className = "llm-test-result ok";
        resultEl.textContent = r.detail;
      }
    } catch (err) {
      resultEl.className = "llm-test-result err";
      resultEl.textContent = "连接失败：" + err.message;
    }
  };
};

/* ---------- 弹窗表单 ---------- */

function openModal(title, fieldsHtml, onOk) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = fieldsHtml;
  const dialog = $("#modal");
  dialog.showModal();
  $("#modal-form").onsubmit = async (e) => {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    try {
      await onOk();
      dialog.close();
      await refresh();
    } catch (err) {
      toast(err.message, true);
    }
  };
}

$("#btn-import").onclick = () => {
  openModal(
    "导入小说拆书（离线 Fake 分析）",
    `<label class="field">书名（可空，默认取文件名）<input id="f-name" type="text" placeholder="例如：我不是戏神" /></label>
     <label class="field">本机小说文件绝对路径（UTF-8 .txt/.md）<input id="f-path" type="text" placeholder="D:\\\\books\\\\novel.txt" /></label>
     <div style="color:var(--muted);font-size:12px">只保存分析结果，不会把原文复制进工作区。</div>`,
    async () => {
      const name = $("#f-name").value.trim();
      const file_path = $("#f-path").value.trim();
      if (!file_path) throw new Error("文件路径必填");
      const p = await api("POST", "/api/projects/import", { name, file_path });
      state.currentId = p.meta.id;
      toast("拆书完成并入库");
    }
  );
};

$("#btn-new").onclick = () => {
  const checks = state.references
    .map((r) => `<label><input type="checkbox" value="${r.id}" />${r.title}</label>`)
    .join("");
  openModal(
    "新建长篇",
    `<label class="field">书名<input id="f-title" type="text" /></label>
     <label class="field">题材<input id="f-genre" type="text" placeholder="末世/系统流/都市高武…" /></label>
     <label class="field">一句话设定<textarea id="f-premise" placeholder="主角+处境+核心矛盾的一句话"></textarea></label>
     <div style="font-size:13px;color:var(--muted)">参考拆书（只注入聚合数字统计）：</div>
     <div class="ref-pick">${checks || "<span style='color:var(--muted);font-size:12px'>暂无参考书</span>"}</div>`,
    async () => {
      const title = $("#f-title").value.trim();
      if (!title) throw new Error("书名必填");
      const reference_ids = [...document.querySelectorAll(".ref-pick input:checked")].map((i) => i.value);
      const p = await api("POST", "/api/projects/create", {
        title,
        genre: $("#f-genre").value.trim(),
        premise: $("#f-premise").value.trim(),
        reference_ids,
      });
      state.currentId = p.meta.id;
      toast("项目已创建");
    }
  );
};

/* ---------- 详情渲染 ---------- */

function field(labelText, key, value, multiline) {
  const tag = multiline ? "textarea" : "input";
  return `<label class="field">${labelText}<${tag} data-k="${key}" type="text">${value ?? ""}</${tag}></label>`;
}

function collectCard(container, keys) {
  const out = {};
  container.querySelectorAll("[data-k]").forEach((el) => {
    out[el.dataset.k] = el.value.trim();
  });
  return out;
}

// 部件状态三态（B2）：draft / auto-accepted / human-confirmed。
// 注意 "draft" 是字符串不是空值，不能直接当布尔用，否则草稿会显示成"已确认"。
function partStateLabel(state, content) {
  if (state === "human-confirmed") return '<span class="badge ok">已人工确认</span>';
  if (state === "auto-accepted") return '<span class="badge ok">已自动验收</span>';
  return content
    ? '<span class="badge draft">模型候选</span>'
    : '<span class="badge">未生成</span>';
}

function partCard(part, titleText, content, confirmed, bodyHtml, onSave) {
  const badge = partStateLabel(confirmed, content);
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<h4>${titleText} ${badge}</h4><div class="card-body">${bodyHtml}</div>
    <button class="btn small primary save-part" data-part="${part}">保存本部件</button>`;
  card.querySelector(".save-part").onclick = async () => {
    try {
      await onSave();
      toast(`${titleText} 已保存为确认内容`);
    } catch (err) {
      toast(err.message, true);
    }
  };
  return card;
}

async function savePart(projectId, part, content) {
  return api("PUT", `/api/projects/${projectId}/parts/${part}`, { content });
}

function renderReference(p) {
  detail.innerHTML = "";
  const h = document.createElement("div");
  const agg = p.aggregation;
  const themes = Object.entries(agg?.theme_distribution || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join("");
  const chars = (agg?.characters || [])
    .slice(0, 12)
    .map(
      (c) =>
        `<tr><td>${c.name}</td><td>${c.mention_count}</td><td>${c.chapter_orders.join("、")}</td></tr>`
    )
    .join("");
  h.innerHTML = `
    <h3 class="proj-title">${p.meta.title}</h3>
    <div class="proj-meta">参考书 · ${p.chapter_count} 章 · ${p.total_plot_points} 个情节点
      · 源路径 <code>${p.source_path}</code></div>
    <div class="card"><h4>爽点节奏</h4>
      含爽点章节：${agg?.pacing.satisfying_chapters.join("、") || "—"}<br/>
      相邻章距：${agg?.pacing.gaps.join("、") || "—"} ·
      平均：${agg?.pacing.average_gap ?? "—"} · 最大空窗：${agg?.pacing.longest_gap ?? "—"}
    </div>
    <div class="card"><h4>主题分布</h4><table class="stats"><tr><th>主题</th><th>次数</th></tr>${themes}</table></div>
    <div class="card"><h4>角色出场矩阵（前 12）</h4>
      <table class="stats"><tr><th>角色</th><th>提及</th><th>章号</th></tr>${chars}</table></div>
    <button class="btn danger small" id="del">删除该项目</button>`;
  h.querySelector("#del").onclick = async () => {
    if (!confirm("确定删除该参考书项目？只删分析记录，不影响源文件。")) return;
    await api("DELETE", `/api/projects/${p.meta.id}`);
    state.currentId = null;
    refresh();
  };
  detail.appendChild(h);
}

function renderCreation(p) {
  detail.innerHTML = "";
  const wrap = document.createElement("div");
  const refNames = p.reference_ids
    .map((id) => state.references.find((r) => r.id === id)?.title)
    .filter(Boolean)
    .join("、");
  wrap.innerHTML = `
    <h3 class="proj-title">${p.meta.title}</h3>
    <div class="proj-meta">长篇创作 · 参考：${refNames || "无"}</div>
    <div class="card">
      <h4>基础设定</h4>
      <div class="grid-2">
        ${field("题材", "genre", p.genre)}
      </div>
      ${field("一句话设定", "premise", p.premise, true)}
      <button class="btn primary" id="gen">一键生成/重生成骨架（离线 Fake）</button>
      <button class="btn" id="gen-ai">AI 生成骨架（已配置模型）</button>
      <span style="color:var(--muted);font-size:12px;margin-left:10px">重生成会把各部件重置为候选态；AI 使用顶栏「模型设置」中的供应商</span>
    </div>
    <div id="parts"></div>`;

  wrap.querySelector('[data-k="genre"]').onchange = (e) => saveBase(p.meta.id, { genre: e.target.value });
  wrap.querySelector('[data-k="premise"]').onchange = (e) => saveBase(p.meta.id, { premise: e.target.value });
  wrap.querySelector("#gen").onclick = async () => {
    toast("正在生成骨架（Fake）…");
    const updated = await api("POST", `/api/projects/${p.meta.id}/generate`, { provider: "fake" });
    renderDetail(updated);
    toast("骨架已生成（Fake）");
  };
  wrap.querySelector("#gen-ai").onclick = async () => {
    if (!state.llm?.configured) {
      toast("请先在顶栏「模型设置」中配置供应商与 key", true);
      return;
    }
    if (!confirm("AI 生成将调用已配置的模型（可能产生 API 费用），并重置各部件为候选态，继续？")) return;
    toast("正在调用 AI 生成骨架…");
    try {
      const updated = await api("POST", `/api/projects/${p.meta.id}/generate`, { provider: "ai" });
      renderDetail(updated);
      toast("AI 骨架已生成");
    } catch (err) {
      toast(err.message, true);
    }
  };

  const parts = wrap.querySelector("#parts");
  const d = p.draft;
  const conf = p.confirmed_parts || {};

  // 题材定位
  if (d.positioning) {
    const v = d.positioning;
    const body = `
      <div class="grid-2">
        ${field("主类型", "genre", v.genre)}${field("目标读者", "audience", v.audience)}
      </div>
      ${field("一句话卖点", "core_premise", v.core_premise, true)}
      ${field("差异化卖点（每行一条）", "selling_points", (v.selling_points || []).join("\n"), true)}
      ${field("目标基调", "tone_target", v.tone_target)}`;
    const card = partCard("positioning", "题材定位", d.positioning, conf.positioning, body, async () => {
      const c = collectCard(card, ["genre", "audience", "core_premise", "tone_target"]);
      c.selling_points = card.querySelector('[data-k="selling_points"]').value
        .split("\n").map((s) => s.trim()).filter(Boolean);
      await savePart(p.meta.id, "positioning", c);
    });
    parts.appendChild(card);
  }

  // 卷纲
  if (d.volume) {
    const v = d.volume;
    const body = `
      <div class="grid-2">
        ${field("首卷标题", "volume_title", v.volume_title)}${field("本卷目标", "volume_goal", v.volume_goal, true)}
      </div>
      ${field("核心矛盾", "main_conflict", v.main_conflict, true)}
      ${field("卷尾钩子", "ending_hook", v.ending_hook, true)}`;
    const volumeCardEl = partCard("volume", "首卷纲要", d.volume, conf.volume, body, async () => {
      await savePart(p.meta.id, "volume", collectCard(volumeCardEl, []));
    });
    parts.appendChild(volumeCardEl);
  }

  // 主角
  if (d.protagonist) {
    const v = d.protagonist;
    const body = `
      <div class="grid-2">
        ${field("姓名", "name", v.name)}${field("核心欲望", "desire", v.desire)}
      </div>
      ${field("出身/初始处境", "background", v.background, true)}
      <div class="grid-2">
        ${field("性格", "personality", v.personality)}${field("标志能力/手段", "signature_ability", v.signature_ability)}
      </div>
      ${field("缺陷/成长课题", "flaw", v.flaw, true)}`;
    const card = partCard("protagonist", "主角人设", d.protagonist, conf.protagonist, body, async () => {
      await savePart(p.meta.id, "protagonist", collectCard(card, ["name", "desire", "background", "personality", "signature_ability", "flaw"]));
    });
    parts.appendChild(card);
  }

  // 金手指
  if (d.golden_finger) {
    const v = d.golden_finger;
    const body = `
      <div class="grid-2">
        ${field("名称", "name", v.name)}${field("形态", "form", v.form)}
      </div>
      ${field("能力", "ability", v.ability, true)}
      ${field("限制与代价（必填）", "cost_limitation", v.cost_limitation, true)}
      ${field("成长路径", "growth_path", v.growth_path, true)}`;
    const card = partCard("golden_finger", "金手指", d.golden_finger, conf.golden_finger, body, async () => {
      await savePart(p.meta.id, "golden_finger",
        collectCard(card, ["name", "form", "ability", "cost_limitation", "growth_path"]));
    });
    parts.appendChild(card);
  }

  // 前十章细纲
  if (d.chapters && d.chapters.length) {
    const cards = d.chapters
      .map((c) => {
        const inner = document.createElement("div");
        inner.className = "chapter-card";
        inner.innerHTML = `<h5>第${c.order}章 · <input data-k="title" type="text" value="${c.title}" style="width:60%"/></h5>
          ${field("核心事件", "core_event", c.core_event, true)}
          ${field("开篇钩子", "opening_hook", c.opening_hook)}
          ${field("章尾钩子", "ending_hook", c.ending_hook)}`;
        attachManuscript(inner, p.meta.id, c.order, (p.manuscripts || {})[`ch${c.order}`]);
        return inner;
      });
    const grid = document.createElement("div");
    grid.className = "chapters";
    cards.forEach((c) => grid.appendChild(c));
    const card = partCard("chapters", "前 10 章细纲", true, conf.chapters, "", async () => {
      const items = [...grid.querySelectorAll(".chapter-card")].map((el, i) => ({
        order: i + 1,
        ...collectCard(el, ["title", "core_event", "opening_hook", "ending_hook"]),
      }));
      await savePart(p.meta.id, "chapters", items);
    });
    card.querySelector(".card-body").appendChild(grid);
    parts.appendChild(card);
  }

  // Planner Agent 自动规划卡片（#25）：运行产出候选，手动确认才入库
  parts.appendChild(buildAgentCard(p));

  // 设定候选 / 细纲候选：两套提示词学习方法不同（creation/plans.py）
  parts.appendChild(buildPlanCard(p));

  // 修正卷纲卡片的收集作用域（闭包内 parts 过宽）
  const volumeCard = [...parts.querySelectorAll(".card")].find((c) =>
    c.querySelector(".save-part")?.dataset.part === "volume");
  if (volumeCard) {
    volumeCard.querySelector(".save-part").onclick = async () => {
      await savePart(p.meta.id, "volume", collectCard(volumeCard, []));
      toast("首卷纲要 已保存为确认内容");
    };
  }

  detail.appendChild(wrap);
}

async function saveBase(projectId, patch) {
  return api("PATCH", `/api/projects/${projectId}`, patch);
}

function reviewHtml(review) {
  if (!review) return '<div class="ms-review muted">暂无结构化自评</div>';
  const deviations = (review.deviations || []).length
    ? `<ul>${review.deviations.map((x) => `<li>${x}</li>`).join("")}</ul>`
    : "<div>未发现与细纲偏差</div>";
  return `<div class="ms-review">
    <div><b>结构化自评</b>（${review.model_id} · ${review.prompt_version}）</div>
    <div>钩子强度：${"★".repeat(review.hook_strength)}${"☆".repeat(5 - review.hook_strength)}
      ｜信息密度：${"★".repeat(review.info_density)}${"☆".repeat(5 - review.info_density)}
      ｜贴合细纲：${review.outline_followed ? "是" : "否"}</div>
    <div><b>偏差点</b>${deviations}</div>
    ${review.notes ? `<div class="muted">评语：${review.notes}</div>` : ""}
  </div>`;
}

function attachManuscript(card, projectId, order, record) {
  const bar = document.createElement("div");
  bar.className = "ms-bar";
  const badge = document.createElement("span");
  const setBadge = (rec) => {
    badge.className = "badge " + (rec ? (rec.status === "user-confirmed" ? "ok" : "draft") : "");
    badge.textContent = rec
      ? (rec.status === "user-confirmed" ? `正文已确认 · ${rec.word_count}字` : `正文候选 · ${rec.word_count}字`)
      : "正文未生成";
  };
  setBadge(record);
  bar.appendChild(badge);

  const mkBtn = (text, cls, onClick) => {
    const b = document.createElement("button");
    b.className = "btn " + cls;
    b.textContent = text;
    b.onclick = onClick;
    bar.appendChild(b);
    return b;
  };

  const panel = document.createElement("div");
  panel.className = "ms-panel";
  panel.style.display = "none";
  panel.innerHTML = `${reviewHtml(null)}
    <textarea class="ms-content" rows="16" placeholder="正文将显示在这里，可直接编辑后保存"></textarea>
    <div class="ms-actions">
      <button class="btn small primary ms-save">保存修改（候选态）</button>
      <button class="btn small ms-confirm">确认本章</button>
    </div>`;

  const fillPanel = (data) => {
    panel.querySelector(".ms-content").value = data.content || "";
    panel.querySelector(".ms-review").outerHTML = reviewHtml(data.record?.review);
    setBadge(data.record);
  };

  const openPanel = async () => {
    const visible = panel.style.display !== "none";
    panel.style.display = visible ? "none" : "block";
    if (!visible && !panel.querySelector(".ms-content").value) {
      try {
        const data = await api("GET", `/api/projects/${projectId}/chapters/${order}`);
        fillPanel(data);
      } catch (err) {
        panel.style.display = "none";
        toast(err.message, true);
      }
    }
  };
  mkBtn("查看/编辑", "small", openPanel);

  const generate = async (provider) => {
    toast(provider === "fake" ? "正在生成正文（离线 Fake）…" : "正在调用 openai-compat…");
    try {
      const data = await api("POST", `/api/projects/${projectId}/chapters/${order}/generate`, { provider });
      panel.style.display = "block";
      fillPanel(data);
      toast("正文已生成为候选");
    } catch (err) {
      toast(err.message, true);
    }
  };
  mkBtn("生成正文·Fake", "small primary", () => generate("fake"));
  mkBtn("生成正文·AI", "small", () => generate("openai-compat"));

  panel.querySelector(".ms-save").onclick = async () => {
    try {
      const data = await api("PUT", `/api/projects/${projectId}/chapters/${order}`, {
        content: panel.querySelector(".ms-content").value,
      });
      fillPanel(data);
      toast("修改已保存（候选态，需确认）");
    } catch (err) {
      toast(err.message, true);
    }
  };
  panel.querySelector(".ms-confirm").onclick = async () => {
    try {
      const data = await api("POST", `/api/projects/${projectId}/chapters/${order}/confirm`);
      fillPanel(data);
      toast("本章正文已确认");
    } catch (err) {
      toast(err.message, true);
    }
  };

  mkBtn("指纹体检", "small", async () => {
    if (panel.style.display === "none") await openPanel();
    try {
      const rep = await api("GET", `/api/projects/${projectId}/chapters/${order}/lint`);
      renderLint(panel, rep);
    } catch (err) {
      toast(err.message, true);
    }
  });

  card.appendChild(bar);
  card.appendChild(panel);
}

/* ---------- AI 指纹体检：只读报告，不给改写文本 ---------- */

function renderLint(panel, rep) {
  let box = panel.querySelector(".ms-lint");
  if (!box) {
    box = document.createElement("div");
    box.className = "ms-lint";
    panel.insertBefore(box, panel.querySelector(".ms-content"));
  }
  const rows = rep.findings.map((f) => {
    const ok = f.severity === "info" ? "info" : (f.passed ? "ok" : "bad");
    return `<tr class="lint-${ok}">
      <td>${esc(f.label)}</td>
      <td>${f.value}</td>
      <td>${f.limit}</td>
      <td>${esc(f.detail || "")}</td>
    </tr>`;
  }).join("");
  box.innerHTML = `<div class="lint-head">
      <b>AI 指纹体检</b>（${esc(rep.version)}）
      ${rep.passed ? '<span class="badge ok">全部在阈值内</span>'
        : '<span class="badge draft">有项超出阈值</span>'}
      <span class="muted">字数 ${rep.cjk_chars} · 段落 ${rep.paragraph_count} · 场景轮换 ${rep.scene_rotations}</span>
    </div>
    <table class="lint-table">
      <thead><tr><th>规则</th><th>实测</th><th>阈值</th><th>说明</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="muted">只报告密度信号，不判定"是不是 AI"，也不提供改写文本——改哪里由你决定。</div>`;
}

function renderDetail(p) {
  if (p.meta.kind === "reference") renderReference(p);
  else renderCreation(p);
}

/* ---------- Planner Agent（#25）：运行候选 + 手动确认入库 ---------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function buildAgentCard(p) {
  const card = document.createElement("div");
  card.className = "card";
  const orderOpts = Array.from({ length: 10 }, (_, i) =>
    `<option value="${i + 1}">第${i + 1}章</option>`).join("");
  card.innerHTML = `
    <h4>Planner Agent 自动规划（#25）</h4>
    <div class="card-body">
      <div class="muted" style="margin-bottom:10px">
        候选不代笔：运行后产出节拍/设定「候选」，需手动确认才入库（确认前不改权威数据）。AI 使用顶栏「模型设置」中的供应商。</div>
      <label class="field">任务
        <select id="agent-task">
          <option value="expand_chapter">扩写某章节拍</option>
          <option value="add_settings">补充结构化设定</option>
        </select></label>
      <label class="field" id="agent-ch-wrap">章号（1-10）
        <select id="agent-order">${orderOpts}</select></label>
      <label class="field">模型
        <select id="agent-provider">
          <option value="fake">离线 Fake（免 key）</option>
          <option value="ai">已配置模型（顶栏「模型设置」）</option>
        </select></label>
      <button class="btn primary small" id="agent-run">运行 Agent</button>
      <div id="agent-out" style="margin-top:12px"></div>
    </div>`;

  const taskSel = card.querySelector("#agent-task");
  const orderWrap = card.querySelector("#agent-ch-wrap");
  const out = card.querySelector("#agent-out");

  const syncTask = () => {
    orderWrap.style.display = taskSel.value === "expand_chapter" ? "" : "none";
  };
  taskSel.onchange = syncTask;
  syncTask();

  card.querySelector("#agent-run").onclick = async () => {
    const task = taskSel.value;
    const provider = card.querySelector("#agent-provider").value;
    const payload = { task, provider };
    if (task === "expand_chapter") {
      payload.chapter_order = Number(card.querySelector("#agent-order").value);
    }
    out.innerHTML = '<div class="muted">运行中…</div>';
    try {
      const r = await api("POST", `/api/projects/${p.meta.id}/agent/runs`, payload);
      renderAgentResult(card, p, r, task);
      toast(r.finished ? "Agent 运行完成" : `Agent 停止：${r.stop_reason}`);
    } catch (err) {
      out.innerHTML = "";
      toast(err.message, true);
    }
  };
  return card;
}

function renderAgentResult(card, p, r, task) {
  const out = card.querySelector("#agent-out");
  const beats = r.drafts?.beats || [];
  const settings = r.drafts?.settings || [];
  let html = `<div class="muted">stop=${esc(r.stop_reason)} · turns=${r.turns_used}` +
    (r.total_latency_ms != null ? ` · ${r.total_latency_ms.toFixed(1)}ms` : "") + `</div>`;
  if (task === "expand_chapter") {
    html += `<h5 style="margin:10px 0 6px">节拍候选（${beats.length}）</h5>`;
    html += beats.map((b) =>
      `<div class="agent-beat">第${b.order}拍 · ${esc(b.scene)}｜${esc((b.characters || []).join("、"))}｜${esc(b.event)}</div>`
    ).join("");
    html += `<button class="btn small primary" id="agent-accept-beats">确认这 ${beats.length} 个节拍</button>`;
  } else {
    html += `<h5 style="margin:10px 0 6px">设定候选（${settings.length}）</h5>`;
    html += settings.map((s) =>
      `<div class="agent-setting">[${esc(s.entry_type)}] ${esc(s.name)}：${esc(s.content)}</div>`
    ).join("");
    html += `<button class="btn small primary" id="agent-accept-settings">确认这 ${settings.length} 条设定</button>`;
  }
  html += `<details style="margin-top:10px"><summary class="muted" style="cursor:pointer">查看运行轨迹（${r.trace.length} 步）</summary>` +
    `<div class="agent-trace">` +
    r.trace.map((t) => `<div>[${t.turn}] ${esc(t.tool)}：${esc(t.thought)} → ${esc(t.observation)}</div>`).join("") +
    `</div></details>`;
  out.innerHTML = html;

  const acceptBeats = out.querySelector("#agent-accept-beats");
  if (acceptBeats) acceptBeats.onclick = async () => {
    try {
      await api("PUT", `/api/projects/${p.meta.id}/agent/accept/beats`,
        { chapter_order: Number(card.querySelector("#agent-order").value), beats });
      toast("节拍已确认为该章权威内容");
      refresh();
    } catch (err) { toast(err.message, true); }
  };
  const acceptSettings = out.querySelector("#agent-accept-settings");
  if (acceptSettings) acceptSettings.onclick = async () => {
    try {
      await api("PUT", `/api/projects/${p.meta.id}/agent/accept/settings`, { settings });
      toast("设定已确认为项目设定");
      refresh();
    } catch (err) { toast(err.message, true); }
  };
}

/* ---------- 设定候选 / 细纲候选（两套学习方法不同，见 creation/plans.py）---------- */

function buildPlanCard(p) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <h4>设定候选 / 细纲候选</h4>
    <div class="card-body">
      <div class="muted" style="margin-bottom:10px">
        两套提示词的学习方法不同：<b>设定</b>走收敛（边界→代价→人），<b>细纲</b>走铺开（时间轴排张力），
        各自用不同的检索词去知识库里取经。产出是候选，确认后才写进项目。</div>
      <div class="plan-tabs">
        <button class="btn small primary" id="pt-settings">设定候选</button>
        <button class="btn small" id="pt-outline">细纲候选</button>
      </div>
      <div class="muted" id="pt-hint" style="margin:8px 0"></div>
      <label class="field">模型
        <select id="pt-provider">
          <option value="fake">离线 Fake（免 key）</option>
          <option value="ai">已配置模型（顶栏「模型设置」）</option>
        </select></label>
      <button class="btn primary small" id="pt-run">生成当前卡片候选</button>
      <div id="pt-out" style="margin-top:12px"></div>
    </div>`;

  const out = card.querySelector("#pt-out");
  const hint = card.querySelector("#pt-hint");
  const btnS = card.querySelector("#pt-settings");
  const btnO = card.querySelector("#pt-outline");
  let kind = "settings";

  const HINTS = {
    settings: "收敛式：每条设定必须回答「它限制什么 / 代价是什么」，会让主角变轻松的条目请删掉。",
    outline: "铺开式：按章排张力；已确认设定是硬约束，不得越界引入新能力/势力/规则。确认细纲需要项目已有 10 章骨架。",
  };
  const syncTab = () => {
    btnS.className = "btn small" + (kind === "settings" ? " primary" : "");
    btnO.className = "btn small" + (kind === "outline" ? " primary" : "");
    hint.textContent = HINTS[kind];
    out.innerHTML = "";
  };
  btnS.onclick = () => { kind = "settings"; syncTab(); };
  btnO.onclick = () => { kind = "outline"; syncTab(); };
  syncTab();

  card.querySelector("#pt-run").onclick = async () => {
    out.innerHTML = '<div class="muted">生成中…</div>';
    try {
      const provider = card.querySelector("#pt-provider").value;
      const r = await api("POST", `/api/projects/${p.meta.id}/plan/${kind}`, { provider });
      renderPlanResult(card, p, r, kind);
      toast(`候选已生成（${r.plan.model_id}）`);
    } catch (err) {
      out.innerHTML = "";
      toast(err.message, true);
    }
  };
  return card;
}

function renderPlanResult(card, p, r, kind) {
  const out = card.querySelector("#pt-out");
  const plan = r.plan || {};
  const snip = r.knowledge || [];
  let html = `<div class="muted">来源 ${esc(plan.model_id)} · ${esc(plan.prompt_version)}
     · 知识库命中 ${snip.length} 条</div>`;

  if (kind === "settings") {
    const entries = plan.entries || [];
    html += `<h5 style="margin:10px 0 6px">设定候选（${entries.length}）</h5>`;
    html += entries.map((s) =>
      `<div class="agent-setting"><b>[${esc(s.entry_type)}] ${esc(s.name)}</b>：${esc(s.content)}</div>`
    ).join("");
    const g = plan.golden_finger;
    if (g) {
      html += `<div class="agent-setting"><b>金手指 · ${esc(g.name)}</b>（${esc(g.form)}）：${esc(g.ability)}
        <div class="muted">代价/限制：${esc(g.cost_limitation)}</div>
        <div class="muted">成长：${esc(g.growth_path)}</div></div>`;
    }
    html += `<button class="btn small primary" id="pt-accept-settings">确认写入项目设定</button>`;
  } else {
    const chapters = plan.chapters || [];
    html += `<h5 style="margin:10px 0 6px">细纲候选（${chapters.length} 章）</h5>`;
    chapters.forEach((c) => {
      const beats = c.beats || [];
      html += `<div class="plan-chapter"><b>第${c.order}章</b>
        <span class="muted">情绪走向 ${esc(c.emotion_track)}</span>`;
      html += beats.map((b) =>
        `<div class="agent-beat">第${b.order}拍 · ${esc(b.scene)}｜${esc((b.characters || []).join("、"))}｜${esc(b.event)}</div>`
      ).join("");
      html += `</div>`;
    });
    html += `<div class="muted" style="margin:8px 0">确认会<b>整章替换</b>对应章节拍（其余章节拍不动）。</div>`;
    html += `<button class="btn small primary" id="pt-accept-outline">确认写入这 ${chapters.length} 章节拍</button>`;
  }

  if ((plan.notes || []).length) {
    html += `<details style="margin-top:8px"><summary class="muted" style="cursor:pointer">产出方自我说明</summary>
      <div class="agent-trace">${plan.notes.map((n) => `<div>${esc(n)}</div>`).join("")}</div></details>`;
  }
  if (snip.length) {
    html += `<details style="margin-top:8px"><summary class="muted" style="cursor:pointer">参考了知识库哪些片段（${snip.length}）</summary>
      <div class="agent-trace">${snip.map((s) =>
        `<div>《${esc(s.source)}》${esc(s.heading)}：${esc(s.text)}</div>`).join("")}</div></details>`;
  }
  out.innerHTML = html;

  const btnS = out.querySelector("#pt-accept-settings");
  if (btnS) btnS.onclick = async () => {
    try {
      await api("PUT", `/api/projects/${p.meta.id}/plan/accept/settings`, {
        entries: plan.entries, golden_finger: plan.golden_finger,
      });
      toast("设定候选已确认为项目设定");
      refresh();
    } catch (err) { toast(err.message, true); }
  };
  const btnO = out.querySelector("#pt-accept-outline");
  if (btnO) btnO.onclick = async () => {
    try {
      await api("PUT", `/api/projects/${p.meta.id}/plan/accept/outline`, { chapters: plan.chapters });
      toast("细纲候选已确认为各章节拍");
      refresh();
    } catch (err) { toast(err.message, true); }
  };
}

refreshLlmStatus().then(refresh);
