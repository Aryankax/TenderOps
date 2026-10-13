"use client";

import { useState } from "react";
import { login, signup } from "../lib/api";

export default function AuthScreen({ onAuth }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const out = mode === "login"
        ? await login(username.trim(), password)
        : await signup(username.trim(), password);
      onAuth(out.user);
    } catch (err) {
      setError(String(err.message || err).replace(/^API \d+: /, ""));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-[80vh] max-w-md flex-col justify-center px-4">
      <div className="mb-6 text-center">
        <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-500 text-2xl font-black text-zinc-950">T</div>
        <h1 className="text-2xl font-bold tracking-tight">TenderOps</h1>
        <p className="mt-1 text-sm text-zinc-500">Agentic tender analysis &amp; bid preparation, end to end</p>
      </div>
      <div className="card">
        <div className="mb-4 grid grid-cols-2 gap-1 rounded-xl border border-white/10 bg-zinc-900/70 p-1">
          {(["login", "signup"]).map((m) => (
            <button key={m} onClick={() => { setMode(m); setError(""); }}
              className={`rounded-lg px-3 py-1.5 text-sm capitalize ${mode === m ? "bg-emerald-500 font-semibold text-zinc-950" : "text-zinc-400 hover:text-zinc-100"}`}>
              {m === "login" ? "Log in" : "Sign up"}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="mb-1 block text-sm text-zinc-400">Username</label>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)}
              placeholder="your-name" autoComplete="username" minLength={3} required />
          </div>
          <div>
            <label className="mb-1 block text-sm text-zinc-400">Password</label>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••" autoComplete={mode === "login" ? "current-password" : "new-password"}
              minLength={6} required />
          </div>
          {error && <div className="rounded-xl border border-red-500/40 p-2 text-sm text-red-200">⚠️ {error}</div>}
          <button className="btn-primary w-full" disabled={busy || !username.trim() || password.length < 6}>
            {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>
        <p className="mt-3 text-center text-xs text-zinc-500">
          {mode === "login"
            ? "New here? Switch to Sign up — then create your company workspace."
            : "Your companies, documents and runs are private to your account."}
        </p>
      </div>
    </div>
  );
}
