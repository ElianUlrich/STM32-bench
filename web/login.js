// Pantalla de ingreso.
"use strict";

document.getElementById("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = document.getElementById("login-error");
  error.hidden = true;
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Bench": "1" },
    body: JSON.stringify({
      username: document.getElementById("username").value,
      password: document.getElementById("password").value,
    }),
  });
  if (resp.ok) {
    location.href = "/";
    return;
  }
  let detail = "No se pudo ingresar";
  try { detail = (await resp.json()).detail || detail; } catch (e) { /* sin cuerpo JSON */ }
  error.textContent = detail;
  error.hidden = false;
});
