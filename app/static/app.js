const $ = (s) => document.querySelector(s);
const STAGES = ["Reading order", "Tracking items", "Checking rules", "Verdict"];

function stage(name) {
  const k = STAGES.indexOf(name);
  [...$("#progress").children].forEach((li, i) => { li.className = i < k ? "done" : i === k ? "now" : ""; });
}

async function show(kind, id) {
  const r = await fetch(`/api/${kind}/${id}/view`);
  if (!r.ok) throw new Error("Couldn't load the result.");
  $("#result").innerHTML = await r.text();
  $("#result").hidden = false;
  $("#evidence").href = `/api/${kind}/${id}/evidence`;
  $("#download").hidden = false;
  $("#result").scrollIntoView({ behavior: "smooth" });
}

function fail(e) {
  $("#error").textContent = e.message;
  $("#error").hidden = false;
}

$("#form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  for (const s of ["#error", "#result", "#download"]) $(s).hidden = true;
  const btn = ev.target.querySelector("button");
  btn.disabled = true;
  $("#progress").hidden = false;
  stage(STAGES[0]);
  try {
    const r = await fetch("/api/check", { method: "POST", body: new FormData(ev.target) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
    for (;;) {
      await new Promise((res) => setTimeout(res, 2000));
      const s = await (await fetch(`/api/job/${j.id}`)).json();
      stage(s.stage);
      if (s.error) throw new Error(s.error);
      if (s.done) { stage("Verdict"); await show("job", j.id); break; }
    }
  } catch (e) { fail(e); }
  btn.disabled = false;
});

fetch("/api/samples").then((r) => r.json()).then((list) => {
  if (!list.length) return;
  $("#sample-list").innerHTML = "";
  for (const s of list) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sample " + s.verdict.split(" ")[0].toLowerCase();
    b.textContent = `${s.id} · ${s.mode} · ${s.verdict}`;
    b.title = s.product;
    b.onclick = () => { $("#progress").hidden = true; $("#error").hidden = true; show("sample", s.id).catch(fail); };
    $("#sample-list").append(b);
  }
});
