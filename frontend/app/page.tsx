"use client";

import { useEffect, useState } from "react";

type Health = {
  status: string;
  database: boolean;
  llm: { provider: string; configured: boolean; model: string };
};

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    fetch(`${base}/api/health/`)
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="mx-auto max-w-xl p-10">
      <h1 className="text-3xl font-semibold">BizLens</h1>
      <p className="mt-1 text-sm text-gray-500">A decision engine for business data</p>
      <section className="mt-8 rounded-lg border p-4 text-sm">
        <h2 className="mb-2 font-medium">Backend status</h2>
        {error && <p className="text-red-600">Cannot reach backend: {error}</p>}
        {!health && !error && <p>Checking…</p>}
        {health && (
          <ul className="space-y-1">
            <li>API: {health.status}</li>
            <li>Database: {health.database ? "connected" : "down"}</li>
            <li>
              LLM: {health.llm.provider} / {health.llm.model} —{" "}
              {health.llm.configured ? "key configured" : "no key set"}
            </li>
          </ul>
        )}
      </section>
    </main>
  );
}
