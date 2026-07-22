import { createFileRoute, Outlet } from "@tanstack/react-router";

export const Route = createFileRoute("/scraping/$missionId/runs")({
  component: () => <Outlet />,
});
