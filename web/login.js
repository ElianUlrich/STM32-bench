// Pantalla de ingreso.
"use strict";

document.getElementById("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = document.getElementById("login-error");
  const submit = document.getElementById("login-submit");
  error.hidden = true;
  // En la placa el login tarda 2-3 s (PBKDF2): el botón muestra que está trabajando.
  submit.disabled = true;
  submit.classList.add("is-busy");
  submit.setAttribute("aria-busy", "true");
  try {
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
  } catch (e) {
    error.textContent = "No hay conexión con el banco. ¿Está Tailscale activo?";
    error.hidden = false;
  } finally {
    submit.disabled = false;
    submit.classList.remove("is-busy");
    submit.removeAttribute("aria-busy");
  }
});
