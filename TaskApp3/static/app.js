// Minimal JS to auto-refresh tasks & handle submit button enablement
const userSelect = document.getElementById("userSelect");
const taskSelect = document.getElementById("taskSelect");
const statusSelect = document.getElementById("statusSelect");
const commentEl = document.getElementById("comment");
const submitBtn = document.getElementById("submitBtn");

const fUser = document.getElementById("f_user_id");
const fTask = document.getElementById("f_task_id");
const fStatus = document.getElementById("f_status");
const fComment = document.getElementById("f_comment");

async function loadTasksFor(userId) {
  if (!userId) {
    return;
  }
  const res = await fetch(`/api/user/${userId}/tasks`);
  const tasks = await res.json();
  taskSelect.innerHTML = `<option value="">— choose task —</option>`;
  for (const t of tasks) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = `[${t.project || "-"}] ${t.title}`;
    taskSelect.appendChild(opt);
  }
  taskSelect.disabled = tasks.length === 0;
}

function updateSubmitState() {
  const can = !!(userSelect?.value && taskSelect?.value && statusSelect?.value);
  submitBtn.disabled = !can;
  if (can) {
    fUser.value = userSelect.value;
    fTask.value = taskSelect.value;
    fStatus.value = statusSelect.value;
    fComment.value = commentEl.value || "";
  }
}

if (userSelect) {
  userSelect.addEventListener("change", async (e) => {
    await loadTasksFor(e.target.value);
    statusSelect.disabled = false;
    updateSubmitState();
  });
}
if (taskSelect) {
  taskSelect.addEventListener("change", updateSubmitState);
}
if (statusSelect) {
  statusSelect.addEventListener("change", updateSubmitState);
}
if (commentEl) {
  commentEl.addEventListener("input", () => {
    fComment.value = commentEl.value;
  });
}

// Polling every 45s so users immediately see admin-added tasks
setInterval(() => {
  if (userSelect && userSelect.value) {
    loadTasksFor(userSelect.value);
  }
}, 45000);

// --- Modern User page helpers ---

// Segmented buttons -> sync to hidden statusSelect
const seg = document.querySelector(".segmented");
if (seg && statusSelect) {
  seg.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      seg
        .querySelectorAll(".seg-btn")
        .forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      statusSelect.value = btn.dataset.status;
      updateSubmitState();
    });
  });
}

// Empty state when no tasks
const emptyTasks = document.getElementById("emptyTasks");
async function loadTasksFor(userId) {
  if (!userId) {
    taskSelect.innerHTML = `<option value="">— choose task —</option>`;
    taskSelect.disabled = true;
    if (emptyTasks) emptyTasks.style.display = "none";
    return;
  }
  const res = await fetch(`/api/user/${userId}/tasks`);
  const tasks = await res.json();
  taskSelect.innerHTML = `<option value="">— choose task —</option>`;
  for (const t of tasks) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = `[${t.project || "-"}] ${t.title}`;
    taskSelect.appendChild(opt);
  }
  taskSelect.disabled = tasks.length === 0;
  if (emptyTasks)
    emptyTasks.style.display = tasks.length === 0 ? "block" : "none";
}
