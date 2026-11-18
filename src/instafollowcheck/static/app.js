"use strict";

/* ------------------------------------------------------------------ */
/* Utilidades compartilhadas                                           */
/* ------------------------------------------------------------------ */

const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";
const ACCOUNT_KEY = "instafollowcheck:account";
const KIND_KEY = "instafollowcheck:kind";
const TERMINAL_STATUSES = new Set(["success", "error", "cancelled"]);
const ACTIVE_STATUSES = new Set(["queued", "running", "waiting_login"]);

const KIND_META = {
  not_following_back: {
    label: "Não te seguem de volta",
    description: "Você segue estas contas, mas elas não seguem você de volta.",
    empty: "Todas as contas que você segue também seguem você. Nada por aqui.",
  },
  lost_followers: {
    label: "Deixaram de seguir",
    description: "Contas que apareciam no histórico e não estão mais na lista atual.",
    empty: "Nenhuma perda de seguidor na janela analisada.",
    seen: "Última aparição",
  },
  followers: {
    label: "Seguidores",
    description: "Todas as contas presentes no último snapshot de seguidores.",
    empty: "Nenhum seguidor no snapshot atual.",
  },
  following: {
    label: "Seguindo",
    description: "Todas as contas que você seguia no último snapshot.",
    empty: "Nenhuma conta seguida no snapshot atual.",
  },
  not_followed_back: {
    label: "Você não segue de volta",
    description: "Estas contas seguem você, mas você não segue de volta.",
    empty: "Você segue de volta todas as contas que te seguem.",
  },
  new_followers: {
    label: "Novos seguidores",
    description: "Contas que entraram na lista de seguidores dentro da janela.",
    empty: "Nenhum seguidor novo na janela analisada.",
    seen: "Primeira aparição",
  },
  mutual: {
    label: "Mútuos",
    description: "Contas que você segue e que também seguem você.",
    empty: "Nenhuma relação mútua no snapshot atual.",
  },
  lost_following: {
    label: "Você deixou de seguir",
    description: "Contas que estavam em seguindo e saíram do snapshot atual.",
    empty: "Nenhuma conta que você deixou de seguir na janela.",
    seen: "Última aparição",
  },
  new_following: {
    label: "Novos seguindo",
    description: "Contas que entraram na sua lista de seguindo dentro da janela.",
    empty: "Nenhuma conta nova em seguindo na janela.",
    seen: "Primeira aparição",
  },
  possible_soft_blocks: {
    label: "Possíveis soft blocks",
    description:
      "Perdas nas duas direções. É só uma hipótese: unfollow, bloqueio ou conta desativada podem parecer iguais.",
    empty: "Nenhuma perda nas duas direções na janela.",
    seen: "Última aparição",
  },
};

class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function formatNumber(value) {
  return new Intl.NumberFormat("pt-BR").format(Number(value) || 0);
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateTime(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleString("pt-BR", { dateStyle: "medium", timeStyle: "short" });
}

function formatDay(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleDateString("pt-BR", { dateStyle: "medium" });
}

function formatRelative(value) {
  const date = parseDate(value);
  if (!date) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "agora";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  const days = Math.round(hours / 24);
  if (days === 1) return "ontem";
  if (days < 30) return `há ${days} dias`;
  const months = Math.round(days / 30);
  if (months < 12) return `há ${months} ${months === 1 ? "mês" : "meses"}`;
  const years = Math.round(months / 12);
  return `há ${years} ${years === 1 ? "ano" : "anos"}`;
}

function plural(count, singular, pluralForm) {
  return `${formatNumber(count)} ${count === 1 ? singular : pluralForm}`;
}

function setHidden(element, hidden) {
  if (element) element.hidden = Boolean(hidden);
}

function showNotice(element, message, type = "info") {
  if (!element) return;
  element.textContent = message;
  element.classList.remove("notice-error", "notice-success");
  if (type === "error") element.classList.add("notice-error");
  if (type === "success") element.classList.add("notice-success");
  element.hidden = !message;
}

let toastTimer = null;
function showToast(message, type = "") {
  const toast = $("#toast");
  if (!toast) return;
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.remove("is-error", "is-success");
  if (type === "error") toast.classList.add("is-error");
  if (type === "success") toast.classList.add("is-success");
  toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, type === "error" ? 6500 : 4200);
}

function setFooterDataDir(path) {
  const element = $("#footer-data-dir");
  if (element && path) element.textContent = path;
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = new Headers(options.headers || {});
  let body = options.body;
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-CSRF-Token", CSRF);
  }
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  if (options.formData) {
    body = options.formData;
  }
  const response = await fetch(path, {
    method,
    headers,
    body,
    credentials: "same-origin",
    cache: "no-store",
  });
  const type = response.headers.get("content-type") || "";
  let data = null;
  if (type.includes("application/json")) {
    data = await response.json().catch(() => null);
  } else {
    data = await response.text().catch(() => "");
  }
  if (!response.ok) {
    const message =
      (data && typeof data === "object" && data.error) ||
      (typeof data === "string" && data.trim()) ||
      `A solicitação falhou (${response.status}).`;
    throw new ApiError(message, response.status, data);
  }
  return data;
}

function normalizeUsername(value) {
  return String(value || "")
    .trim()
    .replace(/^@+/, "")
    .toLowerCase();
}

function validUsername(value) {
  return /^[a-z0-9._]{1,30}$/.test(value);
}

function openDialog(dialog) {
  if (!dialog) return;
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }
}

function closeDialog(dialog) {
  if (!dialog) return;
  if (typeof dialog.close === "function") {
    dialog.close();
  } else {
    dialog.removeAttribute("open");
  }
}

function wireDialogs() {
  $$("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(button.closest("dialog")));
  });
  $$("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
  });
}

function hueFor(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) % 360;
  }
  return hash;
}

function emptyBlock(message) {
  const block = document.createElement("div");
  block.className = "empty-list";
  block.textContent = message;
  return block;
}

function loadingBlock(message = "Carregando…") {
  const block = document.createElement("div");
  block.className = "is-loading-block";
  const spinner = document.createElement("span");
  spinner.className = "spinner";
  const label = document.createElement("span");
  label.textContent = message;
  block.append(spinner, label);
  return block;
}

/* ------------------------------------------------------------------ */
/* Painel                                                              */
/* ------------------------------------------------------------------ */

function initDashboard() {
  const dashboard = {
    state: null,
    accountKey: localStorage.getItem(ACCOUNT_KEY) || "",
    kind: localStorage.getItem(KIND_KEY) || "",
    page: 1,
    query: "",
    listRequest: 0,
    jobId: null,
    jobActive: false,
    jobTimer: null,
    jobHideTimer: null,
    lastJobStage: "",
    searchTimer: null,
  };

  const refs = {
    notice: $("#notice"),
    emptyState: $("#empty-state"),
    accountBar: $("#account-bar"),
    dashboard: $("#dashboard"),
    collect: $("#collect-button"),
    import: $("#import-button"),
    emptyConnect: $("#empty-connect-button"),
    emptyImport: $("#empty-import-button"),
    newAccount: $("#new-account-button"),
    accountSelect: $("#account-select"),
    lastCollection: $("#account-last-collection"),
    cooldownHint: $("#cooldown-hint"),
    jobCard: $("#job-card"),
    jobStage: $("#job-stage"),
    jobTitle: $("#job-title"),
    jobMessage: $("#job-message"),
    jobProgress: $("#job-progress"),
    jobProgressBar: $("#job-progress-bar"),
    jobCounter: $("#job-counter"),
    jobHint: $("#job-hint"),
    jobCancel: $("#job-cancel-button"),
    listTitle: $("#list-title"),
    listSubtitle: $("#list-subtitle"),
    listTabs: $("#list-tabs"),
    listSearch: $("#list-search"),
    listCount: $("#list-count"),
    listBody: $("#list-body"),
    listPager: $("#list-pager"),
    pagePrev: $("#page-prev"),
    pageNext: $("#page-next"),
    pageLabel: $("#page-label"),
    csv: $("#csv-link"),
    windowTitle: $("#window-title"),
    windowSummary: $("#window-summary"),
    warningsCard: $("#warnings-card"),
    warningsList: $("#warnings-list"),
    accountDialog: $("#account-dialog"),
    accountForm: $("#account-form"),
    accountUsername: $("#account-username"),
    accountSubmit: $("#account-submit"),
    importDialog: $("#import-dialog"),
    importForm: $("#import-form"),
    importUsername: $("#import-username"),
    importFile: $("#import-file"),
    importOwnerId: $("#import-owner-id"),
    importError: $("#import-error"),
    importSubmit: $("#import-submit"),
    live: $("#live-region"),
  };

  function setBusyButton(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.dataset.originalLabel = button.textContent;
      button.textContent = label || "Aguarde…";
    } else if (button.dataset.originalLabel) {
      button.textContent = button.dataset.originalLabel;
      delete button.dataset.originalLabel;
    }
    button.disabled = busy;
  }

  function updateCollectAvailability() {
    const hasAccount = Boolean(dashboard.state?.account);
    const cooldown = Number(dashboard.state?.cooldown_remaining || 0);
    refs.collect.disabled = dashboard.jobActive || (hasAccount && cooldown > 0);
    if (dashboard.jobActive) {
      refs.collect.title = "Uma coleta está em andamento.";
    } else if (hasAccount && cooldown > 0) {
      refs.collect.title = "Aguarde o intervalo mínimo entre coletas.";
    } else {
      refs.collect.title = "";
    }
    if (hasAccount && cooldown > 0) {
      const minutes = Math.max(1, Math.ceil(cooldown / 60));
      refs.cooldownHint.textContent = `Nova coleta em ~${minutes} min`;
      refs.cooldownHint.hidden = false;
    } else {
      refs.cooldownHint.hidden = true;
    }
  }

  function renderAccounts(data) {
    const accounts = data.accounts || [];
    refs.accountSelect.replaceChildren();
    accounts.forEach((account) => {
      const option = document.createElement("option");
      option.value = account.owner_key;
      option.textContent = `@${account.username}`;
      refs.accountSelect.append(option);
    });
    if (data.account) {
      refs.accountSelect.value = data.account.owner_key;
    }
    refs.accountSelect.disabled = accounts.length <= 1;
    const hasAccounts = accounts.length > 0;
    setHidden(refs.emptyState, hasAccounts);
    setHidden(refs.accountBar, !hasAccounts);
    setHidden(refs.dashboard, !hasAccounts);
    refs.collect.disabled = dashboard.jobActive;
    if (!hasAccounts) {
      refs.collect.disabled = dashboard.jobActive;
    }
  }

  function renderStats(diff) {
    const summary = diff?.summary || {};
    $$("[data-stat]").forEach((element) => {
      const key = element.dataset.stat;
      element.textContent = formatNumber(summary[key] || 0);
    });
    $$("[data-count]").forEach((element) => {
      const key = element.dataset.count;
      element.textContent = formatNumber(summary[key] || 0);
    });
    const lostNote = $('[data-stat-note="lost_followers"]');
    if (lostNote && diff) {
      lostNote.textContent = diff.latest
        ? `nos últimos ${diff.window_days} dias`
        : "aguardando a primeira coleta";
    }
    const followerNote = $('[data-stat-note="followers"]');
    if (followerNote && diff?.latest) {
      followerNote.textContent = `snapshot de ${formatRelative(diff.latest.captured_at)}`;
    }
    const followingNote = $('[data-stat-note="following"]');
    if (followingNote && diff?.latest) {
      followingNote.textContent = `snapshot de ${formatRelative(diff.latest.captured_at)}`;
    }

    if (diff) {
      refs.windowTitle.textContent = `Últimos ${diff.window_days} dias`;
      if (!diff.latest) {
        refs.windowSummary.textContent =
          "Ainda não há uma coleta completa. Faça a primeira leitura para criar a linha de base.";
      } else if (diff.effective_days === 0 && diff.window_start === diff.latest.captured_at) {
        refs.windowSummary.textContent = `Primeira coleta em ${formatDateTime(
          diff.latest.captured_at,
        )}. Os deltas aparecem na próxima.`;
      } else {
        refs.windowSummary.textContent = `Comparação desde ${formatDateTime(
          diff.window_start,
        )} · ${formatNumber(Math.round(diff.effective_days))} dias efetivos.`;
      }

      const warnings = diff.warnings || [];
      refs.warningsList.replaceChildren();
      warnings.forEach((warning) => {
        const item = document.createElement("li");
        item.textContent = warning;
        refs.warningsList.append(item);
      });
      setHidden(refs.warningsCard, warnings.length === 0);

      if (diff.latest) {
        refs.lastCollection.textContent = `Última coleta: ${formatRelative(
          diff.latest.captured_at,
        )} · ${formatDateTime(diff.latest.captured_at)}`;
      } else {
        refs.lastCollection.textContent = "Ainda sem coleta completa";
      }
    } else {
      refs.windowTitle.textContent = "Janela";
      refs.windowSummary.textContent = "Conecte uma conta para começar.";
      setHidden(refs.warningsCard, true);
      refs.lastCollection.textContent = "Ainda sem coleta";
    }

    if (diff?.latest) {
      const source = diff.latest.source;
      const sourceLabel =
        source === "browser" ? "navegador" : source === "export" ? "exportação" : "migração";
      refs.lastCollection.textContent += ` · fonte: ${sourceLabel}`;
    }
  }

  function ensureKind(diff) {
    if (dashboard.kind && KIND_META[dashboard.kind]) return;
    const summary = diff?.summary || {};
    dashboard.kind = (summary.lost_followers || 0) > 0 ? "lost_followers" : "not_following_back";
    localStorage.setItem(KIND_KEY, dashboard.kind);
  }

  function renderKindChrome() {
    const meta = KIND_META[dashboard.kind] || KIND_META.not_following_back;
    refs.listTitle.textContent = meta.label;
    refs.listSubtitle.textContent = meta.description;
    $$("[data-kind]", refs.listTabs).forEach((tab) => {
      const active = tab.dataset.kind === dashboard.kind;
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.setAttribute("tabindex", active ? "0" : "-1");
    });
    const windowDays = dashboard.state?.window || 10;
    const params = new URLSearchParams({ kind: dashboard.kind, window: String(windowDays) });
    if (dashboard.accountKey) params.set("account", dashboard.accountKey);
    refs.csv.href = `/api/export.csv?${params.toString()}`;
    refs.listSearch.value = dashboard.query;
  }

  function renderJob(job) {
    if (!job) return;
    const stageLabels = {
      queued: "Na fila",
      starting: "Preparando",
      login: "Sessão",
      waiting_login: "Aguardando login",
      profile: "Perfil",
      followers: "Seguidores",
      following: "Seguindo",
      saving: "Salvando",
      done: "Concluído",
      error: "Erro",
      cancelled: "Cancelado",
    };
    const titles = {
      queued: "Coleta na fila",
      starting: "Abrindo o navegador…",
      login: "Conferindo a sessão do Instagram",
      waiting_login: "Entre na janela do Chrome",
      profile: "Lendo o perfil",
      followers: "Lendo seguidores",
      following: "Lendo quem você segue",
      saving: "Salvando o snapshot",
      done: "Coleta concluída",
      error: "Não foi possível concluir",
      cancelled: "Coleta cancelada",
    };
    dashboard.jobId = job.id;
    dashboard.jobActive = ACTIVE_STATUSES.has(job.status);
    refs.jobCard.hidden = false;
    refs.jobCard.classList.toggle("is-error", job.status === "error");
    refs.jobCard.classList.toggle("is-success", job.status === "success");
    refs.jobCard.classList.toggle(
      "is-waiting",
      job.status === "waiting_login" || job.stage === "waiting_login",
    );
    refs.jobStage.textContent = stageLabels[job.stage] || stageLabels[job.status] || "Coleta";
    refs.jobTitle.textContent = titles[job.stage] || titles[job.status] || "Coleta em andamento";
    refs.jobMessage.textContent = job.message || "Aguarde enquanto os dados são lidos.";
    refs.jobCancel.hidden = !dashboard.jobActive;

    const total = Number(job.total || 0);
    const current = Number(job.current || 0);
    if (total > 0) {
      const percent = Math.max(0, Math.min(100, Math.round((current / total) * 100)));
      refs.jobProgress.classList.remove("is-indeterminate");
      refs.jobProgress.setAttribute("aria-valuenow", String(percent));
      refs.jobProgressBar.style.width = `${percent}%`;
      refs.jobCounter.textContent = `${formatNumber(current)} de ${formatNumber(total)}`;
    } else if (dashboard.jobActive) {
      refs.jobProgress.classList.add("is-indeterminate");
      refs.jobProgress.removeAttribute("aria-valuenow");
      refs.jobProgressBar.style.width = "34%";
      refs.jobCounter.textContent = job.status === "waiting_login" ? "Aguardando você" : "Lendo…";
    } else {
      refs.jobProgress.classList.remove("is-indeterminate");
      refs.jobProgressBar.style.width = job.status === "success" ? "100%" : "0%";
      refs.jobCounter.textContent = "";
    }

    if (job.stage === "waiting_login") {
      refs.jobHint.textContent =
        "Conclua o login na janela do Chrome. O app detecta a sessão automaticamente.";
    } else if (dashboard.jobActive) {
      refs.jobHint.textContent = "Você pode deixar esta aba aberta.";
    } else if (job.status === "success") {
      refs.jobHint.textContent = "Os dados foram salvos neste computador.";
    } else {
      refs.jobHint.textContent = "";
    }

    if (
      job.stage === "waiting_login" &&
      dashboard.lastJobStage !== "waiting_login" &&
      dashboard.jobActive
    ) {
      showToast("Faça login na janela do Chrome que o app abriu.");
      refs.live.textContent = "Login necessário na janela do Chrome.";
    }
    dashboard.lastJobStage = job.stage;
    updateCollectAvailability();
  }

  async function pollJob() {
    if (!dashboard.jobId) return;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(dashboard.jobId)}`);
      renderJob(job);
      if (TERMINAL_STATUSES.has(job.status)) {
        stopJobPolling();
        dashboard.jobActive = false;
        updateCollectAvailability();
        if (job.status === "success") {
          showToast("Coleta concluída. Painel atualizado.", "success");
          dashboard.jobHideTimer = window.setTimeout(() => {
            if (!dashboard.jobActive) refs.jobCard.hidden = true;
          }, 7000);
        } else if (job.status === "cancelled") {
          showToast("Coleta cancelada.");
          dashboard.jobHideTimer = window.setTimeout(() => {
            if (!dashboard.jobActive) refs.jobCard.hidden = true;
          }, 5000);
        } else {
          showToast(job.error || job.message || "A coleta falhou.", "error");
        }
        await refreshDashboard({ reloadList: true });
      }
    } catch (error) {
      stopJobPolling();
      dashboard.jobActive = false;
      updateCollectAvailability();
      showToast(error.message || "Não foi possível acompanhar a coleta.", "error");
    }
  }

  function stopJobPolling() {
    if (dashboard.jobTimer) {
      window.clearInterval(dashboard.jobTimer);
      dashboard.jobTimer = null;
    }
  }

  function watchJob(job) {
    if (!job) return;
    window.clearTimeout(dashboard.jobHideTimer);
    const alreadyWatching = dashboard.jobId === job.id && dashboard.jobTimer;
    dashboard.jobId = job.id;
    dashboard.jobActive = ACTIVE_STATUSES.has(job.status);
    renderJob(job);
    if (dashboard.jobActive && !alreadyWatching) {
      stopJobPolling();
      dashboard.jobTimer = window.setInterval(pollJob, 1300);
    } else if (!dashboard.jobActive) {
      stopJobPolling();
    }
  }

  async function startCollect(username) {
    const normalized = normalizeUsername(username);
    if (!validUsername(normalized)) {
      throw new Error("Informe um usuário válido, sem @ e sem link.");
    }
    refs.collect.classList.add("is-loading");
    try {
      const job = await api("/api/collect", { method: "POST", json: { username: normalized } });
      watchJob(job);
      showToast("Coleta iniciada.");
    } catch (error) {
      if (error.status === 409 && error.data && error.data.job) {
        watchJob(error.data.job);
        showToast("Uma coleta já está em andamento.");
        return;
      }
      throw error;
    } finally {
      refs.collect.classList.remove("is-loading");
    }
  }

  async function refreshDashboard({ reloadList = true } = {}) {
    try {
      const params = new URLSearchParams();
      if (dashboard.accountKey) params.set("account", dashboard.accountKey);
      const suffix = params.toString() ? `?${params.toString()}` : "";
      const data = await api(`/api/state${suffix}`);
      dashboard.state = data;
      if (data.account) {
        dashboard.accountKey = data.account.owner_key;
        localStorage.setItem(ACCOUNT_KEY, dashboard.accountKey);
      } else {
        dashboard.accountKey = "";
        localStorage.removeItem(ACCOUNT_KEY);
      }
      setFooterDataDir(data.data_dir);
      renderAccounts(data);
      renderStats(data.diff);
      ensureKind(data.diff);
      renderKindChrome();
      updateCollectAvailability();
      if (data.active_job) {
        watchJob(data.active_job);
      } else {
        dashboard.jobActive = false;
        updateCollectAvailability();
      }
      if (data.account && reloadList) {
        await loadList(dashboard.kind, dashboard.page, { quiet: true });
      }
    } catch (error) {
      showNotice(refs.notice, error.message || "Não foi possível carregar o painel.", "error");
    }
  }

  async function loadList(kind, page = 1, { quiet = false } = {}) {
    if (!dashboard.state?.account) {
      refs.listBody.replaceChildren(
        emptyBlock("Conecte uma conta para carregar as listas."),
      );
      return;
    }
    const requestId = ++dashboard.listRequest;
    if (!quiet) {
      refs.listBody.replaceChildren(loadingBlock("Carregando lista…"));
    }
    const params = new URLSearchParams({
      kind,
      page: String(page),
      window: String(dashboard.state.window || 10),
    });
    if (dashboard.accountKey) params.set("account", dashboard.accountKey);
    if (dashboard.query) params.set("q", dashboard.query);
    try {
      const data = await api(`/api/list?${params.toString()}`);
      if (requestId !== dashboard.listRequest) return;
      dashboard.page = data.page || page;
      renderPeople(data);
    } catch (error) {
      if (requestId !== dashboard.listRequest) return;
      refs.listBody.replaceChildren(emptyBlock(error.message || "Não foi possível ler a lista."));
      refs.listCount.textContent = "";
      setHidden(refs.listPager, true);
    }
  }

  function renderPeople(data) {
    refs.listBody.replaceChildren();
    const meta = KIND_META[dashboard.kind] || {};
    if (!data.items || data.items.length === 0) {
      let message = meta.empty || "Nenhuma conta nesta lista.";
      if (dashboard.query) {
        message = `Nenhuma conta encontrada para “${dashboard.query}”.`;
      }
      refs.listBody.append(emptyBlock(message));
      refs.listCount.textContent = "";
      setHidden(refs.listPager, true);
      return;
    }

    const fragment = document.createDocumentFragment();
    data.items.forEach((person) => {
      const row = document.createElement("div");
      row.className = "person";

      const avatar = document.createElement("span");
      avatar.className = "avatar";
      avatar.style.setProperty("--h", String(hueFor(person.username)));
      avatar.textContent = person.username.slice(0, 1) || "?";
      avatar.setAttribute("aria-hidden", "true");

      const main = document.createElement("div");
      main.className = "person-main";
      const name = document.createElement("div");
      name.className = "person-name";
      const link = document.createElement("a");
      link.href = `https://www.instagram.com/${encodeURIComponent(person.username)}/`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `@${person.username}`;
      name.append(link);
      main.append(name);

      if (meta.seen && person.seen_at) {
        const metaLine = document.createElement("div");
        metaLine.className = "person-meta";
        metaLine.textContent = `${meta.seen}: ${formatDateTime(person.seen_at)}`;
        main.append(metaLine);
      }

      const external = document.createElement("a");
      external.className = "person-link";
      external.href = link.href;
      external.target = "_blank";
      external.rel = "noopener noreferrer";
      external.setAttribute("aria-label", `Abrir @${person.username} no Instagram`);
      external.textContent = "↗";

      row.append(avatar, main, external);
      fragment.append(row);
    });
    refs.listBody.append(fragment);
    refs.listCount.textContent = plural(data.total, "conta", "contas");
    const pages = Number(data.pages || 0);
    setHidden(refs.listPager, pages <= 1);
    refs.pageLabel.textContent = pages > 0 ? `Página ${formatNumber(data.page)} de ${formatNumber(pages)}` : "";
    refs.pagePrev.disabled = data.page <= 1;
    refs.pageNext.disabled = data.page >= pages;
  }

  function selectKind(kind) {
    if (!KIND_META[kind]) return;
    dashboard.kind = kind;
    dashboard.page = 1;
    localStorage.setItem(KIND_KEY, kind);
    renderKindChrome();
    loadList(kind, 1);
    refs.listBody.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function selectAccount(ownerKey) {
    dashboard.accountKey = ownerKey || "";
    dashboard.page = 1;
    dashboard.query = "";
    refs.listSearch.value = "";
    localStorage.setItem(ACCOUNT_KEY, dashboard.accountKey);
    refreshDashboard();
  }

  function openAccountDialog() {
    refs.accountForm.reset();
    refs.accountSubmit.disabled = dashboard.jobActive;
    if (dashboard.state?.account) {
      refs.accountUsername.value = dashboard.state.account.username;
    }
    openDialog(refs.accountDialog);
    window.setTimeout(() => refs.accountUsername.focus(), 60);
  }

  function openImportDialog() {
    refs.importForm.reset();
    refs.importError.hidden = true;
    refs.importSubmit.disabled = false;
    refs.importUsername.value = dashboard.state?.account?.username || "";
    openDialog(refs.importDialog);
  }

  refs.accountSelect.addEventListener("change", (event) => {
    selectAccount(event.target.value);
  });

  refs.collect.addEventListener("click", () => {
    if (dashboard.state?.account) {
      startCollect(dashboard.state.account.username).catch((error) => {
        showToast(error.message || "Não foi possível iniciar a coleta.", "error");
        refreshDashboard({ reloadList: false });
      });
    } else {
      openAccountDialog();
    }
  });

  refs.newAccount.addEventListener("click", openAccountDialog);
  refs.emptyConnect.addEventListener("click", openAccountDialog);
  refs.import.addEventListener("click", openImportDialog);
  refs.emptyImport.addEventListener("click", openImportDialog);

  refs.listTabs.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-kind]");
    if (tab) selectKind(tab.dataset.kind);
  });

  refs.listTabs.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const tabs = $$("[data-kind]", refs.listTabs);
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
    tabs[next].focus();
    selectKind(tabs[next].dataset.kind);
  });

  refs.listSearch.addEventListener("input", () => {
    window.clearTimeout(dashboard.searchTimer);
    dashboard.searchTimer = window.setTimeout(() => {
      dashboard.query = refs.listSearch.value.trim().toLowerCase();
      loadList(dashboard.kind, 1, { quiet: false });
    }, 280);
  });

  refs.pagePrev.addEventListener("click", () => {
    if (dashboard.page > 1) loadList(dashboard.kind, dashboard.page - 1);
  });

  refs.pageNext.addEventListener("click", () => {
    loadList(dashboard.kind, dashboard.page + 1);
  });

  $$("[data-open-kind]").forEach((card) => {
    card.addEventListener("click", () => selectKind(card.dataset.openKind));
  });

  refs.accountForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = normalizeUsername(refs.accountUsername.value);
    if (!validUsername(username)) {
      refs.accountUsername.focus();
      showToast("Informe um usuário válido, sem @ e sem link.", "error");
      return;
    }
    refs.accountSubmit.disabled = true;
    const original = refs.accountSubmit.textContent;
    refs.accountSubmit.textContent = "Abrindo o Chrome…";
    try {
      await startCollect(username);
      closeDialog(refs.accountDialog);
      window.setTimeout(() => refreshDashboard({ reloadList: true }), 700);
    } catch (error) {
      showToast(error.message || "Não foi possível iniciar a coleta.", "error");
    } finally {
      refs.accountSubmit.disabled = false;
      refs.accountSubmit.textContent = original;
    }
  });

  refs.importForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    refs.importError.hidden = true;
    const username = normalizeUsername(refs.importUsername.value);
    const files = Array.from(refs.importFile.files || []);
    if (!validUsername(username)) {
      refs.importError.textContent = "Informe um usuário válido, sem @ e sem link.";
      refs.importError.hidden = false;
      return;
    }
    if (files.length === 0) {
      refs.importError.textContent = "Selecione o ZIP ou os arquivos JSON da exportação.";
      refs.importError.hidden = false;
      return;
    }
    const formData = new FormData();
    formData.append("username", username);
    const ownerId = refs.importOwnerId.value.trim();
    if (ownerId) formData.append("owner_id", ownerId);
    files.forEach((file) => formData.append("file", file, file.name));

    refs.importSubmit.disabled = true;
    const original = refs.importSubmit.textContent;
    refs.importSubmit.textContent = "Importando…";
    try {
      const result = await api("/api/import-export", { method: "POST", formData });
      closeDialog(refs.importDialog);
      dashboard.accountKey = result.account_key;
      localStorage.setItem(ACCOUNT_KEY, dashboard.accountKey);
      showToast(
        `Exportação importada: ${formatNumber(result.followers)} seguidores e ${formatNumber(
          result.following,
        )} seguindo.`,
        "success",
      );
      await refreshDashboard();
    } catch (error) {
      refs.importError.textContent = error.message || "Não foi possível importar a exportação.";
      refs.importError.hidden = false;
    } finally {
      refs.importSubmit.disabled = false;
      refs.importSubmit.textContent = original;
    }
  });

  refs.jobCancel.addEventListener("click", async () => {
    if (!dashboard.jobId) return;
    refs.jobCancel.disabled = true;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(dashboard.jobId)}/cancel`, {
        method: "POST",
        json: {},
      });
      renderJob(job);
      showToast("Cancelamento solicitado.");
    } catch (error) {
      showToast(error.message || "Não foi possível cancelar.", "error");
    } finally {
      refs.jobCancel.disabled = false;
    }
  });

  refreshDashboard();
}

/* ------------------------------------------------------------------ */
/* Histórico                                                          */
/* ------------------------------------------------------------------ */

function initHistory() {
  const refs = {
    account: $("#history-account"),
    meta: $("#history-meta"),
    notice: $("#history-notice"),
    empty: $("#history-empty"),
    content: $("#history-content"),
    body: $("#history-body"),
  };
  let accountKey = localStorage.getItem(ACCOUNT_KEY) || "";

  async function load() {
    showNotice(refs.notice, "");
    try {
      const stateParams = new URLSearchParams();
      if (accountKey) stateParams.set("account", accountKey);
      const stateSuffix = stateParams.toString() ? `?${stateParams.toString()}` : "";
      const state = await api(`/api/state${stateSuffix}`);
      setFooterDataDir(state.data_dir);
      accountKey = state.account?.owner_key || "";
      if (accountKey) localStorage.setItem(ACCOUNT_KEY, accountKey);
      refs.account.replaceChildren();
      (state.accounts || []).forEach((account) => {
        const option = document.createElement("option");
        option.value = account.owner_key;
        option.textContent = `@${account.username}`;
        refs.account.append(option);
      });
      if (accountKey) refs.account.value = accountKey;
      refs.account.disabled = (state.accounts || []).length <= 1;

      if (!accountKey) {
        refs.meta.textContent = "Nenhuma conta conectada.";
        setHidden(refs.empty, false);
        setHidden(refs.content, true);
        return;
      }

      const history = await api(`/api/history?account=${encodeURIComponent(accountKey)}`);
      setHidden(refs.empty, history.history.length > 0);
      setHidden(refs.content, history.history.length === 0);
      renderHistory(history.history);
      const account = history.account || state.account;
      refs.meta.textContent = `@${account.username} · ${plural(
        history.history.length,
        "coleta registrada",
        "coletas registradas",
      )}`;
    } catch (error) {
      showNotice(refs.notice, error.message || "Não foi possível carregar o histórico.", "error");
    }
  }

  function renderHistory(rows) {
    refs.body.replaceChildren();
    rows.forEach((row, index) => {
      const previous = rows[index + 1] || null;
      const tr = document.createElement("tr");

      const when = document.createElement("td");
      when.className = "date-cell";
      const strong = document.createElement("strong");
      strong.textContent = formatDateTime(row.captured_at);
      const relative = document.createElement("span");
      relative.textContent = formatRelative(row.captured_at);
      when.append(strong, relative);

      const source = document.createElement("td");
      const badge = document.createElement("span");
      const sourceName = row.source === "browser" ? "Navegador" : row.source === "export" ? "Exportação" : "Migração";
      badge.className = `source-badge source-${row.source}`;
      badge.textContent = sourceName;
      source.append(badge);

      const followers = document.createElement("td");
      followers.className = "numeric";
      followers.append(document.createTextNode(formatNumber(row.followers_count)));
      followers.append(deltaElement(row.followers_count, previous?.followers_count));

      const following = document.createElement("td");
      following.className = "numeric";
      following.append(document.createTextNode(formatNumber(row.following_count)));
      following.append(deltaElement(row.following_count, previous?.following_count));

      const note = document.createElement("td");
      note.className = "note-cell";
      const notes = [];
      if (row.note) notes.push(row.note);
      if (row.reported_followers != null && row.reported_followers !== row.followers_count) {
        notes.push(`Instagram informou ${formatNumber(row.reported_followers)} seguidores.`);
      }
      if (row.reported_following != null && row.reported_following !== row.following_count) {
        notes.push(`Instagram informou ${formatNumber(row.reported_following)} em seguindo.`);
      }
      note.textContent = notes.length ? notes.join(" ") : "—";

      tr.append(when, source, followers, following, note);
      refs.body.append(tr);
    });
  }

  function deltaElement(current, previous) {
    const element = document.createElement("span");
    element.className = "delta";
    if (previous == null) {
      element.textContent = "linha de base";
      element.classList.add("delta-flat");
      return element;
    }
    const delta = Number(current) - Number(previous);
    if (delta === 0) {
      element.textContent = "sem mudança";
      element.classList.add("delta-flat");
    } else if (delta > 0) {
      element.textContent = `+${formatNumber(delta)}`;
      element.classList.add("delta-up");
    } else {
      element.textContent = formatNumber(delta);
      element.classList.add("delta-down");
    }
    return element;
  }

  refs.account.addEventListener("change", () => {
    accountKey = refs.account.value;
    localStorage.setItem(ACCOUNT_KEY, accountKey);
    load();
  });

  load();
}

/* ------------------------------------------------------------------ */
/* Configurações                                                      */
/* ------------------------------------------------------------------ */

function initSettings() {
  const refs = {
    notice: $("#settings-notice"),
    form: $("#settings-form"),
    windowDays: $("#window-days"),
    cooldown: $("#cooldown-minutes"),
    saved: $("#settings-saved"),
    dataDir: $("#settings-data-dir"),
    copyPath: $("#copy-path-button"),
    accounts: $("#settings-accounts"),
    addAccount: $("#settings-new-account"),
    resetBrowser: $("#reset-browser-button"),
    dialog: $("#settings-account-dialog"),
    dialogForm: $("#settings-account-form"),
    dialogUsername: $('#settings-account-form input[name="username"]'),
  };
  let state = null;
  let accountKey = localStorage.getItem(ACCOUNT_KEY) || "";

  async function load() {
    try {
      const params = new URLSearchParams();
      if (accountKey) params.set("account", accountKey);
      const suffix = params.toString() ? `?${params.toString()}` : "";
      state = await api(`/api/state${suffix}`);
      setFooterDataDir(state.data_dir);
      accountKey = state.account?.owner_key || "";
      if (accountKey) localStorage.setItem(ACCOUNT_KEY, accountKey);
      refs.dataDir.textContent = state.data_dir;
      refs.windowDays.value = String(state.settings.window_days);
      refs.cooldown.value = String(state.settings.cooldown_minutes);
      renderAccounts();
    } catch (error) {
      showNotice(refs.notice, error.message || "Não foi possível carregar as configurações.", "error");
    }
  }

  function renderAccounts() {
    refs.accounts.replaceChildren();
    const accounts = state?.accounts || [];
    if (accounts.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-inline";
      empty.textContent = "Nenhuma conta conectada ainda.";
      refs.accounts.append(empty);
    }
    accounts.forEach((account) => {
      const row = document.createElement("div");
      row.className = "account-row";

      const main = document.createElement("div");
      main.className = "account-row-main";
      const name = document.createElement("div");
      name.className = "account-row-name";
      const label = document.createElement("span");
      label.textContent = `@${account.username}`;
      name.append(label);
      const source = document.createElement("span");
      const sourceName =
        account.last_source === "browser"
          ? "navegador"
          : account.last_source === "export"
            ? "exportação"
            : account.last_source === "legacy"
              ? "migração"
              : "sem coleta";
      source.className = "source-badge source-browser";
      if (account.last_source === "export") source.className = "source-badge source-export";
      if (account.last_source === "legacy") source.className = "source-badge source-legacy";
      source.textContent = sourceName;
      name.append(source);
      const meta = document.createElement("div");
      meta.className = "account-row-meta";
      if (account.captured_at) {
        meta.textContent = `Última coleta em ${formatDateTime(account.captured_at)} · ${formatNumber(
          account.followers_count,
        )} seguidores · ${formatNumber(account.following_count)} seguindo · ${account.owner_key}`;
      } else {
        meta.textContent = `Sem snapshot completo · ${account.owner_key}`;
      }
      main.append(name, meta);

      const actions = document.createElement("div");
      actions.className = "account-row-actions";
      const reset = document.createElement("button");
      reset.type = "button";
      reset.className = "btn btn-small btn-ghost";
      reset.textContent = "Redefinir Chrome";
      reset.addEventListener("click", () => resetBrowser(account.username));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-small btn-danger";
      remove.textContent = "Apagar histórico";
      remove.addEventListener("click", () => deleteAccount(account));
      actions.append(reset, remove);

      row.append(main, actions);
      refs.accounts.append(row);
    });

    const selected = accounts.find((account) => account.owner_key === accountKey) || accounts[0];
    refs.resetBrowser.disabled = !selected;
    refs.resetBrowser.textContent = selected
      ? `Redefinir perfil do Chrome (@${selected.username})`
      : "Redefinir perfil do Chrome";
    refs.resetBrowser.onclick = selected ? () => resetBrowser(selected.username) : null;
  }

  async function resetBrowser(username) {
    const confirmed = window.confirm(
      `Apagar somente o perfil do Chrome de @${username}? A sessão será perdida e o histórico de coletas será preservado.`,
    );
    if (!confirmed) return;
    try {
      await api("/api/browser/reset", { method: "POST", json: { username } });
      showToast("Perfil do Chrome redefinido. A próxima coleta pedirá login.", "success");
    } catch (error) {
      showToast(error.message || "Não foi possível redefinir o perfil.", "error");
    }
  }

  async function deleteAccount(account) {
    const confirmed = window.confirm(
      `Apagar todo o histórico de @${account.username} neste computador? O perfil do Chrome dessa conta também será removido. Nada muda no Instagram.`,
    );
    if (!confirmed) return;
    try {
      await api("/api/accounts/delete", {
        method: "POST",
        json: { account: account.owner_key },
      });
      if (accountKey === account.owner_key) {
        localStorage.removeItem(ACCOUNT_KEY);
        accountKey = "";
      }
      showToast(`Histórico de @${account.username} apagado.`, "success");
      await load();
    } catch (error) {
      showToast(error.message || "Não foi possível apagar o histórico.", "error");
    }
  }

  refs.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const windowDays = Number(refs.windowDays.value);
    const cooldown = Number(refs.cooldown.value);
    if (!Number.isInteger(windowDays) || windowDays < 1 || windowDays > 365) {
      showToast("A janela deve ser um número inteiro entre 1 e 365 dias.", "error");
      return;
    }
    if (!Number.isInteger(cooldown) || cooldown < 0 || cooldown > 1440) {
      showToast("O intervalo deve ser um número inteiro entre 0 e 1440 minutos.", "error");
      return;
    }
    const button = $("#settings-submit");
    button.disabled = true;
    try {
      const result = await api("/api/settings", {
        method: "POST",
        json: { window_days: windowDays, cooldown_minutes: cooldown },
      });
      refs.windowDays.value = String(result.window_days);
      refs.cooldown.value = String(result.cooldown_minutes);
      refs.saved.hidden = false;
      window.setTimeout(() => {
        refs.saved.hidden = true;
      }, 3000);
      showToast("Preferências salvas.", "success");
      await load();
    } catch (error) {
      showToast(error.message || "Não foi possível salvar as preferências.", "error");
    } finally {
      button.disabled = false;
    }
  });

  refs.addAccount.addEventListener("click", () => {
    refs.dialogForm.reset();
    openDialog(refs.dialog);
    window.setTimeout(() => refs.dialogUsername.focus(), 60);
  });

  refs.dialogForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = normalizeUsername(refs.dialogUsername.value);
    if (!validUsername(username)) {
      showToast("Informe um usuário válido, sem @ e sem link.", "error");
      return;
    }
    const button = refs.dialogForm.querySelector('button[type="submit"]');
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "Abrindo o Chrome…";
    try {
      await api("/api/collect", { method: "POST", json: { username } });
      closeDialog(refs.dialog);
      showToast("Coleta iniciada. Abrindo o painel…", "success");
      window.setTimeout(() => {
        window.location.href = "/";
      }, 700);
    } catch (error) {
      if (error.status === 409) {
        showToast("Já existe uma coleta em andamento. Acompanhe no painel.", "error");
      } else {
        showToast(error.message || "Não foi possível iniciar a coleta.", "error");
      }
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });

  refs.copyPath.addEventListener("click", async () => {
    const path = refs.dataDir.textContent || "";
    try {
      await navigator.clipboard.writeText(path);
      const original = refs.copyPath.textContent;
      refs.copyPath.textContent = "Copiado";
      window.setTimeout(() => {
        refs.copyPath.textContent = original;
      }, 1800);
    } catch {
      showToast("Não foi possível copiar o caminho. Selecione o texto manualmente.", "error");
    }
  });

  load();
}

/* ------------------------------------------------------------------ */
/* Inicialização                                                      */
/* ------------------------------------------------------------------ */

function init() {
  wireDialogs();
  const page = document.body.dataset.page;
  if (page === "dashboard") initDashboard();
  else if (page === "history") initHistory();
  else if (page === "settings") initSettings();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
