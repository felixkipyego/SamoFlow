import { useEffect, useState } from "react";
import { DashboardShell } from "@/screens/DashboardShell";
import { LoginScreen } from "@/screens/LoginScreen";
import { me, type DashboardSessionResponse } from "@/lib/api";

type AuthState =
  | { status: "loading" }
  | { status: "authenticated"; session: DashboardSessionResponse }
  | { status: "unauthenticated" };

function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    me()
      .then((session) => setAuth({ status: "authenticated", session }))
      .catch(() => setAuth({ status: "unauthenticated" }));
  }, []);

  if (auth.status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-muted-foreground">Loading…</p>
      </div>
    );
  }

  if (auth.status === "unauthenticated") {
    return (
      <LoginScreen onLoggedIn={(session) => setAuth({ status: "authenticated", session })} />
    );
  }

  return (
    <DashboardShell
      session={auth.session}
      onLoggedOut={() => setAuth({ status: "unauthenticated" })}
    />
  );
}

export default App;
