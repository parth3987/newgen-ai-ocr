import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";

import { atLeast, useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { Account } from "./pages/Account";
import { Admin } from "./pages/Admin";
import { DocumentDetail } from "./pages/DocumentDetail";
import { Documents } from "./pages/Documents";
import { Extractions } from "./pages/Extractions";
import { Login } from "./pages/Login";
import { ReviewItemPage } from "./pages/ReviewItem";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Upload } from "./pages/Upload";
import type { Role } from "./types";

function Guard({ role, children }: { role: Role; children: ReactNode }) {
  const { session } = useAuth();
  // The API enforces every rule; this only avoids showing pages that would be refused.
  if (!atLeast(session?.role, role)) {
    return <p className="alert warn">Your role ({session?.role}) cannot open this page.</p>;
  }
  return <>{children}</>;
}

export function App() {
  const { session, loading } = useAuth();
  if (loading) return <Loading what="Starting" />;
  if (!session) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="documents" replace />} />
        <Route path="documents" element={<Documents />} />
        <Route path="documents/:id" element={<DocumentDetail />} />
        <Route path="upload" element={<Upload />} />
        <Route path="extractions" element={<Extractions />} />
        <Route path="review" element={<Guard role="viewer"><ReviewQueue /></Guard>} />
        <Route path="review/:id/:run" element={<Guard role="viewer"><ReviewItemPage /></Guard>} />
        <Route path="admin" element={<Guard role="admin"><Admin /></Guard>} />
        <Route path="account" element={<Account />} />
        <Route path="*" element={<p className="alert warn">No such page.</p>} />
      </Route>
    </Routes>
  );
}

