import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Lock } from "lucide-react";

export const Route = createFileRoute("/datacenter-login")({
  component: DatacenterLogin,
});

function DatacenterLogin() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();

    if (password === "password123") {
      try {
        // Call signin API with datacenter.client account
        const response = await fetch("/api/v1/signin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: "datacenter.client@gmail.com",
            password: "DataCenter123!@",
          }),
        });

        if (response.ok) {
          const data = await response.json();
          // Store token and org info
          localStorage.setItem("multiai_token", data.access_token);
          localStorage.setItem("multiai_org_id", data.organization.id);
          localStorage.setItem("multiai_org_slug", data.organization.slug);
          localStorage.setItem("multiai_org_path", "/datacenter");
          // Redirect to chat
          navigate({ to: "/chat" });
        } else {
          setError("Login failed. Please try again.");
        }
      } catch (err) {
        setError("Connection error. Please try again.");
      }
    } else {
      setError("Incorrect password");
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 to-slate-800">
      <div className="w-full max-w-md rounded-lg bg-white p-8 shadow-xl">
        <div className="mb-6 flex justify-center">
          <div className="rounded-full bg-blue-100 p-4">
            <Lock className="h-8 w-8 text-blue-600" />
          </div>
        </div>

        <h1 className="mb-2 text-center text-2xl font-bold text-gray-900">
          Datacenter Access
        </h1>
        <p className="mb-6 text-center text-gray-600">
          Enter the access password to continue
        </p>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <input
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setError("");
              }}
              placeholder="Enter password"
              className="w-full rounded-lg border border-gray-300 px-4 py-2 focus:border-blue-500 focus:outline-none"
              autoFocus
            />
          </div>

          {error && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-600">
              {error}
            </div>
          )}

          <button
            type="submit"
            className="w-full rounded-lg bg-blue-600 py-2 text-white font-medium hover:bg-blue-700 transition"
          >
            Access Datacenter
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-gray-500">
          Datacenter Client Portal
        </p>
      </div>
    </div>
  );
}
