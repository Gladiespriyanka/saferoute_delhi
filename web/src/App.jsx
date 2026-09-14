import { Navigate, Route, Routes } from "react-router-dom";

import Dashboard from "./routes/Dashboard.jsx";
import Landing from "./routes/Landing.jsx";
import Planner from "./routes/Planner.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/plan" element={<Planner />} />
      <Route path="/routes" element={<Dashboard />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
